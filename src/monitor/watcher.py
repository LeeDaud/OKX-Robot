"""
监控目标地址的链上交易（eth_getLogs Transfer 轮询模式）。
每隔 poll_interval_sec 秒用 eth_getLogs 双向查询目标地址的 Transfer 事件
（from=target 和 to=target），发现新 swap 后通过回调通知执行层。
同时查两个方向确保不遗漏 Virtuals 等平台的交易。
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
        poll_interval: float = 30.0,
    ) -> None:
        self._w3 = w3
        self._targets = [addr.lower() for addr in targets]
        self._targets_set: set[str] = set(self._targets)
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

        from_block = self._last_block + 1
        to_block = current_block
        tx_hashes: set[str] = set()

        for addr in self._targets:
            padded = "0x" + "0" * 24 + addr[2:]

            # 方向 1：目标地址发出的 Transfer（发现卖出/转账）
            try:
                logs = await self._w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "topics": [TRANSFER_TOPIC, padded],
                })
                for log in logs:
                    tx_hashes.add(log["transactionHash"].hex())
            except Exception as e:
                logger.warning("get_logs(from) failed for %s: %s", addr, e)

            # 方向 2：目标地址收到的 Transfer（发现买入/接收）
            try:
                logs = await self._w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "topics": [TRANSFER_TOPIC, None, padded],
                })
                for log in logs:
                    tx_hashes.add(log["transactionHash"].hex())
            except Exception as e:
                logger.warning("get_logs(to) failed for %s: %s", addr, e)

        self._last_block = current_block

        # 过滤已处理 + 并行解析
        new_hashes = [h for h in tx_hashes if self._filter.is_new(h)]
        if new_hashes:
            await asyncio.gather(*[self._resolve_swap(h) for h in new_hashes])

    async def _resolve_swap(self, tx_hash: str) -> None:
        try:
            receipt, tx = await asyncio.gather(
                self._w3.eth.get_transaction_receipt(tx_hash),
                self._w3.eth.get_transaction(tx_hash),
            )
            if receipt is None or receipt["status"] != 1:
                return

            logs = [dict(log) for log in receipt.get("logs", [])]

            # 从 Transfer 事件中找目标地址（不依赖 receipt.from）
            # 支持 relayer、合约钱包等场景
            from_addr = self._find_target_in_logs(logs)
            if from_addr is None:
                return

            block_number = receipt["blockNumber"]
            tx_value = tx.get("value", 0) if tx else 0
            if isinstance(tx_value, bytes):
                tx_value = int.from_bytes(tx_value, "big")

            logger.info("Target tx: %s from %s", tx_hash[:10], from_addr[:10])

            swap = decode_swap_from_logs(tx_hash, from_addr, logs, block_number, tx_value)
            if swap is None:
                logger.info("[SKIP] %s: no swap detected in logs (%d logs, from_addr=%s)",
                            tx_hash[:10], len(logs), from_addr[:12])
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

    def _find_target_in_logs(self, logs: list) -> str | None:
        """从 Transfer 事件中找涉及的目标地址。
        不受 receipt.from 限制，支持中继器/合约钱包场景。
        """
        transfer_topic = TRANSFER_TOPIC.lstrip("0x").lower()

        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            raw = topics[0]
            topic0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
            if topic0 != transfer_topic:
                continue

            from_ = self._addr_from_topic(topics[1])
            to_ = self._addr_from_topic(topics[2])

            if from_ in self._targets_set:
                return from_
            if to_ in self._targets_set:
                return to_

        return None

    @staticmethod
    def _addr_from_topic(topic) -> str:
        raw = topic.hex() if isinstance(topic, bytes) else topic
        return "0x" + raw[-40:].lower()

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
