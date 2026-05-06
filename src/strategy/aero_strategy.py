"""
AERO 趋势策略引擎。

纯函数设计：输入 MarketSnapshot + 持仓状态 → 输出 TradeDecision。
不依赖链上调用，方便单元测试。
"""
from dataclasses import dataclass
from typing import Optional

from src.strategy.aero_collector import MarketSnapshot
from src.strategy.aero_position import AeroPosition


@dataclass
class TradeDecision:
    """策略引擎输出的交易决策。"""
    action: str  # "BUY" | "SELL" | "HOLD"
    reason: str
    strategy_type: str = ""        # "breakout_momentum" | "strong_pullback"
    pct_to_sell: float = 1.0       # 卖出比例，分批止盈用
    decision_id: str = ""          # 去重用的决策标识


class IndicatorEngine:
    """纯函数指标计算。"""

    @staticmethod
    def meets_trend_startup(snap: MarketSnapshot, cfg) -> bool:
        """趋势启动型买入：全部 11 个条件。"""
        return (
            snap.price_breakout_1h
            and snap.price > snap.vwap
            and snap.price > snap.ma_20m
            and snap.return_5m >= cfg.min_return_5m
            and snap.return_5m <= cfg.max_return_5m
            and snap.return_15m >= cfg.min_return_15m
            and snap.return_30m < cfg.max_return_30m
            and snap.volume_ratio >= cfg.min_volume_ratio
            and snap.buy_pressure >= cfg.min_buy_pressure
            and snap.pool_liquidity_usd >= cfg.min_liquidity_usd
            and snap.simulated_buy_slippage < cfg.max_slippage_buy
        )

    @staticmethod
    def meets_strong_pullback(snap: MarketSnapshot, cfg) -> bool:
        """强势回踩型买入。"""
        trend_1h_up = snap.return_1h > 0
        near_vwap_or_ma = (
            abs(snap.price - snap.vwap) / snap.vwap < 0.02
            if snap.vwap > 0
            else False
        ) or (
            abs(snap.price - snap.ma_20m) / snap.ma_20m < 0.02
            if snap.ma_20m > 0
            else False
        )

        # 卖压下降: 最近 10min 的卖压低于 5min 卖压
        sell_pressure_declining = (
            snap.sell_volume_5m < snap.buy_volume_5m * 0.8
        )

        return (
            trend_1h_up
            and snap.price_above_open_1h
            and near_vwap_or_ma
            and cfg.pullback_min <= snap.pullback_from_high <= cfg.pullback_max
            and sell_pressure_declining
            and snap.volume_ratio >= cfg.pullback_volume_ratio
            and snap.buy_pressure >= cfg.pullback_buy_pressure
            and snap.simulated_buy_slippage < cfg.max_slippage_buy
        )

    @staticmethod
    def check_hard_stop_loss(pos: AeroPosition, cfg) -> bool:
        """P0 硬止损：浮亏 ≥ stop_loss_pct。"""
        return pos.has_position and pos.pnl_pct <= -cfg.stop_loss_pct

    @staticmethod
    def check_reversal_sell(snap: MarketSnapshot) -> tuple[bool, int]:
        """P1 信号反转：任意 2/5 条件满足。"""
        conditions = 0

        if snap.buy_pressure < 0.35:  # 卖压 ≥ 65%
            conditions += 1
        if snap.price < snap.vwap:
            conditions += 1
        if snap.price < snap.ma_20m:
            conditions += 1
        if snap.volume_ratio >= 2 and snap.return_5m < 0:
            conditions += 1

        return conditions >= 2, conditions

    @staticmethod
    def check_time_stop(pos: AeroPosition, cfg) -> bool:
        """P2 时间止损：持仓 ≥ time_stop_minutes 且浮盈 < time_stop_min_profit。"""
        return (
            pos.has_position
            and pos.holding_time_minutes >= cfg.time_stop_minutes
            and pos.pnl_pct < cfg.time_stop_min_profit
        )

    @staticmethod
    def check_take_profit(pos: AeroPosition, cfg) -> Optional[float]:
        """P3 分批止盈：返回应卖出的比例，None 表示不触发。"""
        if not pos.has_position:
            return None

        # TP1: 10%
        if not pos.take_profit_1_done and pos.pnl_pct >= cfg.take_profit_1_pct:
            return cfg.take_profit_1_ratio

        # TP2: 20%
        if pos.take_profit_1_done and not pos.take_profit_2_done and pos.pnl_pct >= cfg.take_profit_2_pct:
            return cfg.take_profit_2_ratio

        return None

    @staticmethod
    def check_trailing_stop(pos: AeroPosition, cfg) -> bool:
        """P4 移动止盈：持仓最高点回撤 ≥ trailing_stop_drawdown。"""
        return (
            pos.has_position
            and pos.trailing_stop_active
            and pos.drawdown_from_peak() >= cfg.trailing_stop_drawdown
        )


class TrendStrategy:
    """AERO 趋势策略主引擎。

    输入 MarketSnapshot + AeroPosition → TradeDecision。
    按优先级逐级评估卖出（P0-P4），再评估买入。
    """

    def __init__(self, cfg):
        self._cfg = cfg

    def evaluate_exit(self, snap: MarketSnapshot, pos: AeroPosition) -> Optional[TradeDecision]:
        """按优先级评估卖出条件。返回第一个触发。"""
        # P0: 硬止损
        if IndicatorEngine.check_hard_stop_loss(pos, self._cfg):
            return TradeDecision(action="SELL", reason="硬止损", pct_to_sell=1.0)

        # P1: 信号反转
        reversal, cond_count = IndicatorEngine.check_reversal_sell(snap)
        if reversal:
            return TradeDecision(action="SELL", reason=f"信号反转({cond_count}/5)", pct_to_sell=1.0)

        # P2: 时间止损
        if IndicatorEngine.check_time_stop(pos, self._cfg):
            return TradeDecision(action="SELL", reason="时间止损", pct_to_sell=1.0)

        # P3: 分批止盈
        tp_ratio = IndicatorEngine.check_take_profit(pos, self._cfg)
        if tp_ratio is not None:
            label = "TP1" if not pos.take_profit_1_done else "TP2"
            return TradeDecision(action="SELL", reason=label, pct_to_sell=tp_ratio)

        # P4: 移动止盈
        if IndicatorEngine.check_trailing_stop(pos, self._cfg):
            return TradeDecision(action="SELL", reason="移动止盈", pct_to_sell=1.0)

        return None

    def evaluate_entry(self, snap: MarketSnapshot, pos: AeroPosition) -> Optional[TradeDecision]:
        """评估买入条件。已有持仓时跳过。"""
        if pos.has_position:
            return None

        # 优先趋势启动型
        if IndicatorEngine.meets_trend_startup(snap, self._cfg):
            return TradeDecision(
                action="BUY",
                reason="趋势启动",
                strategy_type="breakout_momentum",
            )

        # 次优强势回踩型
        if IndicatorEngine.meets_strong_pullback(snap, self._cfg):
            return TradeDecision(
                action="BUY",
                reason="强势回踩",
                strategy_type="strong_pullback",
            )

        return None
