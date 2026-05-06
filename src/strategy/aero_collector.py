"""
AERO 市场数据采集器。

数据来源组合：
- 实时价格: OKX get_quote（复用已有模块）
- 价格滑动窗口: 内存 deque，滚动存储
- 买卖方向/成交量: 链上池子 Swap 事件（复用 eth_getLogs 模式）
- 流动性: 池子 reserves + OKX priceImpact
"""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from web3 import Web3

from src.executor.okx_client import OKXDexClient
from src.rpc.router import RPCRouter

logger = logging.getLogger(__name__)

# Base 链 Aerodrome 池子常量
AERO_ADDRESS = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
USDC_ADDRESS = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
AERO_USDC_POOL = Web3.to_checksum_address("0xcddac48af89589052ff14a3cacf58596fe7e2be2")

# keccak256("Swap(address,uint256,uint256,uint256,uint256,address)")
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

POOL_RESERVE_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint256"},
            {"name": "_reserve1", "type": "uint256"},
        ],
        "type": "function",
        "stateMutability": "view",
    },
]


@dataclass
class SwapEvent:
    """Aerodrome 池子的 Swap 事件解析结果。"""
    tx_hash: str
    timestamp: datetime
    amount0_in: int   # AERO into pool (sell)
    amount1_in: int   # USDC into pool (buy)
    amount0_out: int  # AERO out of pool (buy)
    amount1_out: int  # USDC out of pool (sell)


@dataclass
class MarketSnapshot:
    """一次完整的市场快照，包含所有策略判断所需指标。"""
    timestamp: datetime
    price: float
    return_5m: float
    return_15m: float
    return_30m: float
    return_1h: float
    vwap: float
    ma_20m: float
    volume_5m: float
    avg_volume_5m_1h: float
    volume_ratio: float
    buy_volume_5m: float
    sell_volume_5m: float
    buy_pressure: float
    pool_liquidity_usd: float
    simulated_buy_slippage: float
    simulated_sell_slippage: float
    price_above_open_1h: bool
    pullback_from_high: float
    price_breakout_1h: bool
    recent_high_1h: float
    open_1h: float
    # 附加日志用
    price_count: int = 0
    swap_count: int = 0


