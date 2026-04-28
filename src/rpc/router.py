import asyncio
import inspect
import logging
import time

from web3 import AsyncWeb3

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0  # seconds before switching to fallback


class _EthProxy:
    def __init__(self, router: "RPCRouter") -> None:
        self._router = router

    # ── async property wrappers (go through _call for fallback) ──

    @property
    def block_number(self):
        return self._router._call("block_number")

    @property
    def gas_price(self):
        return self._router._call("gas_price")

    @property
    def chain_id(self):
        return self._router._call("chain_id")

    @property
    def max_priority_fee(self):
        return self._router._call("max_priority_fee")

    @property
    def syncing(self):
        return self._router._call("syncing")

    # ── contract() — bind created contracts to the router for fallback ──

    def contract(self, address, abi, **kwargs):
        contract = self._router._primary.eth.contract(
            address=address, abi=abi, **kwargs
        )
        # Patch w3 so contract function calls go through _EthProxy → _call → fallback
        contract.w3 = self._router  # type: ignore[assignment]
        return contract

    def __getattr__(self, name: str):
        primary_attr = getattr(self._router._primary.eth, name)

        if not callable(primary_attr):
            return primary_attr

        if inspect.iscoroutinefunction(primary_attr):
            async def _with_fallback(*args, **kwargs):
                return await self._router._call(name, *args, **kwargs)
            return _with_fallback

        # sync callable — delegate directly to primary
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
            attr = getattr(self._primary.eth, method)
            # Handle async properties that return coroutines directly
            if asyncio.iscoroutine(attr):
                return await asyncio.wait_for(attr, timeout=_TIMEOUT)
            return await asyncio.wait_for(
                attr(*args, **kwargs), timeout=_TIMEOUT,
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            if self._fallback is None:
                raise
            logger.warning(
                "RPC primary failed (%.2fs, %s: %s), switching to fallback",
                elapsed, type(e).__name__, e,
            )
            attr = getattr(self._fallback.eth, method)
            if asyncio.iscoroutine(attr):
                return await attr
            return await attr(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._primary, name)
