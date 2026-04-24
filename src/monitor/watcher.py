"""
监控目标地址的链上交易（confirmed tx 轮询模式）。
每隔 poll_interval_sec 秒查询目标地址最新区块的交易，
发现新 swap 后通过回调通知执行层。
"""
import asyncio
import logging
from typing import Callable, Awaitable, List

from web3 import AsyncWeb3

from src.monitor.decoder import decode_swap_from_logs, is_swap_tx, SwapInfo
from src.monitor.filter import TxFilter, SwapFilter

logger = logging.getLogger(__name__)


class AddressWatcher:
    def __init__(
        self,
        w3: AsyncWeb3,
        targets: List[str],
        on_swap: Callable[[SwapInfo], Awaitable[None]],
        swap_filter: SwapFilter,
        poll_interval: float = 2.0,
    ) -> None:
        self._w3 = w3
        self._targets = {addr.lower() for addr in targets}
        self._on_swap = on_swap
        self._swap_filter = swap_filter
        self._poll_interval = poll_interval
        self._filter = TxFilter()
        self._last_block: int = 0
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._last_block = await self._w3.eth.block_number
        logger.info("Watcher started, monitoring %d address(es), from block %d",
                    len(self._targets), self._last_block)
        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.warning("Poll error: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        current_block = await self._w3.eth.block_number
        if current_block <= self._last_block:
            return

        for block_num in range(self._last_block + 1, current_block + 1):
            try:
                await self._process_block(block_num)
            except Exception as e:
                logger.warning("Failed to process block %d: %s", block_num, e)
            await asyncio.sleep(0.1)

        self._last_block = current_block

    async def _process_block(self, block_number: int) -> None:
        block = await self._w3.eth.get_block(block_number, full_transactions=True)
        for tx in block.get("transactions", []):
            from_addr = tx.get("from", "").lower()
            if from_addr not in self._targets:
                continue

            tx_hash = tx["hash"].hex()
            if not self._filter.is_new(tx_hash):
                continue

            to_addr = (tx.get("to") or "").lower()
            if is_swap_tx(tx):
                logger.info("Target tx (known router): %s -> %s", tx_hash[:10], to_addr)
            else:
                logger.info("Target tx (unknown router, checking logs): %s -> %s", tx_hash[:10], to_addr)

            await self._resolve_swap(tx_hash, from_addr, block_number)

    async def _resolve_swap(
        self, tx_hash: str, from_addr: str, block_number: int
    ) -> None:
        try:
            receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
            if receipt is None or receipt["status"] != 1:
                return

            logs = [dict(log) for log in receipt.get("logs", [])]
            swap = decode_swap_from_logs(tx_hash, from_addr, logs, block_number)
            if swap is None:
                return

            swap = await self._resolve_pool_tokens(swap)
            if swap is None:
                return

            allowed, reason = self._swap_filter.allow(swap.token_in, swap.token_out, swap.amount_in)
            if not allowed:
                logger.info("[FILTERED] %s: %s", tx_hash[:10], reason)
                return

            logger.info("Swap detected: %s | %s -> %s | amount_in=%d",
                        tx_hash[:10], swap.token_in[:10], swap.token_out[:10], swap.amount_in)
            await self._on_swap(swap)
        except Exception as e:
            logger.warning("Failed to resolve swap %s: %s", tx_hash, e)

    async def _resolve_pool_tokens(self, swap: SwapInfo) -> SwapInfo | None:
        token_in = swap.token_in
        token_out = swap.token_out

        if token_in.startswith("pool:"):
            _, pool_addr, slot = token_in.split(":")
            token_in = await self._get_pool_token(pool_addr, slot)
        if token_out.startswith("pool:"):
            _, pool_addr, slot = token_out.split(":")
            token_out = await self._get_pool_token(pool_addr, slot)

        if not token_in or not token_out:
            return None

        swap.token_in = token_in.lower()
        swap.token_out = token_out.lower()
        return swap

    async def _get_pool_token(self, pool_addr: str, slot: str) -> str | None:
        abi = [
            {"name": "token0", "type": "function", "inputs": [],
             "outputs": [{"type": "address"}], "stateMutability": "view"},
            {"name": "token1", "type": "function", "inputs": [],
             "outputs": [{"type": "address"}], "stateMutability": "view"},
        ]
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(pool_addr), abi=abi
            )
            if slot == "token0":
                return (await contract.functions.token0().call()).lower()
            else:
                return (await contract.functions.token1().call()).lower()
        except Exception:
            return None
