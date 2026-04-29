import asyncio
import inspect
import logging
import time

from web3 import AsyncWeb3
from web3.exceptions import TransactionNotFound

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0  # seconds before switching to fallback

# Rate-limit backoff: consecutive failures double the wait, capped at 30s
_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 30.0

# Errors that should NOT trigger backoff (final / non-retriable)
_NON_RETRIABLE_ERRORS = (TransactionNotFound,)


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

    Rate-limit aware: consecutive failures apply exponential backoff.
    """

    _consecutive_failures = 0  # class-level default so __new__-based tests work

    def __init__(self, primary_url: str, fallback_url: str = "") -> None:
        self._primary = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(primary_url))
        self._fallback = (
            AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(fallback_url))
            if fallback_url else None
        )
        self.eth = _EthProxy(self)
        self._consecutive_failures = 0

    def _is_non_retriable(self, exc: Exception) -> bool:
        """检查异常是否为终态错误（不重试、不触发退避）。"""
        for err_type in _NON_RETRIABLE_ERRORS:
            if isinstance(exc, err_type):
                return True
        # web3.py v6+ 的 TransactionNotFound 可能以字符串形式抛出
        msg = str(exc)
        if "TransactionNotFound" in msg or "not found" in msg.lower():
            return True
        return False

    async def _call(self, method: str, *args, **kwargs):
        # Apply backoff if we've been hitting rate limits (skip for non-retriable)
        if self._consecutive_failures > 0:
            wait = min(_INITIAL_BACKOFF * (2 ** (self._consecutive_failures - 1)), _MAX_BACKOFF)
            logger.info("RPC backoff: waiting %.1fs (failure #%d)", wait, self._consecutive_failures)
            await asyncio.sleep(wait)

        start = time.monotonic()
        try:
            attr = getattr(self._primary.eth, method)
            if asyncio.iscoroutine(attr):
                result = await asyncio.wait_for(attr, timeout=_TIMEOUT)
            else:
                result = await asyncio.wait_for(
                    attr(*args, **kwargs), timeout=_TIMEOUT,
                )
            self._consecutive_failures = 0
            return result
        except Exception as e:
            elapsed = time.monotonic() - start
            logger.warning(
                "RPC primary failed (%.2fs, %s: %s), switching to fallback",
                elapsed, type(e).__name__, e,
            )

            if self._fallback is None:
                if self._is_non_retriable(e):
                    self._consecutive_failures = 0  # don't penalize for non-retriable
                else:
                    self._consecutive_failures += 1
                raise

        # Small pause before hitting fallback (avoid hammering both endpoints)
        await asyncio.sleep(_INITIAL_BACKOFF)

        try:
            attr = getattr(self._fallback.eth, method)
            if asyncio.iscoroutine(attr):
                result = await attr
            else:
                result = await attr(*args, **kwargs)
            self._consecutive_failures = 0
            return result
        except Exception as e:
            if self._is_non_retriable(e):
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
            logger.warning(
                "RPC fallback also failed (%s: %s), consecutive_failures=%d",
                type(e).__name__, e, self._consecutive_failures,
            )
            raise

    def __getattr__(self, name: str):
        return getattr(self._primary, name)
