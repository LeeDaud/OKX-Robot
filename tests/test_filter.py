"""
测试 TxFilter 幂等性。
"""
from src.monitor.filter import TxFilter


def test_new_tx_passes():
    f = TxFilter()
    assert f.is_new("0xabc") is True


def test_duplicate_tx_blocked():
    f = TxFilter()
    f.is_new("0xabc")
    assert f.is_new("0xabc") is False


def test_different_txs_pass():
    f = TxFilter()
    assert f.is_new("0x001") is True
    assert f.is_new("0x002") is True
