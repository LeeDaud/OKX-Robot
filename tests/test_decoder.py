"""
测试 swap 解析逻辑（用构造的 log 数据，不需要真实 RPC）。
"""
import pytest
from src.monitor.decoder import (
    decode_swap_from_logs,
    UNISWAP_V3_SWAP_TOPIC,
    UNISWAP_V2_SWAP_TOPIC,
)


def _make_v3_log(pool_addr: str, amount0: int, amount1: int) -> dict:
    def to_signed_bytes(n: int) -> bytes:
        return n.to_bytes(32, "big", signed=True)

    data = (to_signed_bytes(amount0) + to_signed_bytes(amount1) +
            b"\x00" * 96)  # sqrtPriceX96, liquidity, tick
    return {
        "address": pool_addr,
        "topics": [bytes.fromhex(UNISWAP_V3_SWAP_TOPIC)],
        "data": "0x" + data.hex(),
    }


def _make_v2_log(pool_addr: str, a0in: int, a1in: int, a0out: int, a1out: int) -> dict:
    def u256(n: int) -> bytes:
        return n.to_bytes(32, "big")

    data = u256(a0in) + u256(a1in) + u256(a0out) + u256(a1out)
    return {
        "address": pool_addr,
        "topics": [bytes.fromhex(UNISWAP_V2_SWAP_TOPIC)],
        "data": "0x" + data.hex(),
    }


def test_v3_swap_amount0_positive():
    log = _make_v3_log("0xPool1", amount0=1000, amount1=-900)
    result = decode_swap_from_logs("0xabc", "0xfrom", [log], 100)
    assert result is not None
    assert result.amount_in == 1000
    assert result.amount_out == 900
    assert "token0" in result.token_in
    assert "token1" in result.token_out


def test_v3_swap_amount1_positive():
    log = _make_v3_log("0xPool2", amount0=-500, amount1=600)
    result = decode_swap_from_logs("0xdef", "0xfrom", [log], 101)
    assert result is not None
    assert result.amount_in == 600
    assert result.amount_out == 500
    assert "token1" in result.token_in


def test_v2_swap_token0_in():
    log = _make_v2_log("0xPool3", a0in=200, a1in=0, a0out=0, a1out=180)
    result = decode_swap_from_logs("0x111", "0xfrom", [log], 102)
    assert result is not None
    assert result.amount_in == 200
    assert result.amount_out == 180


def test_no_swap_log():
    log = {"address": "0xPool4", "topics": [b"\x00" * 32], "data": "0x"}
    result = decode_swap_from_logs("0x222", "0xfrom", [log], 103)
    assert result is None
