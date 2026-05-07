"""
SynFutures V3 链上永续合约客户端。

与 Gate / Vault / Observer / Instrument 合约交互，提供存款、交易、杠杆设置、
仓位查询等功能，不依赖 CEX。
"""
import asyncio
import logging
from typing import Optional

from web3 import AsyncWeb3
from eth_account import Account

logger = logging.getLogger(__name__)

# ── Base 链地址 ─────────────────────────────────────────────────────

USDC_BASE = "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913"
USDC_DECIMALS = 6

SYNFUTURES = {
    "gate":     "0x208B443983D8BcC8578e9D86Db23FbA547071270",
    "observer": "0xDb166a6E454d2a273Cd50CCD6420703564B2a830",
    "config":   "0xB63902d38738e353f3f52AdD203C418A0bFEa172",
}

NULL_DDL = 2 ** 32 - 1           # uint32 max — perpetual never settles
PERP_EXPIRY = NULL_DDL           # 永续合约 expiry
REFERRAL_CODE = bytes([0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
DEFAULT_DEADLINE_SEC = 300       # 默认交易 deadline（秒）

# ── 最小 ABI（仅用到的函数） ────────────────────────────────────────

GATE_ABI = [
    {"name": "deposit",   "type": "function", "inputs": [{"name": "arg", "type": "bytes32"}], "outputs": [], "stateMutability": "nonpayable"},
    {"name": "withdraw",  "type": "function", "inputs": [{"name": "arg", "type": "bytes32"}], "outputs": [], "stateMutability": "nonpayable"},
    {"name": "getAllInstruments", "type": "function", "inputs": [], "outputs": [{"name": "", "type": "address[]"}], "stateMutability": "view"},
    {"name": "config",    "type": "function", "inputs": [], "outputs": [{"name": "", "type": "address"}], "stateMutability": "view"},
]

INSTRUMENT_ABI = [
    {"name": "trade",       "type": "function", "inputs": [{"name": "args", "type": "bytes32[2]"}], "outputs": [{"name": "", "type": "tuple"}], "stateMutability": "nonpayable"},
    {"name": "setLeverage", "type": "function", "inputs": [{"name": "leverage", "type": "uint8"}], "outputs": [], "stateMutability": "nonpayable"},
    {"name": "inquire",     "type": "function", "inputs": [{"name": "expiry", "type": "uint32"}, {"name": "size", "type": "int256"}], "outputs": [{"name": "", "type": "tuple"}], "stateMutability": "view"},
    {"name": "getExpiries", "type": "function", "inputs": [], "outputs": [{"name": "", "type": "uint32[]"}], "stateMutability": "view"},
]

OBSERVER_ABI = [
    {"name": "getPosition", "type": "function", "inputs": [{"name": "instrument", "type": "address"}, {"name": "expiry", "type": "uint32"}, {"name": "target", "type": "address"}], "outputs": [{"name": "", "type": "tuple"}], "stateMutability": "view"},
    {"name": "getSetting",  "type": "function", "inputs": [{"name": "instrument", "type": "address"}], "outputs": [{"name": "", "type": "tuple"}], "stateMutability": "view"},
    {"name": "getVaultBalances", "type": "function", "inputs": [{"name": "target", "type": "address"}, {"name": "quotes", "type": "address[]"}], "outputs": [{"name": "balances", "type": "uint256[]"}, {"name": "blockInfo", "type": "tuple"}], "stateMutability": "view"},
    {"name": "getAllInstruments", "type": "function", "inputs": [], "outputs": [{"name": "instruments", "type": "tuple[]"}, {"name": "blockInfo", "type": "tuple"}], "stateMutability": "view"},
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"name": "approve",   "type": "function", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
    {"name": "decimals",  "type": "function", "inputs": [], "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view"},
]

MAX_UINT256 = 2 ** 256 - 1
CHAIN_ID = 8453


# ── 参数编码（与 SDK 完全一致） ─────────────────────────────────────

def _encode_trade(expiry: int, size: int, amount: int,
                  limit_tick: int = 0, deadline: int = 0) -> list[str]:
    """Instrument.trade(bytes32[2]) 参数编码。

    Args:
        expiry: 到期时间（uint32），永续用 NULL_DDL
        size:   仓位规模（int128），正=做多，负=做空
        amount: 质押品数量（uint128，quote token raw units）
        limit_tick: 限制 tick（滑点保护），0=不限
        deadline: 截止时间戳（uint32），0=自动计算

    Returns:
        [page0_hex, page1_hex]
    """
    if deadline == 0:
        deadline = int(asyncio.get_event_loop().time()) + DEFAULT_DEADLINE_SEC
        deadline &= 0xFFFFFFFF

    # size 作为 int128：负值转补码
    usize = size if size >= 0 else size + (1 << 128)

    # page0: (deadline << 56) | (limit_tick << 32) | expiry
    page0_val = (deadline << 56) | ((limit_tick & 0xFFFFFF) << 32) | (expiry & 0xFFFFFFFF)
    # page1: (|size| << 128) | amount
    page1_val = (usize << 128) | amount

    return [hex(page0_val), hex(page1_val)]


def _encode_deposit(token_addr: str, quantity: int) -> str:
    """Gate.deposit(bytes32) 参数编码。

     (quantity << 160) | token_address
    """
    return hex((quantity << 160) | int(token_addr, 16))


def _sqrt_price_to_price(sqrt_price_x96: int, decimals_diff: int = 0) -> float:
    """将 sqrtFairPX96 转换为 USDC 价格。

    sqrtPriceX96 = sqrt(price) * 2^96
    price = (sqrtPriceX96 / 2^96)^2 * 10^(quoteDecimals - baseDecimals)
    """
    sqrt = sqrt_price_x96 / (2 ** 96)
    price = sqrt * sqrt
    if decimals_diff:
        price *= 10 ** decimals_diff
    return price


def _tick_to_price(tick: int, decimals_diff: int = 0) -> float:
    """将 tick 转换为 USDC 价格。"""
    price = 1.0001 ** tick
    if decimals_diff:
        price *= 10 ** decimals_diff
    return price


# ── SynFutures 客户端 ──────────────────────────────────────────────

class SynFuturesClient:
    """SynFutures V3 链上交互客户端。

    使用 executor 钱包的私钥签名并发送交易。
    """

    def __init__(
        self,
        w3: AsyncWeb3,
        private_key: str,
        wallet_address: str,
        dry_run: bool = True,
    ) -> None:
        self._w3 = w3
        self._pk = private_key
        self._wallet = AsyncWeb3.to_checksum_address(wallet_address)
        self._dry_run = dry_run

        self._gate = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(SYNFUTURES["gate"]),
            abi=GATE_ABI,
        )
        self._observer = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(SYNFUTURES["observer"]),
            abi=OBSERVER_ABI,
        )
        # 缓存 {symbol: instrument_info}
        self._instruments: dict[str, dict] = {}

    # ── 查询 ────────────────────────────────────────────────────────

    async def discover_instruments(self) -> dict[str, dict]:
        """扫描所有上线 instrument 并建立 symbol→info 映射。"""
        try:
            instr_addrs = await self._gate.functions.getAllInstruments().call()
        except Exception as e:
            logger.warning("[SF] getAllInstruments failed: %s", e)
            return {}

        result: dict[str, dict] = {}
        for addr_hex in instr_addrs:
            addr = addr_hex.lower()
            try:
                raw = await self._observer.functions.getSetting(
                    AsyncWeb3.to_checksum_address(addr)
                ).call()
            except Exception:
                continue

            # getSetting → [SettingStruct] or SettingStruct directly
            s = raw[0] if isinstance(raw, (list, tuple)) and len(raw) == 1 else raw

            # SettingStruct: [symbol, config, gate, market, quote, decimals, imr, mmr, param]
            if isinstance(s, (list, tuple)):
                base_symbol = str(s[0])
                quote_addr = str(s[4]).lower()
            else:
                base_symbol = str(s.symbol)
                quote_addr = str(s.quote).lower()

            if quote_addr != USDC_BASE.lower():
                continue  # 目前只支持 USDC 质押品

            symbol = f"{base_symbol}/USDC"
            result[symbol] = {
                "address": addr,
                "symbol": symbol,
                "quote": quote_addr,
            }
            logger.info("[SF] Discovered instrument: %s @ %s", symbol, addr)

        self._instruments = result
        return result

    async def resolve_instrument(self, pair: str) -> Optional[dict]:
        """将交易对名称解析为 instrument 信息。"""
        # 标准化 pair 格式: "BTC-USDT-SWAP" → "BTC/USDC"
        normalized = pair.upper().replace("-USDT-SWAP", "/USDC").replace("-USD-SWAP", "/USDC")

        if normalized in self._instruments:
            return self._instruments[normalized]

        # 尝试再扫描一次
        await self.discover_instruments()
        return self._instruments.get(normalized)

    async def get_expiries(self, instrument_addr: str) -> list[int]:
        """获取 instrument 的活跃到期列表。"""
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        try:
            c = self._w3.eth.contract(address=addr, abi=INSTRUMENT_ABI)
            return await c.functions.getExpiries().call()
        except Exception:
            return [PERP_EXPIRY]

    async def get_mark_price(self, instrument_addr: str,
                             expiry: int = PERP_EXPIRY) -> Optional[float]:
        """获取当前标记价格（USDC per Base Token）。"""
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        try:
            c = self._w3.eth.contract(address=addr, abi=INSTRUMENT_ABI)
            quote = await c.functions.inquire(expiry, 0).call()
            # quote = QuotationStruct: [benchmark, sqrtFairPX96, tick, mark, entryNotional, ...]
            sqrt_px96 = quote[1] if isinstance(quote, (list, tuple)) else quote.sqrtFairPX96
            return _sqrt_price_to_price(sqrt_px96)
        except Exception as e:
            logger.warning("[SF] get_mark_price failed: %s", e)
            return None

    async def get_current_tick(self, instrument_addr: str,
                               expiry: int = PERP_EXPIRY) -> Optional[int]:
        """获取当前 tick。"""
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        try:
            c = self._w3.eth.contract(address=addr, abi=INSTRUMENT_ABI)
            quote = await c.functions.inquire(expiry, 0).call()
            return int(quote[2]) if isinstance(quote, (list, tuple)) else int(quote.tick)
        except Exception:
            return None

    async def get_position(self, instrument_addr: str,
                           expiry: int = PERP_EXPIRY) -> Optional[dict]:
        """获取当前钱包在该 instrument 上的持仓。

        Returns:
            {size, balance, entryNotional, entrySocialLossIndex, entryFundingIndex}
            或 None（无持仓 / 查询失败）。
        """
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        try:
            pos = await self._observer.functions.getPosition(
                addr, expiry, self._wallet
            ).call()
            # pos = PositionStruct: [balance, size, entryNotional, ...]
            data = pos[0] if isinstance(pos, (list, tuple)) and len(pos) > 1 else pos
            size_raw = int(data[1] if isinstance(data, (list, tuple)) else data.size)
            return {
                "balance": int(data[0] if isinstance(data, (list, tuple)) else data.balance),
                "size": size_raw,  # int256: 正=多, 负=空
                "entry_notional": int(data[2] if isinstance(data, (list, tuple)) else data.entryNotional),
                "entry_social_loss_index": int(data[3] if isinstance(data, (list, tuple)) else data.entrySocialLossIndex),
                "entry_funding_index": int(data[4] if isinstance(data, (list, tuple)) else data.entryFundingIndex),
            }
        except Exception as e:
            logger.warning("[SF] get_position failed: %s", e)
            return None

    async def get_vault_balance(self, quote_addr: str = USDC_BASE) -> Optional[int]:
        """获取 Vault 中该钱包的质押品余额（raw units）。"""
        addr = AsyncWeb3.to_checksum_address(quote_addr)
        try:
            result = await self._observer.functions.getVaultBalances(
                self._wallet, [addr]
            ).call()
            balances = result[0] if isinstance(result, (list, tuple)) else result.balances
            return int(balances[0])
        except Exception as e:
            logger.warning("[SF] get_vault_balance failed: %s", e)
            return None

    async def get_usdc_balance(self) -> Optional[int]:
        """获取钱包 USDC 链上余额（raw units）。"""
        addr = AsyncWeb3.to_checksum_address(USDC_BASE)
        try:
            c = self._w3.eth.contract(address=addr, abi=ERC20_ABI)
            return await c.functions.balanceOf(self._wallet).call()
        except Exception:
            return None

    async def get_usdc_allowance(self, spender: str) -> Optional[int]:
        """查询 USDC 授权额度。"""
        spender_cs = AsyncWeb3.to_checksum_address(spender)
        addr = AsyncWeb3.to_checksum_address(USDC_BASE)
        try:
            c = self._w3.eth.contract(address=addr, abi=ERC20_ABI)
            return await c.functions.allowance(self._wallet, spender_cs).call()
        except Exception:
            return None

    # ── 交易发送 ────────────────────────────────────────────────────

    async def _send_tx(self, tx: dict) -> Optional[str]:
        """签名并广播交易（或 dry-run 跳过）。"""
        if self._dry_run:
            logger.info("[SF DRY-RUN] would send tx: to=%s data=%s...",
                        tx.get("to", "")[:12], tx.get("data", "")[:20])
            return None

        tx["chainId"] = CHAIN_ID
        tx["from"] = self._wallet
        tx["nonce"] = await self._w3.eth.get_transaction_count(self._wallet)

        # 估算 gas（如果没指定）
        if "gas" not in tx or not tx["gas"]:
            try:
                tx["gas"] = await self._w3.eth.estimate_gas(tx)
            except Exception:
                tx["gas"] = 300000  # fallback

        # 设置 gas price
        if "maxPriorityFeePerGas" not in tx:
            try:
                base_fee = await self._w3.eth.gas_price
                tx["maxPriorityFeePerGas"] = base_fee // 10
                tx["maxFeePerGas"] = base_fee + tx["maxPriorityFeePerGas"]
                tx["type"] = 2
            except Exception:
                pass

        signed = Account.sign_transaction(tx, self._pk)
        raw = signed.raw_transaction
        tx_hash = await self._w3.eth.send_raw_transaction(raw)
        tx_hash_hex = tx_hash.hex()
        logger.info("[SF] tx sent: %s", tx_hash_hex[:20])
        return tx_hash_hex

    # ── 交易操作 ────────────────────────────────────────────────────

    async def approve_usdc(self, spender: str, amount: int = MAX_UINT256) -> Optional[str]:
        """批准 USDC 额度。"""
        spender_cs = AsyncWeb3.to_checksum_address(spender)
        addr = AsyncWeb3.to_checksum_address(USDC_BASE)
        c = self._w3.eth.contract(address=addr, abi=ERC20_ABI)
        tx = await c.functions.approve(spender_cs, amount).build_transaction({
            "from": self._wallet,
        })
        return await self._send_tx(tx)

    async def deposit(self, amount: int) -> Optional[str]:
        """存入 USDC 到 Vault（通过 Gate）。

        Args:
            amount: USDC 数量（raw，6 decimals）

        Returns:
            tx_hash or None
        """
        encoded = _encode_deposit(USDC_BASE, amount)
        tx = await self._gate.functions.deposit(encoded).build_transaction({
            "from": self._wallet,
        })
        return await self._send_tx(tx)

    async def withdraw(self, amount: int) -> Optional[str]:
        """从 Vault 提取 USDC。

        Args:
            amount: 提取数量（raw，6 decimals）

        Returns:
            tx_hash or None
        """
        encoded = _encode_deposit(USDC_BASE, amount)
        tx = await self._gate.functions.withdraw(encoded).build_transaction({
            "from": self._wallet,
        })
        return await self._send_tx(tx)

    async def set_leverage(self, instrument_addr: str, leverage: int) -> Optional[str]:
        """设置 instrument 杠杆倍数。"""
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        c = self._w3.eth.contract(address=addr, abi=INSTRUMENT_ABI)
        tx = await c.functions.setLeverage(leverage).build_transaction({
            "from": self._wallet,
        })
        return await self._send_tx(tx)

    async def trade(
        self,
        instrument_addr: str,
        size: int,
        amount: int,
        expiry: int = PERP_EXPIRY,
        limit_tick: int = 0,
        deadline: int = 0,
    ) -> Optional[str]:
        """执行交易（开仓 / 平仓 / 调仓）。

        Args:
            instrument_addr:  instrument 合约地址
            size:             目标仓位大小（base token raw units × sign）
            amount:           投入保证金（quote token raw units）
            expiry:           expiry
            limit_tick:       限制 tick（滑点保护）, 0=市价
            deadline:         截止时间

        Returns:
            tx_hash or None
        """
        params = _encode_trade(expiry, size, amount, limit_tick, deadline)
        addr = AsyncWeb3.to_checksum_address(instrument_addr)
        c = self._w3.eth.contract(address=addr, abi=INSTRUMENT_ABI)
        tx = await c.functions.trade(params).build_transaction({
            "from": self._wallet,
        })
        return await self._send_tx(tx)
