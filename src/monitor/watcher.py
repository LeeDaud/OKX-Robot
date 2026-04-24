"""
监控目标地址的链上交易（eth_getLogs Transfer 轮询模式）。
每隔 poll_interval_sec 秒用 eth_getLogs 查目标地址发出的 Transfer 事件，
发现新 swap 后通过回调通知执行层。

相比扫块方案，每次轮询只需 1 个 RPC 请求，消耗降低约 30 倍。
"""
import asyncio
import logging
from typing import Callable, Awaitable, List

from web3 import AsyncWeb3

from src.monitor.decoder import decode_swap_from_logs, SwapInfo, TRANSFER_TOPIC
from src.monitor.filter import TxFilter, SwapFilter

logger = logging.getLogger(__name__)


class AddressWatcher:
    def __init__(
        self,
        w3: AsyncWeb3,
        targets: List[str],
        on_swap: Callable[[SwapInfo], Awaitable[None]],
        swap_filter: SwapFilter,
        poll_interval: float = 60.0,
    ) -> None:
        self._w3 = w3
        self._targets = [addr.lower() for addr in targets]
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

        # 用 eth_getLogs 查目标地址发出的 Transfer 事件
        # topic[1] = from address（左填充 32 字节）
        tx_hashes: set[str] = set()
        for addr in self._targets:
            padded = "0x" + "0" * 24 + addr[2:]
            try:
                logs = await self._w3.eth.get_logs({
                    "fromBlock": self._last_block + 1,
                    "toBlock": current_block,
                    "topics": [TRANSFER_TOPIC, padded],
                })
                for log in logs:
                    tx_hashes.add(log["transactionHash"].hex())
            except Exception as e:
                logger.warning("get_logs failed for %s: %s", addr, e)

        self._last_block = current_block

        for tx_hash in tx_hashes:
            if not self._filter.is_new(tx_hash):
                continue
            await self._resolve_swap(tx_hash)

    async def _resolve_swap(self, tx_hash: str) -> None:
        try:
            receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
            if receipt is None or receipt["status"] != 1:
                return

            from_addr = receipt["from"].lower()
            if from_addr not in self._targets:
                return

            block_number = receipt["blockNumber"]
            logs = [dict(log) for log in receipt.get("logs", [])]

            logger.info("Target tx: %s from %s", tx_hash[:10], from_addr[:10])

            swap = decode_swap_from_logs(tx_hash, from_addr, logs, block_number)
            if swap is None:
                logger.info("[SKIP] %s: no swap detected in logs (%d logs)", tx_hash[:10], len(logs))
                return

            swap = await self._resolve_pool_tokens(swap)
            if swap is None:
                logger.info("[SKIP] %s: failed to resolve pool token addresses", tx_hash[:10])
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
