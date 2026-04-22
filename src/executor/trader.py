"""
跟单执行器：计算跟单金额 → 获取报价 → 签名 → 广播（或 dry-run 打印）。
"""
import logging
from decimal import Decimal
from typing import Optional

from web3 import AsyncWeb3
from eth_account import Account

from src.executor.okx_client import OKXDexClient
from src.monitor.decoder import SwapInfo, USDC_BASE, USDT_BASE, WETH_BASE

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

# USDC/USDT decimals on Base
STABLE_DECIMALS = 6


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

    async def execute(self, swap: SwapInfo) -> Optional[str]:
        """
        执行跟单。返回我方 txhash（dry-run 时返回 None）。
        """
        amount_in = await self._calculate_amount(swap)
        if amount_in is None or amount_in <= 0:
            logger.info("[SKIP] Insufficient balance or amount too small")
            return None

        if not await self._check_gas():
            logger.info("[SKIP] Gas price too high")
            return None

        quote = await self._okx.get_quote(
            swap.token_in, swap.token_out, amount_in, self._slippage
        )
        if quote is None:
            logger.warning("[SKIP] Failed to get quote")
            return None

        to_amount = quote.get("toTokenAmount", "?")
        logger.info(
            "[%s] %s -> %s | amount_in=%d | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            swap.token_in[:10],
            swap.token_out[:10],
            amount_in,
            to_amount,
        )

        if self._dry_run:
            return None

        return await self._send_swap(swap.token_in, swap.token_out, amount_in)

    async def _calculate_amount(self, swap: SwapInfo) -> Optional[int]:
        """
        ratio 模式：空闲 USDC 余额 × ratio
        fixed 模式：固定 trade_fixed_usd（USDC）
        两种模式都受 trade_max_usd 上限约束。
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
        if not await self._check_gas():
            logger.info("[SKIP SELL] Gas too high")
            return None

        quote = await self._okx.get_quote(token_in, token_out, amount, self._slippage)
        if quote is None:
            logger.warning("[SKIP SELL] Failed to get quote")
            return None

        logger.info(
            "[%s] SELL %s -> %s | amount=%d | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            token_in[:10], token_out[:10], amount,
            quote.get("toTokenAmount", "?"),
        )

        if self._dry_run:
            return None

        return await self._send_swap(token_in, token_out, amount)
