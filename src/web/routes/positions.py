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

# Well-known token decimals on Base chain
KNOWN_DECIMALS: dict[str, int] = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": 6,   # USDT
    "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b": 18,  # VIRTUAL
    "0x4200000000000000000000000000000000000006": 18,  # WETH
}

_decimals_cache: dict[str, int] = {}

async def _get_decimals(token_addr: str) -> int:
    """获取代币精度：已知映射 → 链上查询 → 默认 18。"""
    key = token_addr.lower()
    if key in _decimals_cache:
        return _decimals_cache[key]
    if key in KNOWN_DECIMALS:
        _decimals_cache[key] = KNOWN_DECIMALS[key]
        return KNOWN_DECIMALS[key]

    # 链上查询
    try:
        from web3 import Web3
        rpc = os.environ.get("RPC_HTTP_URL", "https://mainnet.base.org")
        w3 = Web3(Web3.HTTPProvider(rpc))
        abi = '[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}]'
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=abi)
        dec = contract.functions.decimals().call()
        _decimals_cache[key] = dec
        logger.info("Resolved decimals for %s → %d", token_addr, dec)
        return dec
    except Exception as e:
        logger.warning("Failed to get decimals for %s: %s, defaulting to 18", token_addr, e)
        _decimals_cache[key] = 18
        return 18


def _raw_to_human(raw: str | int | float, decimals: int) -> float:
    """将原始数量转换为人类可读数量。"""
    return int(float(raw)) / 10 ** decimals


async def _fetch_prices_for_token(
    okx: "OKXDexClient", token_addr: str, sell_amount_raw: int
) -> float | None:
    """通过 OKX DEX 获取代币当前 USD 价格。"""
    quote = await okx.get_quote(token_addr, USDC_BASE, sell_amount_raw)
    if not quote:
        return None
    to_amount = int(quote.get("toTokenAmount", 0))  # USDC raw (6 decimals)
    return to_amount / 10 ** 6 / (sell_amount_raw / 10 ** 18)


@router.post("/positions/refresh-prices")
async def refresh_prices():
    """刷新所有持仓的当前价格及未实现盈亏。"""
    open_positions = await get_open_positions()
    if not open_positions:
        return {"prices": {}, "positions": {}}

    # 收集唯一需要查询价格的代币
    unique_tokens: dict[str, int] = {}  # token_addr → raw amount to sell for quoting
    for pos in open_positions:
        token = (pos.get("token_out") or "").lower()
        if not token or token == USDC_BASE:
            continue
        # 使用实际持仓量作为报价金额
        raw = pos.get("filled_amount") or str(int(pos.get("amount_out", 0)))
        if token not in unique_tokens or int(raw) > unique_tokens.get(token, 0):
            unique_tokens[token] = int(raw)

    if not unique_tokens:
        return {"prices": {}, "positions": {}}

    # 初始化 OKX client
    from src.executor.okx_client import OKXDexClient
    import aiohttp

    api_key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")

    if not api_key or not secret or not passphrase:
        return {"prices": {}, "positions": {}, "error": "OKX API 未配置"}

    token_prices: dict[str, dict] = {}

    async with aiohttp.ClientSession() as session:
        okx = OKXDexClient(api_key, secret, passphrase)
        okx._session = session

        for token_addr, sell_raw in unique_tokens.items():
            decimals = await _get_decimals(token_addr)
            price = await _fetch_prices_for_token(okx, token_addr, sell_raw)
            if price is not None:
                token_prices[token_addr] = {
                    "current_price": round(price, 12),
                    "decimals": decimals,
                }
            else:
                token_prices[token_addr] = {
                    "current_price": None,
                    "decimals": decimals,
                }

    # 计算每个持仓的未实现盈亏
    positions_data: dict[str, dict] = {}
    for pos in open_positions:
        pid = str(pos["id"])
        token = (pos.get("token_out") or "").lower()
        decimals = token_prices.get(token, {}).get("decimals", 18)
        current_price = token_prices.get(token, {}).get("current_price")

        raw_amount = pos.get("filled_amount") or str(int(pos.get("amount_out", 0)))
        human_amount = _raw_to_human(raw_amount, decimals)

        entry_price_raw = pos.get("entry_price") or 0
        cost_basis = pos.get("filled_cost_usd") or 0
        if cost_basis == 0 and entry_price_raw:
            cost_basis = entry_price_raw * 10 ** decimals * human_amount

        entry = {
            "amount": round(human_amount, 4),
            "cost_basis_usd": round(cost_basis, 2),
        }

        if current_price is not None:
            current_value = current_price * human_amount
            unrealized_pnl = current_value - cost_basis
            entry["current_price"] = round(current_price, 12)
            entry["current_value_usd"] = round(current_value, 2)
            entry["unrealized_pnl"] = round(unrealized_pnl, 2)
            entry["roi_pct"] = round((unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0, 2)
        else:
            entry["current_price"] = None
            entry["current_value_usd"] = None
            entry["unrealized_pnl"] = None
            entry["roi_pct"] = None

        positions_data[pid] = entry

    return {"prices": token_prices, "positions": positions_data}
