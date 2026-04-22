"""
测试每日亏损风控逻辑。
"""
from src.risk.guard import DailyLossGuard


def test_can_trade_initially():
    guard = DailyLossGuard(limit_usd=50)
    assert guard.can_trade() is True


def test_blocked_after_limit():
    guard = DailyLossGuard(limit_usd=50)
    guard.record_pnl(-30)
    assert guard.can_trade() is True
    guard.record_pnl(-25)
    assert guard.can_trade() is False


def test_profit_does_not_block():
    guard = DailyLossGuard(limit_usd=50)
    guard.record_pnl(100)
    assert guard.can_trade() is True


def test_exact_limit_blocks():
    guard = DailyLossGuard(limit_usd=50)
    guard.record_pnl(-50)
    assert guard.can_trade() is False
