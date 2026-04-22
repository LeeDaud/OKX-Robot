"""
飞书群机器人 Webhook 通知。
配置：在 config.yaml 中设置 feishu_webhook_url。
"""
import logging
import aiohttp
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))


class FeishuNotifier:
    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url
        self._enabled = bool(webhook_url)

    async def _send(self, text: str) -> None:
        if not self._enabled:
            return
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(self._url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        logger.warning("Feishu webhook failed: %s", await r.text())
        except Exception as e:
            logger.warning("Feishu send error: %s", e)

    async def notify_trade(
        self,
        source_tx: str,
        symbol_in: str,
        symbol_out: str,
        token_in: str,
        token_out: str,
        amount_in_usd: float,
        our_tx: str | None,
        dry_run: bool,
        side: str = "buy",
        roi_pct: float | None = None,
        pnl_usd: float | None = None,
        balance_usdc: float = 0.0,
        balance_eth: float = 0.0,
    ) -> None:
        mode = "🔵 DRY-RUN" if dry_run else "🟢 LIVE"
        direction = "📈 买入" if side == "buy" else "📉 卖出"
        status = f"✅ {our_tx[:12]}..." if our_tx else ("📋 模拟" if dry_run else "❌ 失败")
        now_cst = datetime.now(CST).strftime("%m-%d %H:%M")

        lines = [
            f"{mode} {direction}  {now_cst}",
            f"来源: {source_tx[:12]}...",
            f"方向: {symbol_in} → {symbol_out}",
            f"  {token_in}",
            f"  → {token_out}",
            f"金额: ${amount_in_usd:.2f} USDC",
        ]
        if roi_pct is not None and pnl_usd is not None:
            sign = "+" if pnl_usd >= 0 else ""
            lines.append(f"收益: {sign}{roi_pct:.1f}%  ({sign}{pnl_usd:.2f} USDC)")
        lines.append(f"状态: {status}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 余额: ${balance_usdc:.2f} USDC  |  {balance_eth:.5f} ETH")
        if balance_eth < 0.003:
            lines.append("⚠️ ETH 余额不足，请及时补充 gas")

        await self._send("\n".join(lines))

    async def notify_take_profit(
        self,
        symbol: str,
        token: str,
        roi_pct: float,
        pnl_usd: float,
    ) -> None:
        sign = "+" if pnl_usd >= 0 else ""
        await self._send(
            f"🎯 止盈触发\n"
            f"代币: {symbol}  {token}\n"
            f"收益率: +{roi_pct:.1f}%\n"
            f"盈亏: {sign}{pnl_usd:.2f} USDC"
        )

    async def notify_filtered(self, source_tx: str, reason: str) -> None:
        await self._send(f"⏭️ 跟单跳过\n来源: {source_tx[:12]}...\n原因: {reason}")

    async def notify_hourly_report(
        self,
        balance_usdc: float,
        balance_eth: float,
        unrealized_pnl: float,
        realized_pnl: float,
        total_invested: float,
        positions: list[dict],
    ) -> None:
        total_roi = (realized_pnl / total_invested * 100) if total_invested > 0 else 0.0
        unr_sign = "+" if unrealized_pnl >= 0 else ""
        rea_sign = "+" if realized_pnl >= 0 else ""
        now_cst = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

        lines = [
            f"📊 整点汇报 {now_cst}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"💰 余额: ${balance_usdc:.2f} USDC  |  {balance_eth:.5f} ETH",
        ]
        if balance_eth < 0.003:
            lines.append("⚠️ ETH 余额不足，请及时补充 gas")
        lines += [
            f"📈 浮动盈亏: {unr_sign}${unrealized_pnl:.2f}",
            f"✅ 实际盈亏: {rea_sign}${realized_pnl:.2f}",
            f"📊 总收益率: {'+' if total_roi >= 0 else ''}{total_roi:.1f}%",
        ]

        if positions:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📦 当前持仓（{len(positions)} 笔）")
            for p in positions:
                cost = p.get("cost_usd", 0)
                current = p.get("current_usd", 0)
                roi = p.get("roi_pct", 0)
                sym = p.get("symbol", p.get("token_out", "?")[:10])
                sign = "+" if roi >= 0 else ""
                lines.append(f"  {sym:<10} ${cost:.2f} → ${current:.2f}  {sign}{roi:.1f}%")
        else:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("📦 当前无持仓")

        await self._send("\n".join(lines))

    async def notify_daily_report(
        self,
        total_trades: int,
        success: int,
        pnl_usd: float,
    ) -> None:
        sign = "+" if pnl_usd >= 0 else ""
        text = (
            f"📊 每日跟单汇报 {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}\n"
            f"总跟单: {total_trades} 笔\n"
            f"成功: {success} 笔\n"
            f"今日盈亏: {sign}{pnl_usd:.2f} USDC"
        )
        await self._send(text)

    async def notify_risk_halt(self, loss_usd: float, limit_usd: float) -> None:
        await self._send(
            f"🚨 风控触发：今日亏损 ${loss_usd:.2f} 已达上限 ${limit_usd:.2f}\n"
            f"当日跟单已暂停"
        )