class AeroMarketCollector:
    """采集 AERO/USDC 市场数据，生成 MarketSnapshot。

    复用:
      - RPCRouter → 链上 RPC 请求（eth_getLogs, contract call）
      - OKXDexClient → 实时价格 + 报价滑点
      - BuybackMonitor 的 fromBlock 追踪模式 → Swap 事件
    """

    def __init__(
        self,
        w3: RPCRouter,
        okx: OKXDexClient,
        pool_addr: str = AERO_USDC_POOL,
        aero_addr: str = AERO_ADDRESS,
        usdc_addr: str = USDC_ADDRESS,
    ):
        self._w3 = w3
        self._okx = okx
        self._pool = Web3.to_checksum_address(pool_addr)
        self._aero = Web3.to_checksum_address(aero_addr)
        self._usdc = Web3.to_checksum_address(usdc_addr)

        # 滚动价格窗口: (timestamp, price) — 最长 1h
        self._price_buffer: deque[tuple[datetime, float]] = deque(maxlen=3600)

        # Swap 事件缓冲 — 最近 30min ~ 1h 的事件
        self._swap_buffer: deque[SwapEvent] = deque(maxlen=2000)

        # 追踪最后处理的区块（复用 BuybackMonitor 的 fromBlock 模式）
        self._last_swap_block = 0

        # VWAP 累计量
        self._vwap_price_sum = 0.0  # Σ(price × volume)
        self._vwap_vol_sum = 0.0    # Σ(volume)

        # 整小时开盘价
        self._hour_open_price: Optional[float] = None
        self._current_hour_key: Optional[datetime] = None

    # ── 公开 API ─────────────────────────────────────────────────

    async def collect(self) -> Optional[MarketSnapshot]:
        """采集一次完整数据快照。

        如果价格获取失败且缓冲区为空，返回 None。
        """
        # 1. 实时价格
        price = await self._fetch_price()
        if price is None:
            if not self._price_buffer:
                return None
            price = self._price_buffer[-1][1]
            logger.debug("使用缓存价格: %.6f", price)

        self._record_price(price)

        # 2. Swap 事件
        swaps = await self._fetch_swap_events()
        self._record_swaps(swaps)

        # 3. 链上流动性
        liquidity = await self._fetch_liquidity()

        # 4. 模拟滑点
        buy_slip, sell_slip = await self._simulate_slippage()

        # 5. 计算指标
        returns = self._compute_returns()
        ma_20m = self._compute_ma(20)
        vwap = self._compute_vwap()
        vol_5m, avg_vol_1h, vol_ratio = self._compute_volume_stats(5)
        buy_vol, sell_vol, buy_press = self._compute_buy_sell_pressure(5)
        breakout = self._check_breakout_1h()
        pullback = self._compute_pullback()
        recent_high = self._get_recent_high_1h()

        open_1h = self._hour_open_price or price
        price_above_open = price > open_1h if open_1h > 0 else False

        return MarketSnapshot(
            timestamp=datetime.now(timezone.utc),
            price=price,
            return_5m=returns["5m"],
            return_15m=returns["15m"],
            return_30m=returns["30m"],
            return_1h=returns["1h"],
            vwap=vwap,
            ma_20m=ma_20m,
            volume_5m=vol_5m,
            avg_volume_5m_1h=avg_vol_1h,
            volume_ratio=vol_ratio,
            buy_volume_5m=buy_vol,
            sell_volume_5m=sell_vol,
            buy_pressure=buy_press,
            pool_liquidity_usd=liquidity,
            simulated_buy_slippage=buy_slip,
            simulated_sell_slippage=sell_slip,
            price_above_open_1h=price_above_open,
            pullback_from_high=pullback,
            price_breakout_1h=breakout,
            recent_high_1h=recent_high,
            open_1h=open_1h,
            price_count=len(self._price_buffer),
            swap_count=len(self._swap_buffer),
        )

    # ── 内部方法 ─────────────────────────────────────────────────

    async def _fetch_price(self) -> Optional[float]:
        """通过 OKX 获取 AERO 价格。复用 okx.get_quote（0.1 USDC 小额查价）。"""
        quote = await self._okx.get_quote(self._usdc, self._aero, int(0.1 * 1e6))
        if quote is None:
            return None
        to_amount = float(quote.get("toTokenAmount", "0"))
        if to_amount <= 0:
            return None
        to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
        token_amount = to_amount / (10 ** to_decimals)
        return 0.1 / token_amount if token_amount > 0 else None

    async def _fetch_swap_events(self) -> list[SwapEvent]:
        """从 Aerodrome 池获取 Swap 事件。

        复用 BuybackMonitor._check_token() 的 eth_getLogs + fromBlock 模式，
        只修改 topics 和事件解析逻辑。
        """
        current_block = await self._w3.eth.block_number

        # 首次: 往前 200 个区块（~10min）
        if self._last_swap_block <= 0:
            self._last_swap_block = current_block - 200

        from_block = self._last_swap_block
        if from_block >= current_block:
            return []

        try:
            logs = await self._w3.eth.get_logs({
                "address": self._pool,
                "fromBlock": hex(from_block),
                "toBlock": hex(current_block),
                "topics": [SWAP_TOPIC],
            })
        except Exception as e:
            logger.warning("获取 Swap 事件失败: %s", e)
            return []

        self._last_swap_block = current_block

        events = []
        for log in logs:
            tx_hash = (log.get("transactionHash") or b"").hex()
            raw_data = log.get("data", "0x" + "0" * 128)
            data_bytes = (
                raw_data if isinstance(raw_data, bytes)
                else bytes.fromhex(raw_data[2:] if raw_data.startswith("0x") else raw_data)
            )
            if len(data_bytes) < 128:
                continue

            ev = SwapEvent(
                tx_hash=tx_hash,
                timestamp=datetime.now(timezone.utc),
                amount0_in=int.from_bytes(data_bytes[0:32], "big"),
                amount1_in=int.from_bytes(data_bytes[32:64], "big"),
                amount0_out=int.from_bytes(data_bytes[64:96], "big"),
                amount1_out=int.from_bytes(data_bytes[96:128], "big"),
            )
            events.append(ev)

        if events:
            logger.debug("捕获 %d 个 Swap 事件 (block %d→%d)", len(events), from_block, current_block)

        return events

    async def _fetch_liquidity(self) -> float:
        """从池子合约获取流动性（USD 估值）。"""
        try:
            contract = self._w3.eth.contract(
                address=self._pool, abi=POOL_RESERVE_ABI,
            )
            reserves = await contract.functions.getReserves().call()
            reserve1 = reserves[1]  # USDC reserves
            return 2 * (reserve1 / 1e6)  # 双边总流动性 ≈ 2 × USDC 侧
        except Exception as e:
            logger.debug("获取流动性失败: %s", e)
            return 0.0

    async def _simulate_slippage(self, amount_usdc: float = 100) -> tuple[float, float]:
        """通过 OKX 模拟交易滑点。"""
        buy_slip = 1.0
        sell_slip = 1.0

        buy_quote = await self._okx.get_quote(
            self._usdc, self._aero, int(amount_usdc * 1e6),
        )
        if buy_quote:
            buy_slip = abs(float(buy_quote.get("priceImpactPercent", 1.0))) / 100

        # 用 100 USDC 等值 AERO 模拟卖出
        price = self._price_buffer[-1][1] if self._price_buffer else 0.1
        aero_amount = int((amount_usdc / price) * 1e18) if price > 0 else 0
        if aero_amount > 0:
            sell_quote = await self._okx.get_quote(
                self._aero, self._usdc, aero_amount,
            )
            if sell_quote:
                sell_slip = abs(float(sell_quote.get("priceImpactPercent", 1.0))) / 100

        return buy_slip, sell_slip

    def _record_price(self, price: float) -> None:
        """追加价格快照，维护滚动窗口。"""
        now = datetime.now(timezone.utc)
        self._price_buffer.append((now, price))

        # 整小时更新开盘价
        hour_key = now.replace(minute=0, second=0, microsecond=0)
        if hour_key != self._current_hour_key:
            self._current_hour_key = hour_key
            self._hour_open_price = price

    def _record_swaps(self, swaps: list[SwapEvent]) -> None:
        """将 Swap 事件追加到缓冲区。"""
        for s in swaps:
            self._swap_buffer.append(s)

            # 更新 VWAP 累计
            if s.amount0_in > 0 and s.amount1_out > 0:
                # 卖出: AERO → USDC
                price = (s.amount1_out / 1e6) / (s.amount0_in / 1e18)
                vol = s.amount0_in / 1e18
                self._vwap_price_sum += price * vol
                self._vwap_vol_sum += vol
            elif s.amount1_in > 0 and s.amount0_out > 0:
                # 买入: USDC → AERO
                price = (s.amount1_in / 1e6) / (s.amount0_out / 1e18)
                vol = s.amount0_out / 1e18
                self._vwap_price_sum += price * vol
                self._vwap_vol_sum += vol

    # ── 指标计算 ─────────────────────────────────────────────────

    def _find_price_at(self, seconds_ago: int) -> Optional[float]:
        """找到最近约 seconds_ago 秒前的价格。"""
        if len(self._price_buffer) < 2:
            return None
        target = datetime.now(timezone.utc).timestamp() - seconds_ago
        best: Optional[float] = None
        for ts, p in reversed(self._price_buffer):
            if ts.timestamp() <= target:
                best = p
                break
        return best

    def _compute_returns(self) -> dict[str, float]:
        """计算各周期涨幅。"""
        current = self._price_buffer[-1][1] if self._price_buffer else 0.0
        out = {"5m": 0.0, "15m": 0.0, "30m": 0.0, "1h": 0.0}
        for key, sec in [("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600)]:
            old = self._find_price_at(sec)
            if old and old > 0:
                out[key] = (current - old) / old
        return out

    def _compute_ma(self, window_minutes: int) -> float:
        """N 分钟简单移动平均。"""
        now_ts = datetime.now(timezone.utc).timestamp()
        prices = [
            p for ts, p in self._price_buffer
            if now_ts - ts.timestamp() <= window_minutes * 60
        ]
        return sum(prices) / len(prices) if prices else 0.0

    def _compute_vwap(self) -> float:
        """基于 Swap 事件的量加权平均价。"""
        if self._vwap_vol_sum <= 0:
            return self._price_buffer[-1][1] if self._price_buffer else 0.0
        return self._vwap_price_sum / self._vwap_vol_sum

    def _compute_buy_sell_pressure(self, minutes: int) -> tuple[float, float, float]:
        """计算指定窗口内的买入/卖出量（USDC）和买入占比。"""
        now_ts = datetime.now(timezone.utc).timestamp()
        buy = 0.0
        sell = 0.0

        for ev in self._swap_buffer:
            if now_ts - ev.timestamp.timestamp() > minutes * 60:
                continue
            # 买入: USDC 入池 (amount1_in) → 有人用 USDC 买 AERO
            if ev.amount1_in > 0 and ev.amount0_out > 0:
                buy += ev.amount1_in / 1e6
            # 卖出: AERO 入池 (amount0_in) → 有人卖 AERO 换 USDC
            if ev.amount0_in > 0 and ev.amount1_out > 0:
                sell += ev.amount1_out / 1e6

        total = buy + sell
        pressure = buy / total if total > 0 else 0.5
        return buy, sell, pressure

    def _compute_volume_stats(self, minutes: int = 5) -> tuple[float, float, float]:
        """计算当前窗口成交量、过去 1h 平均成交量、放大倍数。"""
        now_ts = datetime.now(timezone.utc).timestamp()
        vol_window = 0.0
        vol_1h = 0.0
        count_1h = 0

        for ev in self._swap_buffer:
            age = now_ts - ev.timestamp.timestamp()
            vol = (ev.amount1_in + ev.amount1_out) / 1e6  # USDC 侧作为参考
            if age <= minutes * 60:
                vol_window += vol
            if age <= 3600:
                vol_1h += vol
                count_1h += 1

        avg_per_window = vol_1h / (3600 / (minutes * 60)) if vol_1h > 0 else vol_window
        # 3600 / (minutes * 60) = 12 for 5min windows, 6 for 10min, etc.
        # 但更精确: 平均每个窗口的量
        num_windows = max(3600 / (minutes * 60), 1) if count_1h > 0 else 1
        avg_per_window = vol_1h / num_windows if vol_1h > 0 else vol_window

        ratio = vol_window / avg_per_window if avg_per_window > 0 else 0
        return vol_window, avg_per_window, ratio

    def _compute_pullback(self) -> float:
        """从近期（30min）最高点的回撤比例。"""
        if len(self._price_buffer) < 2:
            return 0.0
        current = self._price_buffer[-1][1]
        now_ts = datetime.now(timezone.utc).timestamp()
        recent_high = max(
            (p for ts, p in self._price_buffer if now_ts - ts.timestamp() <= 1800),
            default=0.0,
        )
        return (recent_high - current) / recent_high if recent_high > 0 else 0.0

    def _check_breakout_1h(self) -> bool:
        """当前价格是否 ≥ 过去 1h 最高价。"""
        if len(self._price_buffer) < 2:
            return False
        current = self._price_buffer[-1][1]
        now_ts = datetime.now(timezone.utc).timestamp()
        high_1h = max(
            (p for ts, p in self._price_buffer if now_ts - ts.timestamp() <= 3600),
            default=0.0,
        )
        return current >= high_1h if high_1h > 0 else False

    def _get_recent_high_1h(self) -> float:
        """过去 1h 最高价。"""
        now_ts = datetime.now(timezone.utc).timestamp()
        return max(
            (p for ts, p in self._price_buffer if now_ts - ts.timestamp() <= 3600),
            default=0.0,
        )
