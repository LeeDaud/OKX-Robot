"""
双锚稳定套利策略（均值回归）。

数据来源：OKX CEX 日线 K 线
执行终端：Base 链 OKX DEX

入场：90天价格百分位 ≤ 30% + RSI(14) < 40 + MACD 日线金叉（分级制）
出场：ATR(14) 自适应止盈止损 + 时间止损
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── 指标计算（纯函数） ────────────────────────────────────────────────


def compute_ema(prices: list[float], period: int) -> float:
    """计算 EMA。用 SMA 作为初始种子。"""
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    seed = sum(prices[:period]) / period
    multiplier = 2.0 / (period + 1)
    ema = seed
    for p in prices[period:]:
        ema = (p - ema) * multiplier + ema
    return ema


def compute_sma(prices: list[float], period: int) -> float:
    """简单移动平均。"""
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    return sum(prices[-period:]) / period


def compute_rsi(closes: list[float], period: int = 14) -> float:
    """RSI(14) 使用 Wilder 平滑。需要 period + 1 根收盘价。"""
    if len(closes) < period + 1:
        return 50.0  # 数据不足返回中性值
    gains, losses = 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_macd(closes: list[float]) -> tuple[float, float, float]:
    """MACD(12,26,9)。返回 (macd_line, signal_line, histogram)。"""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    # 计算快线和慢线 EMA
    ema_fast = compute_ema(closes, 12)
    ema_slow = compute_ema(closes, 26)
    macd_line = ema_fast - ema_slow
    # 构建 MACD 序列用于信号线 EMA
    macd_values = []
    for i in range(26, len(closes)):
        chunk = closes[:i + 1]
        fast = compute_ema(chunk, 12)
        slow = compute_ema(chunk, 26)
        macd_values.append(fast - slow)
    signal_line = compute_ema(macd_values, 9) if macd_values else 0.0
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_macd_golden_cross(closes: list[float]) -> bool:
    """检查日线 MACD 是否已金叉或即将在 2 根 K 线内金叉。"""
    if len(closes) < 28:
        return False
    # 获取最近几根 MACD 值
    macd_vals = []
    for i in range(len(closes) - 5, len(closes) + 1):
        chunk = closes[:i]
        if len(chunk) < 26:
            continue
        fast = compute_ema(chunk, 12)
        slow = compute_ema(chunk, 26)
        macd_vals.append(fast - slow)
    if len(macd_vals) < 3:
        return False
    signal = compute_ema(macd_vals, min(9, len(macd_vals)))
    # 检查最近两根 K 线的 MACD 是否穿过 signal
    # 或者 MACD 刚在 signal 下方但即将上穿
    for i in range(-2, 0):
        if len(macd_vals) + i >= 0 and macd_vals[i] > signal:
            return True
    return False


def compute_atr(highs: list[float], lows: list[float],
                closes: list[float], period: int = 14) -> float:
    """ATR(14) 使用 Wilder 平滑。"""
    if len(highs) < period + 1:
        return 0.0
    tr_values = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        tr_values.append(tr)
    if len(tr_values) < period:
        return tr_values[-1] if tr_values else 0.0
    # 用 SMA 做种子，然后 Wilder 平滑
    atr = sum(tr_values[:period]) / period
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_percentile(price: float, highs_90d: list[float],
                       lows_90d: list[float]) -> float:
    """价格在 90 天高低点区间的百分位。0 = 最低点，100 = 最高点。"""
    if not highs_90d or not lows_90d:
        return 50.0
    high_max = max(highs_90d)
    low_min = min(lows_90d)
    if high_max == low_min:
        return 50.0
    return (price - low_min) / (high_max - low_min) * 100.0


def compute_ma_direction(prices: list[float], period: int) -> str:
    """判断均线方向。需要 period + 1 条数据。"""
    if len(prices) < period + 1:
        return "up"
    current_ma = compute_sma(prices, period)
    prev_ma = compute_sma(prices[:-1], period)
    return "up" if current_ma >= prev_ma else "down"


def compute_rsi_sequence(closes: list[float], period: int = 14) -> list[float]:
    """计算完整的 RSI 序列用于需要逐点的场景。"""
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    rsis = []
    for i in range(period, len(closes) + 1):
        rsis.append(compute_rsi(closes[:i], period))
    return rsis


# ── 数据模型 ──────────────────────────────────────────────────────────


@dataclass
class SymbolData:
    """单标的 OHLCV 数据集合。"""
    symbol: str
    closes: list[float]
    highs: list[float]
    lows: list[float]
    timestamps: list[int] = field(default_factory=list)  # Unix ms


@dataclass
class Indicators:
    """标的的完整指标状态。"""
    rsi: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_golden_cross: bool = False
    atr: float = 0.0
    sma_200: float = 0.0
    sma_200_direction: str = "up"
    price_percentile_90d: float = 50.0
    current_price: float = 0.0


@dataclass
class SignalResult:
    """信号判断结果。"""
    action: str          # "BUY" / "SELL" / "HOLD"
    signal_strength: str = ""  # "strong" / "medium" / ""
    reason: str = ""
    pct_to_sell: float = 1.0


@dataclass
class PositionState:
    """单个持仓状态。"""
    symbol: str
    token_address: str
    entry_price: float
    amount: float
    cost_basis_usdc: float
    entry_time: str               # ISO timestamp UTC
    buy_tx_hash: str = ""
    highest_price: float = 0.0
    tp1_done: bool = False
    tp2_done: bool = False
    trailing_stop_active: bool = False
    atr_at_entry: float = 0.0
    entry_signal: str = ""         # "strong" / "medium"


@dataclass
class MrState:
    """策略完整运行时状态。"""
    positions: dict[str, PositionState] = field(default_factory=dict)
    consecutive_losses: int = 0
    paused_until: Optional[str] = None   # ISO timestamp UTC
    daily_open_count: int = 0
    daily_open_date: str = ""            # YYYY-MM-DD
    last_indicators: dict[str, dict] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)


# ── 指标引擎 ──────────────────────────────────────────────────────────


class IndicatorEngine:
    """从 OHLCV 数据计算所有指标。纯函数容器。"""

    @staticmethod
    def compute(data: SymbolData) -> Indicators:
        ind = Indicators()
        ind.current_price = data.closes[-1] if data.closes else 0.0

        # RSI
        if len(data.closes) >= 15:
            ind.rsi = compute_rsi(data.closes, 14)

        # MACD
        if len(data.closes) >= 26:
            ind.macd_line, ind.macd_signal, ind.macd_histogram = compute_macd(data.closes)
            ind.macd_golden_cross = compute_macd_golden_cross(data.closes)

        # ATR
        if len(data.highs) >= 15 and len(data.lows) >= 15 and len(data.closes) >= 15:
            ind.atr = compute_atr(data.highs, data.lows, data.closes, 14)

        # SMA 200
        if len(data.closes) >= 200:
            ind.sma_200 = compute_sma(data.closes, 200)
            ind.sma_200_direction = compute_ma_direction(data.closes, 200)

        # 90 天价格百分位
        if len(data.highs) >= 90 and len(data.lows) >= 90:
            ind.price_percentile_90d = compute_percentile(
                ind.current_price, data.highs[-90:], data.lows[-90:],
            )

        return ind


# ── 信号引擎 ──────────────────────────────────────────────────────────


class SignalEngine:
    """入场/出场信号判断。"""

    @staticmethod
    def evaluate_entry(indicators: Indicators, has_position: bool,
                       cfg) -> Optional[SignalResult]:
        """检查入场信号。

        强信号：3/3 条件全满足 → 3% 仓位
        中信号：条件①+②满足 → 1.5% 仓位
        """
        if has_position:
            return None

        # 检查 200MA 趋势过滤
        if (indicators.sma_200 > 0
                and indicators.current_price < indicators.sma_200
                and indicators.sma_200_direction == "down"):
            return SignalResult("HOLD", reason="价格低于200MA且均线向下，不开新仓")

        cond_1 = indicators.price_percentile_90d <= 30.0
        cond_2 = indicators.rsi < 40
        cond_3 = indicators.macd_golden_cross

        if cond_1 and cond_2 and cond_3:
            return SignalResult("BUY", "strong",
                                f"强信号：百分位={indicators.price_percentile_90d:.1f}% "
                                f"RSI={indicators.rsi:.1f} MACD金叉")

        if cond_1 and cond_2:
            return SignalResult("BUY", "medium",
                                f"中信号：百分位={indicators.price_percentile_90d:.1f}% "
                                f"RSI={indicators.rsi:.1f} (缺MACD金叉)")

        return None

    @staticmethod
    def evaluate_exit(pos: PositionState, indicators: Indicators,
                      holding_hours: float, cfg) -> Optional[SignalResult]:
        """检查出场信号。按优先级 P0-P5。

        P0: 硬止损
        P1: T1 止盈（50%）
        P2: T2 止盈（剩余）
        P3: 移动止损
        P4: 时间止损
        P5: 强制平仓
        """
        if pos.entry_price <= 0 or pos.atr_at_entry <= 0:
            return None

        entry_price = pos.entry_price
        atr_pct = pos.atr_at_entry / entry_price
        current_price = indicators.current_price
        pnl_pct = (current_price - entry_price) / entry_price
        peak_pnl = (pos.highest_price - entry_price) / entry_price if pos.highest_price > 0 else 0.0

        # P5: 强制平仓
        if holding_hours >= cfg.force_close_hours:
            return SignalResult("SELL", reason=f"强制平仓：持仓{holding_hours:.0f}小时≥{cfg.force_close_hours}h",
                                pct_to_sell=1.0)

        # P4: 时间止损
        if holding_hours >= cfg.time_stop_hours:
            return SignalResult("SELL", reason=f"时间止损：持仓{holding_hours:.0f}小时≥{cfg.time_stop_hours}h",
                                pct_to_sell=1.0)

        # P0: 硬止损
        if pnl_pct <= -(cfg.atr_stop_mult * atr_pct):
            return SignalResult("SELL", reason=f"硬止损：盈亏{pnl_pct*100:.2f}%≤-{cfg.atr_stop_mult*atr_pct*100:.2f}%",
                                pct_to_sell=1.0)

        # P3: 移动止损（浮盈≥ATR×0.8后回撤至成本）
        if peak_pnl >= cfg.atr_trail_mult * atr_pct and pnl_pct <= 0:
            return SignalResult("SELL", reason="移动止损：已回撤至成本价",
                                pct_to_sell=1.0)

        # P1: T1 止盈（卖 50%）
        if not pos.tp1_done and pnl_pct >= cfg.atr_tp1_mult * atr_pct:
            return SignalResult("SELL", "medium",
                                f"T1止盈：盈亏{pnl_pct*100:.2f}%≥{cfg.atr_tp1_mult*atr_pct*100:.2f}%",
                                pct_to_sell=0.5)

        # P2: T2 止盈（卖完）
        if pos.tp1_done and not pos.tp2_done and pnl_pct >= cfg.atr_tp2_mult * atr_pct:
            return SignalResult("SELL", "medium",
                                f"T2止盈：盈亏{pnl_pct*100:.2f}%≥{cfg.atr_tp2_mult*atr_pct*100:.2f}%",
                                pct_to_sell=1.0)

        return None


# ── 策略编排 ──────────────────────────────────────────────────────────


class MeanReversionStrategy:
    """均值回归策略编排器。"""

    STATE_KEY = "mean_reversion_state"

    def __init__(self, cfg, cex_client, trader, guard, state_mgr,
                 notifier=None, w3=None):
        self.cfg = cfg
        self.cex = cex_client
        self.trader = trader
        self.guard = guard
        self.state_mgr = state_mgr
        self.notifier = notifier
        self.w3 = w3

        self.state = MrState()
        self._candle_cache: dict[str, SymbolData] = {}  # symbol -> cached data
        self._last_fetch_time: float = 0
        self._fetch_cooldown = 3600  # 1 小时缓存

    async def initialize(self) -> bool:
        """从持久化状态恢复。"""
        try:
            raw = self.state_mgr.load().get(self.STATE_KEY, {})
            if raw:
                positions = {}
                for sym, p in raw.get("positions", {}).items():
                    positions[sym] = PositionState(**p)
                self.state.positions = positions
                self.state.consecutive_losses = raw.get("consecutive_losses", 0)
                self.state.paused_until = raw.get("paused_until")
                self.state.daily_open_count = raw.get("daily_open_count", 0)
                self.state.daily_open_date = raw.get("daily_open_date", "")
                self.state.last_indicators = raw.get("last_indicators", {})
                self.state.last_prices = raw.get("last_prices", {})
                logger.info("均值回归策略状态恢复: %d 个持仓", len(self.state.positions))
            return True
        except Exception as e:
            logger.error("均值回归策略初始化失败: %s", e)
            return False

    async def _persist(self):
        """持久化运行时状态。"""
        try:
            positions_dict = {}
            for sym, p in self.state.positions.items():
                positions_dict[sym] = {
                    "symbol": p.symbol,
                    "token_address": p.token_address,
                    "entry_price": p.entry_price,
                    "amount": p.amount,
                    "cost_basis_usdc": p.cost_basis_usdc,
                    "entry_time": p.entry_time,
                    "buy_tx_hash": p.buy_tx_hash,
                    "highest_price": p.highest_price,
                    "tp1_done": p.tp1_done,
                    "tp2_done": p.tp2_done,
                    "trailing_stop_active": p.trailing_stop_active,
                    "atr_at_entry": p.atr_at_entry,
                    "entry_signal": p.entry_signal,
                }
            self.state_mgr.update(**{
                self.STATE_KEY: {
                    "positions": positions_dict,
                    "consecutive_losses": self.state.consecutive_losses,
                    "paused_until": self.state.paused_until,
                    "daily_open_count": self.state.daily_open_count,
                    "daily_open_date": self.state.daily_open_date,
                    "last_indicators": self.state.last_indicators,
                    "last_prices": self.state.last_prices,
                }
            })
        except Exception as e:
            logger.error("均值回归状态持久化失败: %s", e)

    async def _fetch_candles(self, symbol: str) -> Optional[SymbolData]:
        """从 OKX CEX 获取日线数据。先获取最近 300 根，再补历史数据。"""
        try:
            logger.info("获取 %s 日线数据...", symbol)
            # 先拿最近 300 根（OKX 上限）
            candles = await self.cex.get_candles(symbol, bar="1D", limit="300")
            if not candles:
                logger.warning("%s 无 K 线数据", symbol)
                return None

            # 如果需要更早期的数据（SMA200 需要 200 根以上）
            if len(candles) < 200:
                oldest_ts = candles[-1][0]  # 最早的 K 线时间戳
                more_candles = await self.cex.get_history_candles(
                    symbol, bar="1D", before=oldest_ts, limit="300",
                )
                if more_candles:
                    candles = candles + more_candles

            candles.sort(key=lambda x: x[0])  # 按时间升序

            closes = [float(c[4]) for c in candles]
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            timestamps = [int(c[0]) for c in candles]

            return SymbolData(
                symbol=symbol,
                closes=closes,
                highs=highs,
                lows=lows,
                timestamps=timestamps,
            )
        except Exception as e:
            logger.error("获取 %s K线失败: %s", symbol, e)
            return None

    async def collect_data(self) -> dict[str, Indicators]:
        """为所有配置的标的获取数据并计算指标。"""
        now = datetime.now(timezone.utc).timestamp()
        # 缓存控制：1 小时内不重复拉取
        if now - self._last_fetch_time < self._fetch_cooldown and self._candle_cache:
            logger.debug("使用缓存的 K 线数据")
            return self._compute_all_indicators(self._candle_cache)

        all_indicators = {}
        for token_cfg in self.cfg.tokens:
            data = await self._fetch_candles(token_cfg.symbol)
            if data is None:
                continue
            self._candle_cache[token_cfg.symbol] = data
            indicators = IndicatorEngine.compute(data)
            all_indicators[token_cfg.symbol] = indicators

        self._last_fetch_time = now
        return all_indicators

    def _compute_all_indicators(self, cache: dict[str, SymbolData]) -> dict[str, Indicators]:
        """从缓存计算指标（不重新拉取网络数据）。"""
        result = {}
        for sym, data in cache.items():
            result[sym] = IndicatorEngine.compute(data)
        return result

    async def check_entries(self, all_indicators: dict[str, Indicators]):
        """检查入场信号并执行买入。"""
        # 重置每日开仓计数
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.daily_open_date != today:
            self.state.daily_open_count = 0
            self.state.daily_open_date = today

        # 检查暂停状态
        if self.state.paused_until:
            paused_dt = datetime.fromisoformat(self.state.paused_until)
            if datetime.now(timezone.utc) < paused_dt:
                logger.info("策略暂停中，直到 %s", self.state.paused_until)
                return
            self.state.paused_until = None

        # 检查每日开仓限制
        if self.state.daily_open_count >= self.cfg.max_daily_open:
            logger.info("今日已开仓 %d 次，达到上限", self.state.daily_open_count)
            return

        # 检查总持仓数
        if len(self.state.positions) >= self.cfg.max_positions:
            logger.info("持仓已达上限 %d", self.cfg.max_positions)
            return

        for token_cfg in self.cfg.tokens:
            if token_cfg.symbol in self.state.positions:
                continue  # 已有持仓
            indicators = all_indicators.get(token_cfg.symbol)
            if indicators is None:
                continue

            has_pos = token_cfg.symbol in self.state.positions
            signal = SignalEngine.evaluate_entry(indicators, has_pos, self.cfg)
            if signal is None or signal.action != "BUY":
                continue

            # 计算仓位大小
            pos_pct = (self.cfg.position_size_strong
                       if signal.signal_strength == "strong"
                       else self.cfg.position_size_medium)

            logger.info("[%s] %s 信号: %s", token_cfg.symbol, signal.signal_strength, signal.reason)

            # 获取 USDC 余额
            usdc_balance = await self._get_usdc_balance()
            if usdc_balance <= 0:
                logger.warning("USDC 余额不足")
                continue

            amount_usdc = usdc_balance * pos_pct
            if amount_usdc < 2:
                logger.warning("%s 仓位 USDC=%0.2f 过小，跳过", token_cfg.symbol, amount_usdc)
                continue

            # 执行买入
            await self._execute_buy(token_cfg, amount_usdc, indicators, signal)

    async def _get_usdc_balance(self) -> float:
        """获取钱包 USDC 余额。"""
        try:
            if self.w3 is None:
                return 100.0  # dry-run 默认值
            from src.executor.trader import USDC_BASE
            from eth_utils import to_checksum_address
            from web3 import Web3
            erc20_abi = [{"constant": True, "inputs": [{"name": "_owner","type": "address"}],
                          "name": "balanceOf","outputs": [{"name": "balance","type": "uint256"}],
                          "type": "function"}]
            contract = self.w3.eth.contract(address=Web3.to_checksum_address(USDC_BASE), abi=erc20_abi)
            raw = await contract.functions.balanceOf(
                Web3.to_checksum_address(self.trader.wallet_address)
            ).call()
            return float(raw) / 1_000_000
        except Exception as e:
            logger.error("获取 USDC 余额失败: %s", e)
            return 0.0

    async def _execute_buy(self, token_cfg, amount_usdc: float,
                           indicators: Indicators, signal: SignalResult):
        """执行买入逻辑。"""
        try:
            logger.info("[%s] 执行买入: $%.2f USDC (%s)",
                        token_cfg.symbol, amount_usdc, signal.signal_strength)

            if self.trader is None:
                raise RuntimeError("trader 未初始化")

            result = await self.trader.buy(
                token=token_cfg.token_address,
                amount_in=amount_usdc,
                base_token="USDC",
            )

            if result is None:
                logger.error("[%s] 买入失败（trader 返回空）", token_cfg.symbol)
                return

            # 解析成交结果
            tx_hash = result.get("tx_hash", "")
            filled_raw = result.get("filled_amount", 0)
            amount_out = result.get("amount_out", 0)

            # 估算买入价格和代币数量
            price = indicators.current_price
            token_amount = amount_usdc / price if price > 0 else 0

            pos = PositionState(
                symbol=token_cfg.symbol,
                token_address=token_cfg.token_address,
                entry_price=price,
                amount=token_amount,
                cost_basis_usdc=amount_usdc,
                entry_time=datetime.now(timezone.utc).isoformat(),
                buy_tx_hash=tx_hash,
                highest_price=price,
                atr_at_entry=indicators.atr,
                entry_signal=signal.signal_strength,
            )
            self.state.positions[token_cfg.symbol] = pos
            self.state.daily_open_count += 1

            # 记录买入交易到 DB
            await self._record_trade(token_cfg, "buy", amount_usdc, price, tx_hash, signal.signal_strength)

            logger.info("[%s] 买入成功: $%.2f, 价格=%.2f, tx=%s",
                        token_cfg.symbol, amount_usdc, price, tx_hash[:10] if tx_hash else "dry-run")
            await self._persist()

        except Exception as e:
            logger.error("[%s] 买入执行异常: %s", token_cfg.symbol, e)

    async def check_exits(self, all_indicators: dict[str, Indicators]):
        """检查出场信号并执行卖出。"""
        now = datetime.now(timezone.utc)

        for token_cfg in self.cfg.tokens:
            pos = self.state.positions.get(token_cfg.symbol)
            if pos is None:
                continue

            indicators = all_indicators.get(token_cfg.symbol)
            if indicators is None:
                # 使用缓存的最新价格
                current_price = self.state.last_prices.get(token_cfg.symbol, pos.entry_price)
                indicators = Indicators(current_price=current_price)

            # 更新最高价和当前价格
            if indicators.current_price > pos.highest_price:
                pos.highest_price = indicators.current_price
            if indicators.atr > 0 and pos.atr_at_entry == 0:
                pos.atr_at_entry = indicators.atr

            # 计算持仓时间（小时）
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time)
            except (ValueError, TypeError):
                entry_dt = now
            holding_hours = (now - entry_dt).total_seconds() / 3600

            # 自动激活移动止损（浮盈≥ATR×0.8）
            pnl_pct = (indicators.current_price - pos.entry_price) / pos.entry_price
            atr_pct = pos.atr_at_entry / pos.entry_price if pos.entry_price > 0 else 0
            if (not pos.trailing_stop_active
                    and pnl_pct >= self.cfg.atr_trail_mult * atr_pct):
                pos.trailing_stop_active = True
                logger.info("[%s] 移动止损已激活：浮盈%.2f%%≥%.2f%%",
                            pos.symbol, pnl_pct * 100, self.cfg.atr_trail_mult * atr_pct * 100)

            signal = SignalEngine.evaluate_exit(pos, indicators, holding_hours, self.cfg)
            if signal is None or signal.action != "SELL":
                continue

            logger.info("[%s] %s", pos.symbol, signal.reason)
            await self._execute_sell(pos, indicators, signal)

    async def _execute_sell(self, pos: PositionState, indicators: Indicators,
                            signal: SignalResult):
        """执行卖出逻辑。"""
        try:
            pnl_pct = (indicators.current_price - pos.entry_price) / pos.entry_price
            sell_pct = signal.pct_to_sell  # 0.5 for T1, 1.0 for others
            sell_amount = pos.amount * sell_pct
            sell_value = pos.cost_basis_usdc * sell_pct

            logger.info("[%s] 执行卖出: %.4f tokens (%s)",
                        pos.symbol, sell_amount, signal.reason)

            if self.trader is not None:
                result = await self.trader.sell(
                    token_in=pos.token_address,
                    amount_in=sell_amount,
                    base_token="USDC",
                )
                tx_hash = result.get("tx_hash", "") if result else ""
            else:
                tx_hash = ""

            # 处理 T1/T2 部分卖出
            if sell_pct < 1.0:
                pos.amount -= sell_amount
                pos.cost_basis_usdc -= sell_value
                pos.tp1_done = True
                realized_pnl_pct = pnl_pct
                await self._record_trade(
                    self._find_token_cfg(pos.symbol), "sell",
                    sell_value, indicators.current_price, tx_hash,
                    f"TP1_{pos.symbol}",
                )
                logger.info("[%s] T1止盈完成: 卖出 %.0f%%, 盈亏 %.2f%%",
                            pos.symbol, sell_pct * 100, realized_pnl_pct * 100)
            else:
                # 全部清仓
                realized_pnl_pct = pnl_pct
                is_loss = realized_pnl_pct < 0

                await self._record_trade(
                    self._find_token_cfg(pos.symbol), "sell",
                    pos.cost_basis_usdc, indicators.current_price, tx_hash,
                    f"close_{pos.symbol}",
                )

                if is_loss:
                    self.state.consecutive_losses += 1
                    if self.state.consecutive_losses >= self.cfg.consecutive_loss_pause:
                        pause_until = (datetime.now(timezone.utc)
                                       + timedelta(hours=self.cfg.pause_hours))
                        self.state.paused_until = pause_until.isoformat()
                        logger.warning("连续%d笔亏损，暂停交易至%s",
                                       self.state.consecutive_losses, self.state.paused_until)
                else:
                    self.state.consecutive_losses = 0

                del self.state.positions[pos.symbol]
                logger.info("[%s] 平仓完成: 盈亏 %.2f%%, 连续亏损 %d",
                            pos.symbol, realized_pnl_pct * 100, self.state.consecutive_losses)

            await self._persist()

        except Exception as e:
            logger.error("[%s] 卖出执行异常: %s", pos.symbol, e)

    def _find_token_cfg(self, symbol: str):
        """通过 symbol 查找 token 配置。"""
        for t in self.cfg.tokens:
            if t.symbol == symbol:
                return t
        return None

    async def _record_trade(self, token_cfg, side: str, amount_usdc: float,
                            price: float, tx_hash: str, strategy_tag: str):
        """记录交易到数据库。"""
        try:
            if token_cfg is None:
                return
            from src.db.database import insert_buy, insert_sell
            if side == "buy":
                await insert_buy(
                    tx_hash=tx_hash or f"mr_dry_{token_cfg.symbol}_{datetime.now(timezone.utc).timestamp()}",
                    token_address=token_cfg.token_address,
                    strategy=f"mr_{strategy_tag}",
                    amount_in=amount_usdc,
                    amount_out=int(amount_usdc / price * 1e6) if price > 0 else 0,
                    cost_usd=amount_usdc,
                    status="success" if tx_hash else "pending",
                )
            else:
                await insert_sell(
                    token_address=token_cfg.token_address,
                    strategy=f"mr_{strategy_tag}",
                    amount_in=0,
                    amount_out=int(amount_usdc / price * 1e6) if price > 0 else 0,
                    cost_usd=amount_usdc,
                    pnl_usd=0.0,
                    roi=0.0,
                    status="success" if tx_hash else "pending",
                )
        except Exception as e:
            logger.warning("记录交易到 DB 失败: %s", e)

    async def check_risk_controls(self, all_indicators: dict[str, Indicators]):
        """检查全局风控条件。"""
        # 黑天鹅检测：单标的一日跌幅 > black_swan_drop_pct
        for token_cfg in self.cfg.tokens:
            indicators = all_indicators.get(token_cfg.symbol)
            if indicators is None:
                continue
            # 使用缓存的昨日收盘价
            prev_close = self.state.last_prices.get(f"{token_cfg.symbol}_prev_close", 0)
            if prev_close > 0:
                drop_pct = (prev_close - indicators.current_price) / prev_close
                if drop_pct > self.cfg.black_swan_drop_pct:
                    logger.warning("[%s] 黑天鹅检测: 单日跌幅 %.2f%%",
                                   token_cfg.symbol, drop_pct * 100)
                    # 清空所有持仓
                    for pos in list(self.state.positions.values()):
                        await self._execute_sell(pos, indicators, SignalResult("SELL", reason="黑天鹅清仓"))
                    return

        # 更新缓存价格
        for sym, ind in all_indicators.items():
            self.state.last_prices[sym] = ind.current_price
            if ind.current_price > 0:
                self.state.last_indicators[sym] = {
                    "rsi": round(ind.rsi, 1),
                    "atr": round(ind.atr, 2),
                    "macd_line": round(ind.macd_line, 2),
                    "macd_signal": round(ind.macd_signal, 2),
                    "macd_golden_cross": ind.macd_golden_cross,
                    "sma_200": round(ind.sma_200, 2),
                    "sma_200_direction": ind.sma_200_direction,
                    "price_percentile_90d": round(ind.price_percentile_90d, 1),
                    "current_price": round(ind.current_price, 2),
                }

    async def close_all_positions(self, reason: str = "策略停止"):
        """关闭所有持仓（用于停机和重启）。"""
        for pos in list(self.state.positions.values()):
            indicators = Indicators(current_price=self.state.last_prices.get(pos.symbol, pos.entry_price))
            await self._execute_sell(
                pos, indicators,
                SignalResult("SELL", reason=reason, pct_to_sell=1.0),
            )

    async def tick(self):
        """主循环：收集数据 → 检查入场 → 检查出场 → 风控。"""
        try:
            all_indicators = await self.collect_data()
            if not all_indicators:
                logger.warning("未获取到任何标的指标数据")
                return

            await self.check_risk_controls(all_indicators)
            await self.check_exits(all_indicators)
            await self.check_entries(all_indicators)

            # 定期持久化
            await self._persist()

        except Exception as e:
            logger.error("均值回归策略执行异常: %s", e, exc_info=True)

    def get_state(self) -> dict:
        """返回当前完整状态，供 API 读取。"""
        now = datetime.now(timezone.utc)
        symbols = []
        for token_cfg in self.cfg.tokens:
            pos = self.state.positions.get(token_cfg.symbol)
            indicators = self.state.last_indicators.get(token_cfg.symbol, {})
            entry_conditions = self._compute_entry_conditions(indicators)

            symbol_state = {
                "symbol": token_cfg.symbol,
                "has_position": pos is not None,
                "indicators": indicators,
                "entry_conditions": entry_conditions,
                "signal_strength": pos.entry_signal if pos else "",
            }

            if pos:
                try:
                    entry_dt = datetime.fromisoformat(pos.entry_time)
                    holding_hours = (now - entry_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    holding_hours = 0

                current_price = self.state.last_prices.get(pos.symbol, pos.entry_price)
                pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

                symbol_state["position"] = {
                    "symbol": pos.symbol,
                    "token_address": pos.token_address,
                    "entry_price": pos.entry_price,
                    "amount": pos.amount,
                    "cost_basis_usdc": pos.cost_basis_usdc,
                    "current_price": current_price,
                    "position_value_usdc": pos.amount * current_price,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "holding_hours": round(holding_hours, 1),
                    "entry_signal": pos.entry_signal,
                    "tp1_done": pos.tp1_done,
                    "tp2_done": pos.tp2_done,
                    "trailing_stop_active": pos.trailing_stop_active,
                    "entry_time": pos.entry_time,
                    "buy_tx_hash": pos.buy_tx_hash,
                }

            symbols.append(symbol_state)

        return {
            "enabled": self.cfg.enabled,
            "symbols": symbols,
            "consecutive_losses": self.state.consecutive_losses,
            "paused_until": self.state.paused_until,
            "daily_open_count": self.state.daily_open_count,
            "daily_open_date": self.state.daily_open_date,
            "config": {
                "position_size_strong": self.cfg.position_size_strong,
                "position_size_medium": self.cfg.position_size_medium,
                "atr_period": self.cfg.atr_period,
                "atr_tp1_mult": self.cfg.atr_tp1_mult,
                "atr_tp2_mult": self.cfg.atr_tp2_mult,
                "atr_stop_mult": self.cfg.atr_stop_mult,
                "atr_trail_mult": self.cfg.atr_trail_mult,
                "max_positions": self.cfg.max_positions,
                "max_daily_open": self.cfg.max_daily_open,
                "time_stop_hours": self.cfg.time_stop_hours,
                "force_close_hours": self.cfg.force_close_hours,
                "consecutive_loss_pause": self.cfg.consecutive_loss_pause,
                "daily_loss_pct_limit": self.cfg.daily_loss_pct_limit,
                "black_swan_drop_pct": self.cfg.black_swan_drop_pct,
                "pause_hours": self.cfg.pause_hours,
            },
        }

    def _compute_entry_conditions(self, indicators: dict) -> list[dict]:
        """计算入场条件的达标状态（供前端展示）。"""
        price_percentile = indicators.get("price_percentile_90d", 50)
        rsi = indicators.get("rsi", 50)

        return [
            {
                "label": "90天价格百分位 ≤ 30%",
                "ok": price_percentile <= 30,
                "current": price_percentile,
                "threshold": "≤ 30%",
            },
            {
                "label": "RSI(14) < 40",
                "ok": rsi < 40,
                "current": rsi,
                "threshold": "< 40",
            },
            {
                "label": "MACD 日线金叉",
                "ok": indicators.get("macd_golden_cross", False),
                "current": indicators.get("macd_golden_cross", False),
                "threshold": "已金叉或即将金叉",
            },
        ]
