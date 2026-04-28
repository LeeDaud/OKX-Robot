"""
飞书群机器人 Webhook 通知（卡片格式）。
"""
import logging
import aiohttp
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

BASESCAN_TX = "https://basescan.org/tx/"
BASESCAN_TOKEN = "https://basescan.org/token/"


class FeishuNotifier:
    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url
        self._enabled = bool(webhook_url)

    async def _send_card(self, card: dict) -> None:
        if not self._enabled:
            return
        payload = {"msg_type": "interactive", "card": card}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(self._url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        logger.warning("Feishu webhook failed: %s", await r.text())
        except Exception as e:
            logger.warning("Feishu send error: %s", e)

    # ── 内部构建工具 ──────────────────────────────────────────

    @staticmethod
    def _md(text: str) -> dict:
        return {"tag": "markdown", "content": text}

    @staticmethod
    def _divider() -> dict:
        return {"tag": "hr"}

    @staticmethod
    def _two_col(left_label: str, left_val: str, right_label: str, right_val: str) -> dict:
        return {
            "tag": "column_set",
            "flex_mode": "bisect",
            "background_style": "default",
            "columns": [
                {
                    "tag": "column",
                    "elements": [
                        {"tag": "markdown", "content": f"**{left_label}**\n{left_val}"}
                    ],
                },
                {
                    "tag": "column",
                    "elements": [
                        {"tag": "markdown", "content": f"**{right_label}**\n{right_val}"}
                    ],
                },
            ],
        }

    @staticmethod
    def _button(label: str, url: str) -> dict:
        return {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": "default",
                    "url": url,
                }
            ],
        }

    @staticmethod
    def _buttons(left_label: str, left_url: str, right_label: str, right_url: str) -> dict:
        return {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": left_label},
                    "type": "default",
                    "url": left_url,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": right_label},
                    "type": "default",
                    "url": right_url,
                },
            ],
        }

    # ── 公开通知方法 ──────────────────────────────────────────

    async def notify_swap_alert(
        self,
        source_tx: str,
        symbol_in: str,
        symbol_out: str,
        token_in: str,
        token_out: str,
        amount_display: float,
        amount_unit: str,
        side: str,
        auto_followed: bool,
    ) -> None:
        now_cst = datetime.now(CST).strftime("%m-%d %H:%M")
        view_token = token_out if side == "buy" else token_in

        if auto_followed:
            status_line = "✅ 已自动跟单"
            color = "green" if side == "buy" else "orange"
        else:
            status_line = "⚠️ 未自动跟单，请手动操作"
            color = "yellow"

        amount_str = f"${amount_display:.2f} {amount_unit}" if amount_unit == "USDC" else f"{amount_display:.4f} {amount_unit}"

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"{symbol_in} → {symbol_out}"},
                "template": color,
            },
            "elements": [
                self._two_col("方向", f"{symbol_in} → {symbol_out}", "时间", now_cst),
                self._two_col("金额", amount_str, "跟单状态", status_line),
                self._divider(),
                self._buttons("查看代币", BASESCAN_TOKEN + view_token,
                              "查看原始交易", BASESCAN_TX + source_tx),
            ],
        }
        await self._send_card(card)

    async def notify_trade(
        self,
        source_tx: str,
        symbol_in: str,
        symbol_out: str,
        token_in: str,
        token_out: str,
        amount_display: float,
        amount_unit: str,
        our_tx: str | None,
        dry_run: bool,
        side: str = "buy",
        roi_pct: float | None = None,
        pnl_usd: float | None = None,
        balance_usdc: float = 0.0,
        balance_virtual: float = 0.0,
        balance_eth: float = 0.0,
        skip_reason: str = "",
    ) -> None:
        now_cst = datetime.now(CST).strftime("%m-%d %H:%M")

        amount_str = f"${amount_display:.2f} {amount_unit}" if amount_unit == "USDC" else f"{amount_display:.4f} {amount_unit}"

        if side == "buy":
            color = "green"
            title = f"⚡ {symbol_in} → {symbol_out}"
        else:
            color = "red" if (pnl_usd or 0) < 0 else "orange"
            sign = "+" if (pnl_usd or 0) >= 0 else ""
            title = f"📉 {symbol_in} → {symbol_out}" + (f"  {sign}${pnl_usd:.0f}" if pnl_usd is not None else "")

        mode_tag = "🔵 DRY-RUN" if dry_run else "🟢 LIVE"

        if our_tx:
            status_val = f"✅ {our_tx[:12]}..."
        elif skip_reason:
            status_val = f"❌ {skip_reason}"
        elif dry_run:
            status_val = "📋 模拟"
        else:
            status_val = "❌ 执行失败"

        view_token = token_out if side == "buy" else token_in

        elements = [
            self._two_col("方向", f"{symbol_in} → {symbol_out}", "时间", now_cst),
            self._two_col("金额", amount_str, "模式", mode_tag),
        ]

        if roi_pct is not None and pnl_usd is not None:
            sign = "+" if pnl_usd >= 0 else ""
            elements.append(
                self._two_col("收益率", f"{sign}{roi_pct:.1f}%", "盈亏", f"{sign}${pnl_usd:.2f}")
            )

        if balance_virtual > 0:
            balance_str = f"{balance_virtual:.2f} VIRTUAL"
        else:
            balance_str = f"${balance_usdc:.2f} USDC"
        elements.append(self._two_col("状态", status_val, "余额", balance_str))

        if balance_eth < 0.001:
            elements.append(self._md("⚠️ **ETH 余额不足，请及时补充 gas**"))

        elements.append(self._divider())

        if our_tx:
            elements.append(self._buttons("查看代币", BASESCAN_TOKEN + view_token,
                                          "查看交易", BASESCAN_TX + our_tx))
        else:
            elements.append(self._button("查看代币", BASESCAN_TOKEN + view_token))

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        }
        await self._send_card(card)

    async def notify_take_profit(
        self,
        symbol: str,
        token: str,
        roi_pct: float,
        pnl_usd: float,
    ) -> None:
        now_cst = datetime.now(CST).strftime("%m-%d %H:%M")
        sign = "+" if pnl_usd >= 0 else ""
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🎯 止盈触发  {symbol}  {sign}${pnl_usd:.0f}"},
                "template": "turquoise",
            },
            "elements": [
                self._two_col("代币", symbol, "收益率", f"+{roi_pct:.1f}%"),
                self._two_col("盈亏", f"{sign}${pnl_usd:.2f} USDC", "时间", now_cst),
                self._divider(),
                self._button("查看代币", BASESCAN_TOKEN + token),
            ],
        }
        await self._send_card(card)

    async def notify_hourly_report(
        self,
        balance_usdc: float,
        balance_eth: float,
        balance_virtual: float = 0.0,
        unrealized_pnl: float = 0.0,
        realized_pnl: float = 0.0,
        total_invested: float = 0.0,
        positions: list[dict] = [],
        today_trades: int | None = None,
        today_success: int | None = None,
        today_pnl: float | None = None,
    ) -> None:
        total_roi = (realized_pnl / total_invested * 100) if total_invested > 0 else 0.0
        unr_sign = "+" if unrealized_pnl >= 0 else ""
        rea_sign = "+" if realized_pnl >= 0 else ""
        now_cst = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

        if balance_virtual > 0:
            balance_label = "VIRTUAL 余额"
            balance_str = f"{balance_virtual:.2f} VIRTUAL"
        else:
            balance_label = "USDC 余额"
            balance_str = f"${balance_usdc:.2f}"

        elements = [
            self._two_col(balance_label, balance_str, "ETH 余额", f"{balance_eth:.5f}"),
        ]
        if balance_eth < 0.001:
            elements.append(self._md("⚠️ **ETH 余额不足，请及时补充 gas**"))

        elements.append(
            self._two_col("浮动盈亏", f"{unr_sign}${unrealized_pnl:.2f}", "实际盈亏", f"{rea_sign}${realized_pnl:.2f}")
        )
        elements.append(
            self._two_col("总收益率", f"{'+' if total_roi >= 0 else ''}{total_roi:.1f}%", "总投入", f"${total_invested:.2f}")
        )
        elements.append(self._divider())

        if positions:
            pos_lines = [f"**持仓（{len(positions)} 笔）**"]
            for p in positions:
                sym = p.get("symbol", p.get("token_out", "?")[:10])
                cost = p.get("cost_usd", 0)
                current = p.get("current_usd", 0)
                roi = p.get("roi_pct", 0)
                sign = "+" if roi >= 0 else ""
                pos_lines.append(f"• {sym}  ${cost:.2f} → ${current:.2f}  **{sign}{roi:.1f}%**")
            elements.append(self._md("\n".join(pos_lines)))
        else:
            elements.append(self._md("📦 当前无持仓"))

        if today_trades is not None:
            elements.append(self._divider())
            t_sign = "+" if (today_pnl or 0) >= 0 else ""
            elements.append(self._two_col(
                "今日跟单", f"{today_trades} 笔（成功 {today_success}）",
                "今日盈亏", f"{t_sign}${today_pnl:.2f}" if today_pnl is not None else "—",
            ))

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 定时汇报  {now_cst}"},
                "template": "blue",
            },
            "elements": elements,
        }
        await self._send_card(card)

    async def notify_risk_halt(self, loss_usd: float, limit_usd: float) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🚨 风控触发  亏损 ${loss_usd:.2f} 已达上限"},
                "template": "red",
            },
            "elements": [
                self._two_col("今日亏损", f"${loss_usd:.2f}", "上限", f"${limit_usd:.2f}"),
                self._md("**当日跟单已暂停**"),
            ],
        }
        await self._send_card(card)
