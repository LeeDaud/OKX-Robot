"""
持仓价格刷新 API — 通过 OKX DEX 获取代币当前价格及未实现盈亏。
"""
import os
import logging
from fastapi import APIRouter
from src.db.database import get_open_positions

logger = logging.getLogger(__name__)
router = APIRouter(tags=["positions"])

USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

KNOWN_TOKENS: dict[str, dict] = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": {"decimals": 6,  "symbol": "USDC"},
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": {"decimals": 6,  "symbol": "USDT"},
    "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b": {"decimals": 18, "symbol": "VIRTUAL"},
    "0x4200000000000000000000000000000000000006": {"decimals": 18, "symbol": "WETH"},
}

_info_cache: dict[str, dict] = {}

async def _get_token_info(token_addr: str) -> dict:
    """获取代币精度和符号：已知映射 → 链上查询 → 默认。"""
    key = token_addr.lower()
    if key in _info_cache:
        return _info_cache[key]
    if key in KNOWN_TOKENS:
        _info_cache[key] = KNOWN_TOKENS[key]
        return KNOWN_TOKENS[key]

    try:
        from web3 import Web3
        rpc = os.environ.get("RPC_HTTP_URL", "https://mainnet.base.org")
        w3 = Web3(Web3.HTTPProvider(rpc))
        abi = ('[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},'
               '{"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}]')
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=abi)
        decimals = contract.functions.decimals().call()
        symbol = contract.functions.symbol().call()
        info = {"decimals": decimals, "symbol": symbol}
        _info_cache[key] = info
        logger.info("Resolved %s → decimals=%d symbol=%s", token_addr, decimals, symbol)
        return info
    except Exception as e:
        logger.warning("Failed to get token info for %s: %s, defaulting", token_addr, e)
        info = {"decimals": 18, "symbol": None}
        _info_cache[key] = info
        return info


def _raw_to_human(raw: str | int | float, decimals: int) -> float:
    return int(float(raw)) / 10 ** decimals


async def _fetch_price(okx: "OKXDexClient", token_addr: str, sell_raw: int) -> float | None:
    quote = await okx.get_quote(token_addr, USDC_BASE, sell_raw)
    if not quote:
        return None
    to_amount = int(quote.get("toTokenAmount", 0))
    return to_amount / 10 ** 6 / (sell_raw / 10 ** 18)


@router.post("/positions/refresh-prices")
async def refresh_prices():
    """刷新所有持仓的当前价格及未实现盈亏。"""
    open_positions = await get_open_positions()
    if not open_positions:
        return {"tokens": {}, "positions": {}}

    unique_tokens: dict[str, int] = {}
    for pos in open_positions:
        token = (pos.get("token_out") or "").lower()
        if not token or token == USDC_BASE:
            continue
        raw = pos.get("filled_amount") or str(int(pos.get("amount_out", 0)))
        if token not in unique_tokens or int(raw) > unique_tokens[token]:
            unique_tokens[token] = int(raw)

    if not unique_tokens:
        return {"tokens": {}, "positions": {}}

    from src.executor.okx_client import OKXDexClient
    import aiohttp

    api_key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")

    if not api_key or not secret or not passphrase:
        return {"tokens": {}, "positions": {}, "error": "OKX API 未配置"}

    token_data: dict[str, dict] = {}

    async with aiohttp.ClientSession() as session:
        okx = OKXDexClient(api_key, secret, passphrase)
        okx._session = session

        for token_addr, sell_raw in unique_tokens.items():
            info = await _get_token_info(token_addr)
            price = await _fetch_price(okx, token_addr, sell_raw)
            entry = {"symbol": info["symbol"], "decimals": info["decimals"]}
            if price is not None:
                entry["current_price"] = round(price, 12)
            else:
                entry["current_price"] = None
            token_data[token_addr] = entry

    positions_data: dict[str, dict] = {}
    for pos in open_positions:
        pid = str(pos["id"])
        token = (pos.get("token_out") or "").lower()
        info = token_data.get(token, {})
        decimals = info.get("decimals", 18)
        current_price = info.get("current_price")

        raw_amount = pos.get("filled_amount") or str(int(pos.get("amount_out", 0)))
        human_amount = _raw_to_human(raw_amount, decimals)
        entry_price_raw = pos.get("entry_price") or 0
        cost_basis = pos.get("filled_cost_usd") or 0
        if cost_basis == 0 and entry_price_raw:
            cost_basis = entry_price_raw * 10 ** decimals * human_amount

        entry: dict = {
            "amount": round(human_amount, 4),
            "cost_basis_usd": round(cost_basis, 2),
        }

        if current_price is not None:
            current_value = current_price * human_amount
            pnl = current_value - cost_basis
            entry["current_price"] = round(current_price, 12)
            entry["current_value_usd"] = round(current_value, 2)
            entry["unrealized_pnl"] = round(pnl, 2)
            entry["roi_pct"] = round((pnl / cost_basis * 100) if cost_basis > 0 else 0, 2)
        else:
            entry["current_price"] = None
            entry["current_value_usd"] = None
            entry["unrealized_pnl"] = None
            entry["roi_pct"] = None

        positions_data[pid] = entry

    return {"tokens": token_data, "positions": positions_data}
