"""
测试飞书卡片通知格式是否正确构建（不发网络请求）。
"""
import pytest
from unittest.mock import AsyncMock, patch
from src.notify.feishu import FeishuNotifier


@pytest.fixture
def notifier():
    n = FeishuNotifier("https://fake.webhook/")
    return n


def _capture_card(notifier: FeishuNotifier):
    """返回 _send_card 的 mock，调用后可从 .call_args 取到 card。"""
    m = AsyncMock()
    notifier._send_card = m
    return m


# ── notify_trade ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_buy_card_structure(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_trade(
        source_tx="0xabc123",
        symbol_in="USDC", symbol_out="VIRTUAL",
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0xdeadbeef",
        amount_in_usd=50.0,
        our_tx="0xourtx123456",
        dry_run=False,
        side="buy",
    )
    card = mock.call_args[0][0]
    assert card["msg_type"] if "msg_type" in card else True  # card itself
    assert card["header"]["template"] == "green"
    assert "买入" in card["header"]["title"]["content"]
    assert "VIRTUAL" in card["header"]["title"]["content"]
    # 有查看代币按钮
    buttons = [e for e in card["elements"] if e.get("tag") == "action"]
    assert len(buttons) >= 1
    assert "0xdeadbeef" in buttons[0]["actions"][0]["url"]


@pytest.mark.asyncio
async def test_trade_sell_profit_card(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_trade(
        source_tx="0xabc123",
        symbol_in="VIRTUAL", symbol_out="USDC",
        token_in="0xdeadbeef",
        token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        amount_in_usd=80.0,
        our_tx=None,
        dry_run=True,
        side="sell",
        roi_pct=60.0,
        pnl_usd=30.0,
    )
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "orange"
    assert "卖出" in card["header"]["title"]["content"]
    # 有收益率列
    cols = [e for e in card["elements"] if e.get("tag") == "column_set"]
    col_texts = str(cols)
    assert "60.0%" in col_texts or "60" in col_texts


@pytest.mark.asyncio
async def test_trade_sell_loss_card(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_trade(
        source_tx="0xabc",
        symbol_in="VIRTUAL", symbol_out="USDC",
        token_in="0xdeadbeef",
        token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        amount_in_usd=50.0,
        our_tx=None,
        dry_run=False,
        side="sell",
        roi_pct=-20.0,
        pnl_usd=-10.0,
    )
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "red"


@pytest.mark.asyncio
async def test_trade_eth_warning(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_trade(
        source_tx="0xabc",
        symbol_in="USDC", symbol_out="TOKEN",
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0xdeadbeef",
        amount_in_usd=10.0,
        our_tx=None,
        dry_run=True,
        balance_eth=0.001,  # 低于 0.003 触发警告
    )
    card = mock.call_args[0][0]
    texts = str(card["elements"])
    assert "ETH" in texts and "不足" in texts


# ── notify_take_profit ────────────────────────────────────────

@pytest.mark.asyncio
async def test_take_profit_card(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_take_profit("VIRTUAL", "0xdeadbeef", 35.0, 17.5)
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "turquoise"
    assert "止盈" in card["header"]["title"]["content"]
    assert "VIRTUAL" in card["header"]["title"]["content"]


# ── notify_hourly_report ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hourly_report_with_positions(notifier):
    mock = _capture_card(notifier)
    positions = [
        {"symbol": "VIRTUAL", "token_out": "0xdeadbeef", "cost_usd": 50.0, "current_usd": 65.0, "roi_pct": 30.0},
        {"symbol": "AIXBT", "token_out": "0xfeed", "cost_usd": 30.0, "current_usd": 27.0, "roi_pct": -10.0},
    ]
    await notifier.notify_hourly_report(
        balance_usdc=200.0,
        balance_eth=0.01,
        unrealized_pnl=12.0,
        realized_pnl=5.0,
        total_invested=100.0,
        positions=positions,
    )
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "blue"
    assert "定时汇报" in card["header"]["title"]["content"]
    texts = str(card["elements"])
    assert "VIRTUAL" in texts
    assert "AIXBT" in texts
    assert "30.0%" in texts


@pytest.mark.asyncio
async def test_hourly_report_no_positions(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_hourly_report(
        balance_usdc=100.0, balance_eth=0.005,
        unrealized_pnl=0.0, realized_pnl=0.0,
        total_invested=0.0, positions=[],
    )
    card = mock.call_args[0][0]
    texts = str(card["elements"])
    assert "无持仓" in texts


# ── notify_daily_report ───────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_stats_in_hourly_report(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_hourly_report(
        balance_usdc=200.0, balance_eth=0.01,
        unrealized_pnl=5.0, realized_pnl=25.5,
        total_invested=100.0, positions=[],
        today_trades=10, today_success=8, today_pnl=25.5,
    )
    card = mock.call_args[0][0]
    texts = str(card["elements"])
    assert "10" in texts and "8" in texts
    assert "25.50" in texts


@pytest.mark.asyncio
async def test_no_daily_stats_when_not_provided(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_hourly_report(
        balance_usdc=100.0, balance_eth=0.005,
        unrealized_pnl=0.0, realized_pnl=-8.0,
        total_invested=50.0, positions=[],
    )
    card = mock.call_args[0][0]
    texts = str(card["elements"])
    assert "今日跟单" not in texts


# ── notify_swap_alert ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_swap_alert_auto_followed(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_swap_alert(
        "0xabc", "USDC", "VIRTUAL",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "0xdeadbeef",
        50.0, "buy", auto_followed=True,
    )
    card = mock.call_args[0][0]
    assert "买入" in card["header"]["title"]["content"]
    assert "VIRTUAL" in card["header"]["title"]["content"]
    texts = str(card["elements"])
    assert "已自动跟单" in texts


@pytest.mark.asyncio
async def test_swap_alert_not_followed(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_swap_alert(
        "0xabc", "USDC", "VIRTUAL",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "0xdeadbeef",
        50.0, "buy", auto_followed=False,
    )
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "yellow"
    texts = str(card["elements"])
    assert "手动" in texts


# ── notify_risk_halt ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_halt_card(notifier):
    mock = _capture_card(notifier)
    await notifier.notify_risk_halt(12.5, 10.0)
    card = mock.call_args[0][0]
    assert card["header"]["template"] == "red"
    assert "风控" in card["header"]["title"]["content"]
    texts = str(card["elements"])
    assert "12.50" in texts
    assert "10.00" in texts


# ── disabled notifier ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_notifier_does_not_send():
    n = FeishuNotifier("")
    assert not n._enabled
    # _send_card 内部有 enabled 检查，mock aiohttp 确认不发网络请求
    with patch("aiohttp.ClientSession") as mock_session:
        await n.notify_trade("0x", "A", "B", "0x1", "0x2", 10.0, None, True)
        mock_session.assert_not_called()
