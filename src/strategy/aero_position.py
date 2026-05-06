"""
AERO 持仓管理器。

管理：
- 持仓状态（入场价、数量、最高价、分批止盈状态）
- 连续亏损计数
- 冷却期判断（复用 StrategyState）
- 移动止盈追踪
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from math import floor

from src.state.persistence import StateManager, StrategyState
from src.risk.guard import DailyLossGuard
from src.db.database import get_trades_by_strategy, DB_PATH

logger = logging.getLogger(__name__)

STRATEGY_NAME = "aero_trend"
CONSECUTIVE_KEY = "aero_consecutive_losses"
POSITION_KEY = "aero_position"
COOLDOWN_KEY = "aero_trend"


@dataclass
class AeroPosition:
    """AERO 持仓状态快照。"""
    has_position: bool = False
    entry_price: float = 0.0
    current_price: float = 0.0
    position_amount: float = 0.0        # AERO 数量
    cost_basis_usdc: float = 0.0
    position_value_usdc: float = 0.0
    pnl_pct: float = 0.0
    highest_price_since_entry: float = 0.0
    holding_time_minutes: int = 0
    take_profit_1_done: bool = False
    take_profit_2_done: bool = False
    trailing_stop_active: bool = False
    entry_time: Optional[datetime] = None
    buy_tx_hash: str = ""
    filled_amount: int = 0  # raw AERO

    def update_price(self, price: float) -> None:
        """更新当前价、最高价，重新计算浮盈。"""
        self.current_price = price
        if price > self.highest_price_since_entry:
            self.highest_price_since_entry = price

        self.position_value_usdc = self.position_amount * price
        if self.cost_basis_usdc > 0:
            self.pnl_pct = (self.position_value_usdc - self.cost_basis_usdc) / self.cost_basis_usdc

        if self.entry_time:
            elapsed = datetime.now(timezone.utc) - self.entry_time
            self.holding_time_minutes = int(elapsed.total_seconds() // 60)

    def drawdown_from_peak(self) -> float:
        """当前从持仓最高点回撤比例。"""
        if self.highest_price_since_entry <= 0:
            return 0.0
        return (self.highest_price_since_entry - self.current_price) / self.highest_price_since_entry

    def to_dict(self) -> dict:
        return {
            "has_position": self.has_position,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "position_amount": self.position_amount,
            "cost_basis_usdc": self.cost_basis_usdc,
            "position_value_usdc": self.position_value_usdc,
            "pnl_pct": self.pnl_pct,
            "highest_price_since_entry": self.highest_price_since_entry,
            "holding_time_minutes": self.holding_time_minutes,
            "take_profit_1_done": self.take_profit_1_done,
            "take_profit_2_done": self.take_profit_2_done,
            "trailing_stop_active": self.trailing_stop_active,
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "buy_tx_hash": self.buy_tx_hash,
            "filled_amount": self.filled_amount,
        }

    @staticmethod
    def from_dict(d: dict) -> "AeroPosition":
        pos = AeroPosition(
            has_position=d.get("has_position", False),
            entry_price=float(d.get("entry_price", 0)),
            current_price=float(d.get("current_price", 0)),
            position_amount=float(d.get("position_amount", 0)),
            cost_basis_usdc=float(d.get("cost_basis_usdc", 0)),
            position_value_usdc=float(d.get("position_value_usdc", 0)),
            pnl_pct=float(d.get("pnl_pct", 0)),
            highest_price_since_entry=float(d.get("highest_price_since_entry", 0)),
            holding_time_minutes=int(d.get("holding_time_minutes", 0)),
            take_profit_1_done=d.get("take_profit_1_done", False),
            take_profit_2_done=d.get("take_profit_2_done", False),
            trailing_stop_active=d.get("trailing_stop_active", False),
            entry_time=datetime.fromisoformat(d["entry_time"]) if d.get("entry_time") else None,
            buy_tx_hash=d.get("buy_tx_hash", ""),
            filled_amount=int(d.get("filled_amount", 0)),
        )
        return pos


class PositionManager:
    """持仓管理器。

    复用:
      - StateManager → 持久化持仓状态
      - StrategyState → 冷却期判断
      - DailyLossGuard → 每日亏损上限
    """

    def __init__(
        self,
        state_mgr: StateManager,
        strategy_state: StrategyState,
        guard: DailyLossGuard,
        db_path: str = DB_PATH,
    ):
        self._state_mgr = state_mgr
        self._strategy_state = strategy_state
        self._guard = guard
        self._db_path = db_path
        self._pos = AeroPosition()
        self._consecutive_losses = 0
        self._conservative_mode = False
        self._halted = False

    @property
    def position(self) -> AeroPosition:
        return self._pos

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def conservative_mode(self) -> bool:
        return self._conservative_mode

    @property
    def halted(self) -> bool:
        return self._halted

    # ── 持久化 ─────────────────────────────────────────────────

    async def load(self) -> None:
        """从 state.json 恢复持仓和连续亏损计数。"""
        state = self._state_mgr.load()
        raw_pos = state.get(POSITION_KEY)
        if raw_pos:
            self._pos = AeroPosition.from_dict(raw_pos)
        else:
            self._pos = AeroPosition()

        self._consecutive_losses = int(state.get(CONSECUTIVE_KEY, 0))

        # 初始从 DB 恢复连续亏损计数（跨重启）
        if self._consecutive_losses == 0 and not self._pos.has_position:
            await self._sync_consecutive_losses()

        logger.info(
            "AERO 持仓已恢复: pos=%s losses=%d",
            "有" if self._pos.has_position else "无",
            self._consecutive_losses,
        )

    def save(self) -> None:
        """持久化持仓状态。"""
        self._state_mgr.update(**{
            POSITION_KEY: self._pos.to_dict(),
            CONSECUTIVE_KEY: self._consecutive_losses,
        })

    # ── 持仓操作 ─────────────────────────────────────────────────

    def open_position(
        self,
        price: float,
        amount_aero: float,
        cost_usdc: float,
        filled_raw: int,
        tx_hash: str,
    ) -> None:
        """开仓。"""
        self._pos = AeroPosition(
            has_position=True,
            entry_price=price,
            current_price=price,
            position_amount=amount_aero,
            cost_basis_usdc=cost_usdc,
            position_value_usdc=cost_usdc,
            highest_price_since_entry=price,
            entry_time=datetime.now(timezone.utc),
            buy_tx_hash=tx_hash,
            filled_amount=filled_raw,
        )
        self.save()

    async def close_position(self, pnl_pct: float) -> None:
        """平仓，记录盈亏。"""
        if not self._pos.has_position:
            return

        # 记录盈亏
        self._guard.record_pnl(self._pos.position_value_usdc - self._pos.cost_basis_usdc)

        # 连续亏损追踪
        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0  # 盈利一笔即重置

        # 保守模式: 单日盈利达 cap
        if pnl_pct > 0:
            day_pnl = await self._compute_day_pnl()
            if day_pnl >= self._get_daily_profit_cap_pct():
                self._conservative_mode = True
                logger.info("AERO 进入保守模式: 当日盈利已达上限")

        self._pos = AeroPosition()
        self.save()

    def partial_close(self, pnl_pct: float, pct_sold: float) -> None:
        """分批止盈：卖出部分持仓。"""
        if not self._pos.has_position:
            return

        self._pos.position_amount *= (1 - pct_sold)
        self._pos.cost_basis_usdc *= (1 - pct_sold)

        if self._pos.take_profit_1_done is False:
            self._pos.take_profit_1_done = True
            logger.info("TP1 完成: 卖出 %.0f%% 持仓", pct_sold * 100)
        elif not self._pos.take_profit_2_done:
            self._pos.take_profit_2_done = True
            self._pos.trailing_stop_active = True
            logger.info("TP2 完成: 卖出 %.0f%%，启用移动止盈", pct_sold * 100)

        self.save()

    def halt_today(self) -> None:
        """当日停止交易。"""
        self._halted = True
        logger.warning("AERO 当日停止交易")

    # ── 风控检查 ─────────────────────────────────────────────────

    def can_trade(self) -> tuple[bool, str]:
        """检查是否允许交易。"""
        if self._halted:
            return False, "当日已停止交易"

        if not self._guard.can_trade():
            return False, "每日亏损上限已触发"

        if self._consecutive_losses >= 5:
            return False, f"连续亏损 {self._consecutive_losses} 次，已停止"

        if self._pos.has_position:
            return False, "已有持仓"

        if self._strategy_state.is_on_cooldown(COOLDOWN_KEY, self._get_cooldown_seconds()):
            return False, "冷却期中"

        return True, "ok"

    def set_cooldown(self) -> None:
        """交易结束后设置冷却期。"""
        self._strategy_state.set_last_run(COOLDOWN_KEY)

    def get_position_size(self, total_usdc: float) -> float:
        """根据当前状态获取仓位比例。"""
        pct = self._get_cfg().position_size_pct

        # 连续 3 笔亏损 → 减半
        if self._consecutive_losses >= 3:
            pct = self._get_cfg().position_size_reduced

        # 保守模式 → 减半
        if self._conservative_mode:
            pct /= 2

        return total_usdc * pct

    def reset_daily(self) -> None:
        """每日重置（外部调用）。"""
        self._halted = False
        self._conservative_mode = False
        logger.info("AERO 每日状态已重置")

    # ── 内部 ─────────────────────────────────────────────────────

    async def _sync_consecutive_losses(self) -> None:
        """从 DB 恢复最近交易盈亏。"""
        try:
            trades = await get_trades_by_strategy(STRATEGY_NAME, self._db_path)
            losses = 0
            for t in trades[:10]:  # 最近 10 笔
                pnl = float(t.get("pnl_usd", 0))
                if pnl < 0:
                    losses += 1
                elif pnl > 0:
                    losses = 0  # 盈利一笔重置
            self._consecutive_losses = losses
        except Exception as e:
            logger.warning("同步连续亏损失败: %s", e)

    async def _compute_day_pnl(self) -> float:
        """查询当日 AERO 策略总盈利比例。"""
        try:
            trades = await get_trades_by_strategy(STRATEGY_NAME, self._db_path)
            today = datetime.now(timezone.utc).date().isoformat()
            day_pnl = 0.0
            for t in trades:
                if t.get("created_at", "").startswith(today):
                    day_pnl += float(t.get("pnl_usd", 0))
            total_invested = sum(
                float(t.get("cost_usd", 0))
                for t in trades
                if t.get("created_at", "").startswith(today) and t.get("side") == "buy"
            )
            return day_pnl / total_invested if total_invested > 0 else 0.0
        except Exception:
            return 0.0

    def _get_cooldown_seconds(self) -> int:
        return self._get_cfg().cooldown_minutes * 60

    def _get_daily_profit_cap_pct(self) -> float:
        return self._get_cfg().daily_profit_cap

    def _get_cfg(self):
        from src.config.loader import load_config
        return load_config().aero_trend
