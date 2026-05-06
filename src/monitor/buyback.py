"""
回购事件监控：监听链上 ERC-20 Transfer 到回购地址，触发卖出。
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

from web3 import AsyncWeb3

from src.rpc.router import RPCRouter

logger = logging.getLogger(__name__)

# ERC-20 Transfer 事件的 topic0
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@dataclass
class BuybackEvent:
    """回购检测到的事件。"""
    buyback_addr: str      # 回购地址
    token_addr: str        # 被买入的代币地址
    amount: int            # 转入数量(raw)
    tx_hash: str           # 触发交易的哈希


class BuybackMonitor:
    """
    监控指定回购地址的 ERC-20 Transfer 转入事件。
    当监控地址收到代币时，触发 on_buyback 回调。

    此模块同时监控多个 (buyback_address, token_address) 配对。
    每次轮询会查询每个 token 的 Transfer 事件，过滤 to=buyback_address。
    """

    def __init__(
        self,
        w3: RPCRouter,
        watch_pairs: dict[str, str],   # buyback_addr → token_addr
        poll_interval: float = 10,
        on_buyback: Callable[[BuybackEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._w3 = w3
        # 转成 checksum 地址，web3.py 要求 checksum 格式
        self._pairs = [
            (AsyncWeb3.to_checksum_address(addr), AsyncWeb3.to_checksum_address(token))
            for addr, token in watch_pairs.items()
        ]
        self._interval = poll_interval
        self._on_buyback = on_buyback
        self._running = False
        # 记录每个 token 最后处理的区块，避免重复检测
        self._last_block: dict[str, int] = {}

    async def start(self) -> None:
        self._running = True
        logger.info("BuybackMonitor started: %d pairs", len(self._pairs))

        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.warning("BuybackMonitor poll error: %s", e)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        if not self._pairs:
            return

        current_block = await self._w3.eth.block_number

        for buyback_addr, token_addr in self._pairs:
            await self._check_token(buyback_addr, token_addr, current_block)

    async def _check_token(
        self, buyback_addr: str, token_addr: str, current_block: int
    ) -> None:
        from_block = self._last_block.get(token_addr, current_block - 100)
        if from_block >= current_block:
            return

        try:
            logs = await self._w3.eth.get_logs({
                "address": token_addr,
                "fromBlock": hex(from_block),
                "toBlock": hex(current_block),
                "topics": [
                    TRANSFER_TOPIC,
                    None,  # from (any)
                    "0x000000000000000000000000" + buyback_addr[2:],  # to = buyback addr (padded)
                ],
            })
        except Exception as e:
            logger.warning("eth_getLogs failed for %s: %s", token_addr[:10], e)
            return

        self._last_block[token_addr] = current_block

        for log in logs:
            tx_hash = (log.get("transactionHash") or b"").hex()
            if not tx_hash:
                continue

            data = log.get("data", "0x0")
            if isinstance(data, bytes):
                data_bytes = data
            else:
                data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)

            amount = int.from_bytes(data_bytes[:32], "big") if len(data_bytes) >= 32 else 0
            if amount <= 0:
                continue

            event = BuybackEvent(
                buyback_addr=buyback_addr,
                token_addr=token_addr,
                amount=amount,
                tx_hash=tx_hash,
            )
            logger.info(
                "Buyback detected: %s -> %s | amount=%d tx=%s",
                buyback_addr[:8], token_addr[:10], amount, tx_hash[:10],
            )

            if self._on_buyback:
                try:
                    await self._on_buyback(event)
                except Exception as e:
                    logger.error("on_buyback callback failed: %s", e)
