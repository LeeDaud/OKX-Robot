"""
网格交易策略：在价格网格的不同价位挂单，跌到位就买，涨到位就卖。
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from src.executor.trader import USDC_BASE
from src.db.database import insert_buy, insert_sell

logger = logging.getLogger(__name__)


@dataclass
class GridSlot:
    """一个网格位。"""
    slot_id: int
    buy_price: float          # 触发买入的价格（USDC）
    sell_price: float         # 触发卖出的价格（USDC）
    amount_usdc: float        # 此位投入的 USDC
    status: str = "idle"      # idle | bought
    buy_tx: str = ""
    sell_tx: str = ""
    filled_amount: int = 0    # 买入成交数量（raw）
    created_at: str = ""


class GridStrategy:
    """网格交易策略。"""

    def __init__(
        self,
        okx,
        trader,
        config,
        notifier,
        guard,
        state_mgr,
        dry_run: bool = True,
    ) -> None:
        self._okx = okx
        self._trader = trader
        self._config = config
        self._notifier = notifier
        self._guard = guard
        self._state_mgr = state_mgr
        self._dry_run = dry_run
        self._token = config.token.lower()
        self._slots: list[GridSlot] = []
        self._current_price: float = 0.0
        self._initialized = False

    async def initialize(self) -> bool:
        """从持久化恢复或创建新的网格。"""
        loaded = self._load_state()
        if loaded:
            self._slots = loaded
            self._initialized = True
            logger.info("[GRID] 从状态恢复: %d slots", len(self._slots))
            return True

        price = await self._get_price()
        if price is None or price <= 0:
            logger.error("[GRID] 无法获取 %s 初始价格", self._token[:10])
            return False

        self._current_price = price
        self._slots = self._build_slots(price)
        self._save_state()
        self._initialized = True

        logger.info("[GRID] 初始化: token=%s price=$%.6f levels=%d spread=%.1f%% invest=$%.0f",
                    self._token[:10], price, self._config.levels,
                    self._config.spread_pct, self._config.investment_usdc)
        for s in self._slots:
            logger.info("[GRID]  slot %d: buy=$%.6f sell=$%.6f amount=$%.2f",
                        s.slot_id, s.buy_price, s.sell_price, s.amount_usdc)
        return True

    def _build_slots(self, current_price: float) -> list[GridSlot]:
        per_slot = self._config.investment_usdc / max(self._config.levels, 1)
        slots = []
        for i in range(self._config.levels):
            buy_price = current_price * (1 - self._config.spread_pct / 100.0 * (i + 1))
            sell_price = buy_price * (1 + self._config.profit_pct / 100.0)
            slots.append(GridSlot(
                slot_id=i,
                buy_price=round(max(buy_price, 0.000001), 12),
                sell_price=round(sell_price, 12),
                amount_usdc=round(per_slot, 2),
            ))
        return slots

    async def tick(self) -> None:
        """单次轮询：获取当前价格并检查各 slot 是否触发。"""
        if not self._initialized:
            return

        price = await self._get_price()
        if price is None or price <= 0:
            return
        self._current_price = price

        changed = False
        for slot in self._slots:
            if slot.status == "idle" and price <= slot.buy_price:
                await self._buy(slot)
                changed = True
            elif slot.status == "bought" and price >= slot.sell_price:
                await self._sell(slot)
                changed = True

        self._save_state()

    async def _get_price(self) -> Optional[float]:
        """通过 OKX 估算代币的 USDC 价格。"""
        quote = await self._okx.get_quote(USDC_BASE, self._token, int(0.1 * 1e6))
        if quote is None:
            return None
        to_amount = float(quote.get("toTokenAmount", "0"))
        if to_amount <= 0:
            return None
        to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
        token_amount = to_amount / (10 ** to_decimals)
        return 0.1 / token_amount if token_amount > 0 else None

    async def _buy(self, slot: GridSlot) -> None:
        if not self._guard.can_trade():
            logger.info("[GRID] 风控已触发，跳过买入 slot %d", slot.slot_id)
            return

        amount_raw = int(slot.amount_usdc * 1e6)
        source = f"grid_buy_{slot.slot_id}_{time.time_ns()}"

        tx_hash, filled_raw = await self._trader.buy(
            self._token, amount_raw,
            payment_token=USDC_BASE, payment_decimals=6,
            source_tx=source,
        )

        if tx_hash and filled_raw > 0:
            slot.status = "bought"
            slot.buy_tx = tx_hash
            slot.filled_amount = filled_raw
            await insert_buy(tx_hash, self._token, amount_raw, filled_raw,
                             strategy="grid", cost_usd=slot.amount_usdc,
                             filled_amount=str(filled_raw))
            self._guard.record_pnl(-slot.amount_usdc)
            await self._notifier.notify_trade(
                source, self._token[:10], self._token[:10],
                USDC_BASE, self._token, slot.amount_usdc, "USDC",
                tx_hash, self._dry_run, side="buy",
                wallet_label="Grid",
            )
            logger.info("[GRID] slot %d 买入: tx=%s filled=%d",
                        slot.slot_id, tx_hash[:12], filled_raw)
        else:
            reason = self._trader.last_skip_reason or "执行失败"
            await self._notifier.notify_trade(
                source, self._token[:10], self._token[:10],
                USDC_BASE, self._token, slot.amount_usdc, "USDC",
                None, self._dry_run, side="buy",
                skip_reason=reason, wallet_label="Grid",
            )
            logger.info("[GRID] slot %d 买入跳过: %s", slot.slot_id, reason)

    async def _sell(self, slot: GridSlot) -> None:
        if not self._guard.can_trade():
            return

        if slot.filled_amount <= 0:
            logger.warning("[GRID] slot %d 无可卖数量，重置", slot.slot_id)
            slot.status = "idle"
            return

        source = f"grid_sell_{slot.slot_id}_{time.time_ns()}"
        tx_hash = await self._trader.sell(
            self._token, token_out=USDC_BASE,
            amount=slot.filled_amount,
            source_tx=source,
        )

        if tx_hash:
            # 计算盈亏
            exit_quote = await self._okx.get_quote(self._token, USDC_BASE, slot.filled_amount)
            exit_usd = float(exit_quote.get("toTokenAmount", 0)) / 1e6 if exit_quote else 0.0

            pnl = exit_usd - slot.amount_usdc
            roi = (pnl / slot.amount_usdc * 100) if slot.amount_usdc > 0 else 0.0

            await insert_sell(tx_hash, self._token, slot.filled_amount, 0,
                             strategy="grid_sell", cost_usd=slot.amount_usdc,
                             pnl_usd=pnl, roi=roi)

            await self._notifier.notify_trade(
                source, self._token[:10], "USDC",
                self._token, USDC_BASE, exit_usd, "USDC",
                tx_hash, self._dry_run, side="sell",
                roi_pct=roi, pnl_usd=pnl, wallet_label="Grid",
            )

            logger.info("[GRID] slot %d 卖出: tx=%s pnl=%.2f roi=%.1f%%",
                        slot.slot_id, tx_hash[:12], pnl, roi)

            # 重置 slot 循环使用
            slot.status = "idle"
            slot.buy_tx = ""
            slot.filled_amount = 0
        else:
            logger.info("[GRID] slot %d 卖出跳过: %s",
                        slot.slot_id, self._trader.last_skip_reason)

    def _save_state(self) -> None:
        self._state_mgr.update(**{
            "grid_slots": [
                {
                    "slot_id": s.slot_id,
                    "buy_price": s.buy_price,
                    "sell_price": s.sell_price,
                    "amount_usdc": s.amount_usdc,
                    "status": s.status,
                    "buy_tx": s.buy_tx,
                    "sell_tx": s.sell_tx,
                    "filled_amount": s.filled_amount,
                }
                for s in self._slots
            ],
            "grid_current_price": self._current_price,
        })

    def _load_state(self) -> Optional[list[GridSlot]]:
        state = self._state_mgr.load()
        raw = state.get("grid_slots")
        if not raw:
            return None
        try:
            return [
                GridSlot(
                    slot_id=int(r["slot_id"]),
                    buy_price=float(r["buy_price"]),
                    sell_price=float(r["sell_price"]),
                    amount_usdc=float(r["amount_usdc"]),
                    status=str(r.get("status", "idle")),
                    buy_tx=str(r.get("buy_tx", "")),
                    sell_tx=str(r.get("sell_tx", "")),
                    filled_amount=int(r.get("filled_amount", 0)),
                )
                for r in raw
            ]
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("[GRID] 状态解析失败: %s", e)
            return None
