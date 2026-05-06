"""
网格交易策略：在价格网格的不同价位挂单，跌到位就买，涨到位就卖。
"""
import logging
import math
import time
from collections import deque
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
        self._max_slots: int = getattr(config, "max_slots", 12)
        self._volatility_adjust: bool = getattr(config, "volatility_adjust", False)
        self._vol_window: int = max(getattr(config, "volatility_window", 20), 5)
        self._price_history: deque = deque(maxlen=self._vol_window)
        self._adjusted_spread_val: float = config.spread_pct

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
        """几何间距网格：价格越下跌，买入线越密集。"""
        per_slot = self._config.investment_usdc / max(self._config.levels, 1)
        ratio = 1 + self._config.spread_pct / 100.0
        slots = []
        for i in range(self._config.levels):
            buy_price = current_price / (ratio ** (i + 1))
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
        self._update_volatility(price)

        await self._extend_if_needed(price)

        for slot in self._slots:
            if slot.status == "idle" and price <= slot.buy_price:
                await self._buy(slot)
            elif slot.status == "bought" and price >= slot.sell_price:
                await self._sell(slot)

        self._save_state()

        # 每轮输出 slots 概况用于排查
        idle_count = sum(1 for s in self._slots if s.status == "idle")
        bought_count = sum(1 for s in self._slots if s.status == "bought")
        logger.debug("[GRID] tick: price=$%.6f slots=%d (idle=%d bought=%d)",
                     price, len(self._slots), idle_count, bought_count)

    async def _extend_if_needed(self, current_price: float) -> None:
        """价格突破现有网格范围时自动延伸。"""
        if not self._slots:
            return

        max_sell = max(s.sell_price for s in self._slots)
        min_buy = min(s.buy_price for s in self._slots)
        idle_slots = [s for s in self._slots if s.status == "idle"]

        if current_price > max_sell and idle_slots and len(self._slots) < self._max_slots:
            await self._extend_upward(current_price, idle_slots)
        elif current_price < min_buy and idle_slots and len(self._slots) < self._max_slots:
            await self._extend_downward(current_price, idle_slots)

    async def _extend_upward(self, current_price: float, idle: list) -> None:
        """向上延伸：在现有范围上方添加新槽位。"""
        spread = self._adjusted_spread()
        ratio = 1 + spread / 100.0
        sorted_slots = sorted(self._slots, key=lambda s: s.buy_price)
        highest = sorted_slots[-1]
        new_buy = highest.buy_price * ratio
        new_sell = new_buy * (1 + self._config.profit_pct / 100.0)

        slot = idle[0]
        next_id = max(s.slot_id for s in self._slots) + 1
        old_id = slot.slot_id

        slot.slot_id = next_id
        slot.buy_price = round(new_buy, 12)
        slot.sell_price = round(new_sell, 12)
        slot.buy_tx = ""
        slot.filled_amount = 0

        logger.info("[GRID] 向上延伸: slot %d→%d buy=$%.6f sell=$%.6f (当前价 $%.6f, 原最高卖出 $%.6f)",
                    old_id, next_id, new_buy, new_sell, current_price,
                    max(s.sell_price for s in self._slots))

    async def _extend_downward(self, current_price: float, idle: list) -> None:
        """向下延伸：在现有范围下方添加新槽位。"""
        spread = self._adjusted_spread()
        ratio = 1 + spread / 100.0
        sorted_slots = sorted(self._slots, key=lambda s: s.buy_price)
        lowest = sorted_slots[0]
        new_buy = lowest.buy_price / ratio
        new_sell = new_buy * (1 + self._config.profit_pct / 100.0)

        slot = idle[0]
        next_id = max(s.slot_id for s in self._slots) + 1
        old_id = slot.slot_id

        slot.slot_id = next_id
        slot.buy_price = round(max(new_buy, 0.000001), 12)
        slot.sell_price = round(new_sell, 12)
        slot.buy_tx = ""
        slot.filled_amount = 0

        logger.info("[GRID] 向下延伸: slot %d→%d buy=$%.6f sell=$%.6f (当前价 $%.6f, 原最低买入 $%.6f)",
                    old_id, next_id, new_buy, new_sell, current_price,
                    min(s.buy_price for s in self._slots))

    def _update_volatility(self, price: float) -> None:
        """维护价格窗口，计算波动率并调整网格间距。"""
        if not self._volatility_adjust:
            return

        self._price_history.append(price)
        if len(self._price_history) < 5:
            return

        prices = list(self._price_history)
        returns = []
        for i in range(1, len(prices)):
            r = math.log(prices[i] / prices[i - 1]) if prices[i - 1] > 0 else 0.0
            returns.append(r)

        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        vol = math.sqrt(variance)

        # 波动率高 → 放宽价差（减少无效触发）；波动率低 → 收紧价差（增加交易频率）
        scale = 30.0
        mult = max(0.5, min(2.0, 1.0 + vol * scale))
        self._adjusted_spread_val = round(self._config.spread_pct * mult, 4)

        logger.debug("[GRID] vol=%.6f mult=%.2f spread=%.2f%% → %.2f%%",
                     vol, mult, self._config.spread_pct, self._adjusted_spread_val)

    def _adjusted_spread(self) -> float:
        """返回当前波动率调整后的 spread_pct。"""
        return self._adjusted_spread_val if self._volatility_adjust else self._config.spread_pct

    async def _get_price(self) -> Optional[float]:
        """通过 OKX 估算代币的 USDC 价格，失败时使用缓存价格。"""
        quote = await self._okx.get_quote(USDC_BASE, self._token, int(0.1 * 1e6))
        if quote is None:
            if self._current_price > 0:
                logger.debug("[GRID] 价格获取失败，使用缓存 $%.6f", self._current_price)
                return self._current_price
            return None
        to_amount = float(quote.get("toTokenAmount", "0"))
        if to_amount <= 0:
            if self._current_price > 0:
                return self._current_price
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
