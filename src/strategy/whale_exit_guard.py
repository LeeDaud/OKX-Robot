"""
WhaleExitGuard：基于大户持仓成本分布的退出决策模块。

输入：大户持仓排名（来自 WhaleRadar leaderboard API）
输出：SAFE / WARN / EXIT 三级信号
"""
import logging
from enum import Enum
from dataclasses import dataclass

from .virtuals_client import WhalePosition

logger = logging.getLogger(__name__)


class ExitSignal(Enum):
    SAFE = "safe"          # 安全，不触发退出
    WARN = "warn"          # 接近危险区，准备减仓
    EXIT = "exit"          # 到达危险区，执行退出


@dataclass
class WhaleAnalysis:
    """单次分析结果。"""
    signal: ExitSignal
    whale_count: int                # 大户数量
    top_holder_pct: float           # 第一大户持仓占比
    concentration_pct: float        # 前 3 名持仓占比
    low_cost_whale_pct: float       # 低成本大户占比
    danger_price: float | None      # 危险价位
    avg_cost_v: float | None        # 加权平均成本
    current_price: float | None     # 当前价格


class WhaleExitGuard:
    """基于大户持仓成本分布的退出守卫。

    核心逻辑：
    - 如果大户集中度过高 → WARN/EXIT
    - 如果当前价格接近大户平均成本 → 准备退出
    - 如果大户开始移动 → EXIT
    """

    def __init__(
        self,
        exit_threshold_pct: float = 1.5,
        warn_threshold_pct: float = 2.0,
        max_concentration_pct: float = 40.0,
        max_low_cost_ratio: float = 0.5,
    ) -> None:
        """
        Args:
            exit_threshold_pct: 当前价 / 大户加权成本 低于此倍数时触发 EXIT
            warn_threshold_pct: 当前价 / 大户加权成本 低于此倍数时触发 WARN
            max_concentration_pct: 前 3 名持仓占比超过此值时触发 WARN
            max_low_cost_ratio: 低成本大户占比超过此值时触发 WARN
        """
        self._exit_threshold = exit_threshold_pct
        self._warn_threshold = warn_threshold_pct
        self._max_concentration = max_concentration_pct
        self._max_low_cost_ratio = max_low_cost_ratio

    def analyze(
        self,
        positions: list[WhalePosition],
        current_price_v: float | None = None,
    ) -> WhaleAnalysis:
        """分析大户持仓，返回退出信号。"""
        if not positions:
            return WhaleAnalysis(
                signal=ExitSignal.SAFE, whale_count=0,
                top_holder_pct=0, concentration_pct=0,
                low_cost_whale_pct=0, danger_price=None,
                avg_cost_v=None, current_price=current_price_v,
            )

        # 解析持仓数据
        parsed = []
        for p in positions:
            try:
                spent = float(p.sum_spent_v_est or "0")
                tokens = float(p.sum_token_bought or "0")
                avg_cost = float(p.avg_cost_v or "0")
                parsed.append({
                    "wallet": p.wallet,
                    "spent": spent,
                    "tokens": tokens,
                    "avg_cost": avg_cost,
                })
            except (ValueError, TypeError):
                continue

        if not parsed:
            return WhaleAnalysis(
                signal=ExitSignal.SAFE, whale_count=0,
                top_holder_pct=0, concentration_pct=0,
                low_cost_whale_pct=0, danger_price=None,
                avg_cost_v=None, current_price=current_price_v,
            )

        # 按持仓量排序
        parsed.sort(key=lambda x: x["tokens"], reverse=True)
        total_tokens = sum(x["tokens"] for x in parsed)

        if total_tokens <= 0:
            return WhaleAnalysis(
                signal=ExitSignal.SAFE, whale_count=len(parsed),
                top_holder_pct=0, concentration_pct=0,
                low_cost_whale_pct=0, danger_price=None,
                avg_cost_v=None, current_price=current_price_v,
            )

        # 集中度
        top_holder_pct = parsed[0]["tokens"] / total_tokens * 100 if parsed else 0
        top3_tokens = sum(x["tokens"] for x in parsed[:3])
        concentration_pct = top3_tokens / total_tokens * 100

        # 加权平均成本
        total_spent = sum(x["spent"] for x in parsed)
        weighted_avg_cost = total_spent / total_tokens if total_tokens > 0 else 0

        # 低成本大户占比（成本低于加权平均 50% 的视为低成本）
        low_cost_threshold = weighted_avg_cost * 0.5
        low_cost_tokens = sum(
            x["tokens"] for x in parsed
            if 0 < x["avg_cost"] < low_cost_threshold
        )
        low_cost_whale_pct = low_cost_tokens / total_tokens * 100

        # 计算危险价位
        danger_price = weighted_avg_cost * self._exit_threshold

        # 判断信号
        signal = ExitSignal.SAFE
        reasons = []

        if current_price_v is not None and current_price_v > 0:
            price_ratio = current_price_v / weighted_avg_cost if weighted_avg_cost > 0 else float("inf")

            if price_ratio <= self._exit_threshold:
                signal = ExitSignal.EXIT
                reasons.append(f"价格({current_price_v:.6f})逼近大户成本({weighted_avg_cost:.6f})，倍数={price_ratio:.2f}")
            elif price_ratio <= self._warn_threshold:
                signal = ExitSignal.WARN
                reasons.append(f"价格接近大户成本区，倍数={price_ratio:.2f}")

        if concentration_pct > self._max_concentration and signal.value != "exit":
            signal = ExitSignal.WARN
            reasons.append(f"大户集中度过高: top3={concentration_pct:.1f}%")

        if low_cost_whale_pct > self._max_low_cost_ratio * 100 and signal.value != "exit":
            signal = ExitSignal.WARN
            reasons.append(f"低成本大户占比过高: {low_cost_whale_pct:.1f}%")

        if reasons:
            logger.info("[WHALE] %s: %s", signal.value, "; ".join(reasons))

        return WhaleAnalysis(
            signal=signal,
            whale_count=len(parsed),
            top_holder_pct=round(top_holder_pct, 1),
            concentration_pct=round(concentration_pct, 1),
            low_cost_whale_pct=round(low_cost_whale_pct, 1),
            danger_price=round(danger_price, 12) if danger_price > 0 else None,
            avg_cost_v=round(weighted_avg_cost, 12),
            current_price=current_price_v,
        )

    def should_exit(self, analysis: WhaleAnalysis) -> bool:
        return analysis.signal == ExitSignal.EXIT

    def should_warn(self, analysis: WhaleAnalysis) -> bool:
        return analysis.signal in (ExitSignal.WARN, ExitSignal.EXIT)
