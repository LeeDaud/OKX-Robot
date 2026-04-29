"""
止盈监控器：定时轮询持仓价格，达到阈值自动卖出。
"""
import asyncio
import logging
from typing import Callable, Awaitable

from src.db.database import get_open_positions, close_position, _amount_to_usd
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader
from src.monitor.decoder import USDC_BASE

logger = logging.getLogger(__name__)


class TakeProfitMonitor:
    def __init__(
        self,
        okx: OKXDexClient,
        traders: dict[str, Trader],
        take_profit_roi: float,
        check_interval: float,
        on_take_profit: Callable[[dict, float, float], Awaitable[None]],
    ) -> None:
        self._okx = okx
        self._traders = traders
        self._roi_threshold = take_profit_roi
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
        token = pos["token_out"]

        # 优先使用机器人实际成交数据，旧记录 fallback 到目标报价估算
        filled_raw = pos.get("filled_amount")
        if filled_raw:
            amount_out = int(filled_raw)
            cost_usd = pos.get("filled_cost_usd", 0.0)
        else:
            amount_out = pos.get("amount_out", 0)
            cost_usd = _amount_to_usd(pos.get("amount_in", 0), pos.get("token_in", USDC_BASE))

        if not token or amount_out <= 0 or cost_usd <= 0:
            return

        # 查当前价值：用 amount_out 个 token 能换多少 USDC
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

        # 找对应的 trader（用 source_addr 匹配）
        trader = self._traders.get(pos["source_addr"])
        if trader is None:
            logger.warning("No trader for source_addr=%s, skipping take profit", pos.get("source_addr"))
            return

        tx = await trader.sell(token, USDC_BASE, int(amount_out), source_tx=pos.get("source_tx", ""))
        if tx is None:
            logger.info("Take profit sell skipped: %s", trader.last_skip_reason)
            return

        pnl = current_usd - cost_usd

        await close_position(
            position_id=pos["position_id"],
            exit_price=current_usd / amount_out if amount_out else 0,
            roi_pct=roi * 100,
            pnl=pnl,
        )

        await self._on_take_profit(pos, roi, pnl)
