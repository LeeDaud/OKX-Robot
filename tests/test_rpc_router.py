import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.rpc.router import RPCRouter


def _make_router(primary_url="https://primary.example.com", fallback_url="https://fallback.example.com"):
    router = RPCRouter.__new__(RPCRouter)
    router._primary = MagicMock()
    router._fallback = MagicMock()
    router.eth = MagicMock()
    return router


# ── RPCRouter._call tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uses_primary_on_success():
    router = RPCRouter.__new__(RPCRouter)
    router._primary = MagicMock()
    router._fallback = MagicMock()

    router._primary.eth.block_number = AsyncMock(return_value=100)
    router._fallback.eth.block_number = AsyncMock(return_value=99)

    result = await router._call("block_number")
    assert result == 100
    router._fallback.eth.block_number.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_on_exception():
    router = RPCRouter.__new__(RPCRouter)
    router._primary = MagicMock()
    router._fallback = MagicMock()

    router._primary.eth.block_number = AsyncMock(side_effect=Exception("403 Forbidden"))
    router._fallback.eth.block_number = AsyncMock(return_value=99)

    result = await router._call("block_number")
    assert result == 99
    router._fallback.eth.block_number.assert_called_once()


@pytest.mark.asyncio
async def test_falls_back_on_timeout():
    router = RPCRouter.__new__(RPCRouter)
    router._primary = MagicMock()
    router._fallback = MagicMock()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    router._primary.eth.get_block = AsyncMock(side_effect=slow)
    router._fallback.eth.get_block = AsyncMock(return_value={"number": 42})

    import src.rpc.router as rpc_module
    original = rpc_module._TIMEOUT
    rpc_module._TIMEOUT = 0.05
    try:
        result = await router._call("get_block", 42, True)
        assert result == {"number": 42}
    finally:
        rpc_module._TIMEOUT = original


@pytest.mark.asyncio
async def test_raises_when_no_fallback():
    router = RPCRouter.__new__(RPCRouter)
    router._primary = MagicMock()
    router._fallback = None

    router._primary.eth.block_number = AsyncMock(side_effect=Exception("503"))

    with pytest.raises(Exception, match="503"):
        await router._call("block_number")


# ── Watcher poll 测试 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_advances_last_block_even_if_logs_fail():
    from src.monitor.watcher import AddressWatcher

    w3 = MagicMock()

    async def fake_block_number():
        return 5

    type(w3.eth).block_number = property(lambda self: fake_block_number())

    watcher = AddressWatcher.__new__(AddressWatcher)
    watcher._w3 = w3
    watcher._last_block = 3
    watcher._targets = ["0xabcd"]
    watcher._targets_set = {"0xabcd"}
    watcher._on_swap = AsyncMock()
    watcher._swap_filter = MagicMock()
    watcher._poll_interval = 0
    watcher._filter = MagicMock()

    # eth_getLogs 总是失败，但 _last_block 仍应前进
    w3.eth.get_logs = AsyncMock(side_effect=Exception("RPC error"))

    await watcher._poll()

    assert watcher._last_block == 5, "last_block must advance even when get_logs fails"
    w3.eth.get_logs.assert_called()
