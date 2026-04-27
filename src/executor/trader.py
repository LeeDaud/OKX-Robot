"""
跟单执行器：计算跟单金额 → 获取报价 → 签名 → 广播（或 dry-run 打印）。
"""
import logging
from decimal import Decimal
from typing import Optional

from web3 import AsyncWeb3
from eth_account import Account

from src.executor.okx_client import OKXDexClient
from src.monitor.decoder import SwapInfo, USDC_BASE, USDT_BASE, WETH_BASE, VIRTUALS_BASE, TRANSFER_TOPIC

logger = logging.getLogger(__name__)

ERC20_BALANCE_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]

class Trader:
    def __init__(
        self,
        w3: AsyncWeb3,
        okx: OKXDexClient,
        wallet_addr: str,
        private_key: str,
        trade_mode: str,
        trade_ratio: float,
        trade_fixed_usd: float,
        trade_max_usd: float,
        slippage: float,
        gas_limit_gwei: float,
        dry_run: bool,
    ) -> None:
        self._w3 = w3
        self._okx = okx
        self._wallet = wallet_addr.lower()
        self._pk = private_key
        self._mode = trade_mode
        self._ratio = trade_ratio
        self._fixed_usd = trade_fixed_usd
        self._max_usd = trade_max_usd
        self._slippage = slippage
        self._gas_limit_gwei = gas_limit_gwei
        self._dry_run = dry_run
        self.last_skip_reason = ""

    async def execute(self, swap: SwapInfo) -> tuple[Optional[str], float, int]:
        """
        执行跟单。返回 (txhash, amount_usd, filled_amount_raw)。
        - 跳过时返回 (None, 0.0, 0)
        - dry-run 时返回 (None, 计算金额, 0)
        - 成功时返回 (txhash, 实际跟单金额, 实际收到 token 原始数量)
        买入时统一用 USDC 支付，卖出时沿用 swap.token_in。
        """
        self.last_skip_reason = ""
        is_buy = swap.token_out.lower() not in {
            USDC_BASE.lower(), USDT_BASE.lower(), VIRTUALS_BASE.lower(),
        }
        payment_token = USDC_BASE if is_buy else swap.token_in
        target_token = swap.token_out if is_buy else USDC_BASE

        amount_in = await self._calculate_amount()
        if amount_in is None or amount_in <= 0:
            self.last_skip_reason = "USDC 余额不足或金额太小"
            logger.info("[SKIP] %s", self.last_skip_reason)
            return (None, 0.0, 0)

        if not await self._check_gas():
            self.last_skip_reason = f"Gas 过高（>{self._gas_limit_gwei} gwei）"
            logger.info("[SKIP] %s", self.last_skip_reason)
            return (None, 0.0, 0)

        quote = await self._okx.get_quote(
            payment_token, target_token, amount_in, self._slippage
        )
        if quote is None:
            self.last_skip_reason = "OKX 无可用买入报价路由"
            logger.warning("[SKIP] %s", self.last_skip_reason)
            return (None, 0.0, 0)

        amount_usd = amount_in / 1e6
        to_amount = quote.get("toTokenAmount", "?")
        logger.info(
            "[%s] %s -> %s | amount_in=%d (%.2f USDC) | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            payment_token[:10],
            target_token[:10],
            amount_in, amount_usd,
            to_amount,
        )

        if self._dry_run:
            return (None, amount_usd, 0)

        tx_hash = await self._send_swap(payment_token, target_token, amount_in)
        filled_raw = 0
        if tx_hash:
            filled_raw = await self._confirm_and_parse(tx_hash, target_token)
        return (tx_hash, amount_usd, filled_raw)

    async def _calculate_amount(self) -> Optional[int]:
        """
        ratio 模式：空闲 USDC 余额 × ratio
        fixed 模式：固定 trade_fixed_usd
        两种模式都受 trade_max_usd 上限约束。
        统一使用 USDC 计算。
        """
        balance = await self._get_token_balance(USDC_BASE)
        if balance is None:
            return None

        if self._mode == "fixed":
            amount = int(Decimal(str(self._fixed_usd)) * Decimal("1e6"))
        else:
            amount = int(Decimal(str(balance)) * Decimal(str(self._ratio)))

        # 单笔上限
        if self._max_usd > 0:
            cap = int(Decimal(str(self._max_usd)) * Decimal("1e6"))
            amount = min(amount, cap)

        # 不能超过实际余额
        amount = min(amount, balance)

        return amount if amount > 0 else None

    async def _get_token_balance(self, token_addr: str) -> Optional[int]:
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(token_addr),
                abi=ERC20_BALANCE_ABI,
            )
            return await contract.functions.balanceOf(
                AsyncWeb3.to_checksum_address(self._wallet)
            ).call()
        except Exception as e:
            logger.warning("Failed to get balance: %s", e)
            return None

    async def _check_gas(self) -> bool:
        try:
            gas_price = await self._w3.eth.gas_price
            gas_gwei = gas_price / 1e9
            if gas_gwei > self._gas_limit_gwei:
                logger.warning("Gas too high: %.1f gwei > limit %.1f", gas_gwei, self._gas_limit_gwei)
                return False
            return True
        except Exception:
            return True  # 查询失败时不阻断

    async def _confirm_and_parse(self, tx_hash: str, target_token: str) -> int:
        """等待交易确认，返回实际收到的 target_token 数量（raw）。"""
        receipt = await self._wait_for_receipt(tx_hash)
        if receipt is None:
            logger.warning("[FILL] Receipt not found for %s", tx_hash[:10])
            return 0
        if receipt.get("status") != 1:
            logger.warning("[FILL] Tx failed on-chain: %s", tx_hash[:10])
            return 0
        filled = self._parse_received_amount(receipt.get("logs", []), target_token)
        if filled > 0:
            logger.info("[FILL] %s received %s raw of %s", tx_hash[:10], filled, target_token[:10])
        else:
            logger.warning("[FILL] %s no Transfer events for %s to wallet", tx_hash[:10], target_token[:10])
        return filled

    async def _wait_for_receipt(self, tx_hash: str, max_wait: int = 30) -> Optional[dict]:
        """轮询等待交易收据，最多 max_wait 秒。"""
        for _ in range(max_wait * 2):
            try:
                receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    return dict(receipt)
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return None

    def _parse_received_amount(self, logs: list, target_token: str) -> int:
        """从 receipt logs 中解析 target_token 转入本钱包的总量。"""
        target_lower = target_token.lower()
        wallet_padded = "0x" + "0" * 24 + self._wallet[2:]
        transfer_topic = TRANSFER_TOPIC.lstrip("0x").lower()

        total = 0
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            raw = topics[0]
            topic0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
            if topic0 != transfer_topic:
                continue
            if log["address"].lower() != target_lower:
                continue
            to_addr = topics[2]
            to_hex = (to_addr.hex() if isinstance(to_addr, bytes) else to_addr).lower()
            if to_hex != wallet_padded:
                continue
            data = log.get("data", b"")
            if isinstance(data, bytes):
                data_bytes = data
            else:
                data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)
            if len(data_bytes) < 32:
                continue
            total += int.from_bytes(data_bytes[:32], "big")
        return total

    async def _send_swap(self, token_in: str, token_out: str, amount_in: int) -> Optional[str]:
        tx_data = await self._okx.build_swap_tx(
            token_in, token_out, amount_in, self._wallet, self._slippage
        )
        if tx_data is None:
            return None

        tx = tx_data.get("tx", {})
        nonce = await self._w3.eth.get_transaction_count(
            AsyncWeb3.to_checksum_address(self._wallet)
        )
        tx["nonce"] = nonce
        tx["chainId"] = 8453

        signed = Account.sign_transaction(tx, self._pk)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    async def sell(self, token_in: str, token_out: str, amount: int) -> Optional[str]:
        """止盈卖出：将持仓代币换回稳定币。"""
        self.last_skip_reason = ""
        if not await self._check_gas():
            self.last_skip_reason = f"Gas 过高（>{self._gas_limit_gwei} gwei）"
            logger.info("[SKIP SELL] %s", self.last_skip_reason)
            return None

        quote = await self._okx.get_quote(token_in, token_out, amount, self._slippage)
        if quote is None:
            self.last_skip_reason = "OKX 无可用卖出报价路由"
            logger.warning("[SKIP SELL] %s", self.last_skip_reason)
            return None

        logger.info(
            "[%s] SELL %s -> %s | amount=%d | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            token_in[:10], token_out[:10], amount,
            quote.get("toTokenAmount", "?"),
        )

        if self._dry_run:
            self.last_skip_reason = "模拟运行模式"
            return None

        return await self._send_swap(token_in, token_out, amount)
