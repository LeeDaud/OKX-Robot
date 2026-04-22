"""
每日亏损风控：超过阈值时阻断新跟单。
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)


class DailyLossGuard:
    def __init__(self, limit_usd: float) -> None:
        self._limit = limit_usd
        self._today = date.today()
        self._loss_today: float = 0.0  # 负值累计

    def record_pnl(self, pnl_usd: float) -> None:
        if date.today() != self._today:
            self._today = date.today()
            self._loss_today = 0.0
        if pnl_usd < 0:
            self._loss_today += pnl_usd

    def can_trade(self) -> bool:
        if date.today() != self._today:
            self._today = date.today()
            self._loss_today = 0.0
        loss = abs(self._loss_today)
        if loss >= self._limit:
            logger.warning(
                "Daily loss limit reached: $%.2f >= $%.2f, trading paused",
                loss, self._limit,
            )
            return False
        return True
