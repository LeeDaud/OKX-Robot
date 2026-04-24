import asyncio
import inspect
import logging
import time

from web3 import AsyncWeb3

logger = logging.getLogger(__name__)

_TIMEOUT = 3.0  # seconds before switching to fallback


class _EthProxy:
    def __init__(self, router: "RPCRouter") -> None:
        self._router = router

    def __getattr__(self, name: str):
        primary_attr = getattr(self._router._primary.eth, name)

        if not callable(primary_attr):
            return primary_attr

        if inspect.iscoroutinefunction(primary_attr):
            async def _with_fallback(*args, **kwargs):
                return await self._router._call(name, *args, **kwargs)
            return _with_fallback

        # sync callable (e.g. .contract()) — delegate directly to primary
        return primary_attr


class RPCRouter:
    """
    Wraps two AsyncWeb3 instances. All w3.eth.* async calls try primary first;
    on exception or timeout > _TIMEOUT seconds, retries on fallback.
    """

    def __init__(self, primary_url: str, fallback_url: str = "") -> None:
        self._primary = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(primary_url))
        self._fallback = (
            AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(fallback_url))
            if fallback_url else None
        )
        self.eth = _EthProxy(self)

    async def _call(self, method: str, *args, **kwargs):
        start = time.monotonic()
        try:
            return await asyncio.wait_for(
                getattr(self._primary.eth, method)(*args, **kwargs),
                timeout=_TIMEOUT,
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            if self._fallback is None:
                raise
            logger.warning(
                "RPC primary failed (%.2fs, %s: %s), switching to fallback",
                elapsed, type(e).__name__, e,
            )
            return await getattr(self._fallback.eth, method)(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._primary, name)
