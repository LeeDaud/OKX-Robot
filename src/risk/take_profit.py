"""
止盈监控器：定时轮询持仓价格，达到阈值自动卖出。
"""
import asyncio
import logging
from typing import Callable, Awaitable, Optional

from src.db.database import get_open_positions
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader, USDC_BASE

logger = logging.getLogger(__name__)


class TakeProfitMonitor:
    """监控所有持仓，当 ROI 达到阈值时自动卖出。"""

    def __init__(
        self,
        okx: OKXDexClient,
        trader: Trader,
        roi_threshold: float,
        check_interval: float,
        on_take_profit: Callable[[dict, float, float], Awaitable[None]],
    ) -> None:
        self._okx = okx
        self._trader = trader
        self._roi_threshold = roi_threshold
        self._interval = check_interval
        self._on_take_profit = on_take_profit
        self._running = False

    async def start(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(self._interval)
            if self._roi_threshold <= 0:
                continue
            try:
                await self._check_positions()
            except Exception as e:
                logger.warning("TakeProfit check error: %s", e)

    async def stop(self) -> None:
        self._running = False

    async def _check_positions(self) -> None:
        positions = await get_open_positions()
        for pos in positions:
            await self._evaluate(pos)

    async def _evaluate(self, pos: dict) -> None:
        token = pos["token_address"]

        # 优先使用实际成交数量
        filled_raw = pos.get("filled_amount")
        if filled_raw:
            amount_out = int(filled_raw)
            cost_usd = pos.get("cost_usd", 0.0)
        else:
            amount_out = int(pos.get("amount_out", 0))
            cost_usd = pos.get("cost_usd", 0.0)

        if not token or amount_out <= 0 or cost_usd <= 0:
            return

        # 查当前价值
        quote = await self._okx.get_quote(token, USDC_BASE, int(amount_out))
        if quote is None:
            return

        current_usd = float(quote.get("toTokenAmount", 0)) / 1e6
        if current_usd <= 0:
            return

        roi = (current_usd - cost_usd) / cost_usd
        if roi < self._roi_threshold:
            return

        logger.info("Take profit triggered: %s roi=%.2f%%", token[:10], roi * 100)

        tx_hash = await self._trader.sell(token, USDC_BASE, int(amount_out),
                                           source_tx=pos.get("tx_hash", ""))
        if tx_hash is None:
            logger.info("Take profit sell skipped: %s", self._trader.last_skip_reason)
            return

        pnl = current_usd - cost_usd
        await self._on_take_profit(pos, roi * 100, pnl)
