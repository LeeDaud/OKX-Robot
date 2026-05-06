"""
交易执行器：买入/卖出，通过 OKX DEX API 获取报价并签名广播。
"""
import asyncio
import logging
from typing import Optional

from web3 import AsyncWeb3
from eth_account import Account

from src.executor.okx_client import OKXDexClient
from src.db.database import set_tx_pending

logger = logging.getLogger(__name__)

# Base 链常用代币
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDT_BASE = "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2"
VIRTUALS_BASE = "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

ERC20_SHORT_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "approve",
        "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]

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

GAS_LIMIT_MULTIPLIER = 1.2
MAX_UINT256 = 2**256 - 1

# 基础代币配置：address → (decimals, symbol)
BASE_TOKEN_CONFIG = {
    "USDC":    (USDC_BASE, 6),
    "VIRTUAL": (VIRTUALS_BASE, 18),
}


class Trader:
    """交易执行器，封装买入和卖出的完整流程。"""

    def __init__(
        self,
        w3: AsyncWeb3,
        okx: OKXDexClient,
        wallet_addr: str,
        private_key: str,
        base_token: str = "USDC",
        slippage: float = 0.01,
        gas_limit_gwei: float = 50,
        dry_run: bool = True,
    ) -> None:
        self._w3 = w3
        self._okx = okx
        self._wallet = wallet_addr.lower()
        self._pk = private_key
        self._base_token = base_token.upper()
        self._slippage = slippage
        self._gas_limit_gwei = gas_limit_gwei
        self._dry_run = dry_run
        self.last_skip_reason = ""

    @property
    def base_address(self) -> str:
        return BASE_TOKEN_CONFIG[self._base_token][0]

    @property
    def base_decimals(self) -> int:
        return BASE_TOKEN_CONFIG[self._base_token][1]

    # ── 公开 API ─────────────────────────────────────────────────

    async def buy(
        self,
        token_address: str,
        amount_in: int,
        payment_token: str | None = None,
        payment_decimals: int = 6,
        source_tx: str = "",
    ) -> tuple[Optional[str], int]:
        """
        买入代币：用 payment_token 换取目标代币。
        默认用 USDC（6 decimals）支付。

        Args:
            token_address: 目标代币地址
            amount_in: 支付代币数量（raw）
            payment_token: 支付代币地址，默认 USDC
            payment_decimals: 支付代币小数位数，默认 6（USDC）
            source_tx: 触发源交易哈希（用于崩溃恢复追踪）

        Returns:
            (tx_hash or None, filled_amount_raw)
        """
        self.last_skip_reason = ""
        payment_token = payment_token or USDC_BASE

        if amount_in <= 0:
            self.last_skip_reason = "买入金额 <= 0"
            return (None, 0)

        if not await self._check_gas():
            return (None, 0)

        quote = await self._okx.get_quote(
            payment_token, token_address, amount_in, self._slippage
        )
        if quote is None:
            self.last_skip_reason = "OKX 无可用买入报价"
            logger.warning("[SKIP BUY] %s", self.last_skip_reason)
            return (None, 0)

        try:
            self._validate_quote(quote)
        except ValueError as e:
            self.last_skip_reason = f"报价校验不通过: {e}"
            logger.warning("[SKIP BUY] %s", self.last_skip_reason)
            return (None, 0)

        amount_base = amount_in / (10 ** payment_decimals)
        logger.info(
            "[%s] BUY %s with %.4f %s | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            token_address[:10], amount_base,
            "USDC" if payment_token == USDC_BASE.lower() or payment_token == USDC_BASE else "TOKEN",
            quote.get("toTokenAmount", "?"),
        )

        if self._dry_run:
            return (None, 0)

        tx_hash = await self._send_swap(payment_token, token_address, amount_in,
                                         source_tx=source_tx, stage="buy")
        if not tx_hash:
            return (None, 0)

        filled_raw = await self._confirm_and_parse(tx_hash, token_address)
        if filled_raw > 0:
            logger.info("[BUY OK] tx=%s filled=%d", tx_hash[:12], filled_raw)
        else:
            logger.warning("[BUY WARN] tx=%s: no token received", tx_hash[:12])

        return (tx_hash, filled_raw)

    async def sell(
        self,
        token_in: str,
        token_out: str | None = None,
        amount: int = 0,
        source_tx: str = "",
    ) -> Optional[str]:
        """
        卖出代币：将持仓代币换回 base_token。

        Args:
            token_in: 要卖出的代币地址
            token_out: 收款代币地址（None 则使用 base_token）
            amount: 卖出数量(raw)，0 表示卖出全部余额
            source_tx: 触发源交易哈希（用于崩溃恢复追踪）

        Returns:
            tx_hash or None
        """
        self.last_skip_reason = ""
        token_out = token_out or self.base_address
        total_balance = await self._get_token_balance(token_in)

        if total_balance is None or total_balance <= 0:
            self.last_skip_reason = "持仓余额为 0"
            logger.info("[SKIP SELL] %s", self.last_skip_reason)
            return None

        sell_amount = amount if amount > 0 else total_balance
        if sell_amount > total_balance:
            sell_amount = total_balance

        if not await self._check_gas():
            return None

        quote = await self._okx.get_quote(token_in, token_out, sell_amount, self._slippage)
        if quote is None:
            self.last_skip_reason = "OKX 无可用卖出报价"
            logger.warning("[SKIP SELL] %s", self.last_skip_reason)
            return None

        try:
            self._validate_quote(quote)
        except ValueError as e:
            self.last_skip_reason = f"卖出报价校验不通过: {e}"
            logger.warning("[SKIP SELL] %s", self.last_skip_reason)
            return None

        logger.info(
            "[%s] SELL %s -> %s | amount=%d | expected_out=%s",
            "DRY-RUN" if self._dry_run else "LIVE",
            token_in[:10], token_out[:10], sell_amount,
            quote.get("toTokenAmount", "?"),
        )

        if self._dry_run:
            self.last_skip_reason = "模拟运行模式"
            return None

        return await self._send_swap(token_in, token_out, sell_amount,
                                     source_tx=source_tx, stage="sell")

    # ── 内部辅助 ─────────────────────────────────────────────────

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
                self.last_skip_reason = f"Gas 过高 ({gas_gwei:.1f} > {self._gas_limit_gwei} gwei)"
                return False
            return True
        except Exception:
            return True  # 查询失败时不阻断

    def _validate_quote(self, quote: dict) -> None:
        to_token = quote.get("toToken", {}) or {}
        from_token = quote.get("fromToken", {}) or {}

        if to_token.get("isHoneyPot") or from_token.get("isHoneyPot"):
            raise ValueError(f"Honeypot token detected: {quote.get('toTokenAddress', '?')}")

        price_impact = abs(float(quote.get("priceImpactPercent", 0)))
        if price_impact > 5.0:
            raise ValueError(f"Price impact {price_impact:.1f}% exceeds 5% limit")

        tax_rate = float(to_token.get("taxRate", 0))
        if tax_rate > 0.05:
            raise ValueError(f"Token tax rate {tax_rate*100:.1f}% exceeds 5% limit")

    async def _check_and_approve(self, token_addr: str, spender: str, amount_needed: int) -> bool:
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(token_addr),
                abi=ERC20_SHORT_ABI,
            )
            allow = await contract.functions.allowance(
                AsyncWeb3.to_checksum_address(self._wallet),
                AsyncWeb3.to_checksum_address(spender),
            ).call()
            if allow >= amount_needed:
                logger.info("Allowance 充足 (%d >= %d), 跳过 approve", allow, amount_needed)
                return False
            logger.info("Allowance 不足 (%d < %d), 需要 approve", allow, amount_needed)
            return True
        except Exception as e:
            logger.warning("检查 allowance 失败: %s", e)
            return True

    async def _approve_and_wait(self, token_addr: str, spender: str, amount: int) -> bool:
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(token_addr),
                abi=ERC20_SHORT_ABI,
            )
            nonce = await self._w3.eth.get_transaction_count(
                AsyncWeb3.to_checksum_address(self._wallet)
            )
            approve_tx = await contract.functions.approve(
                AsyncWeb3.to_checksum_address(spender), MAX_UINT256
            ).build_transaction({
                "from": AsyncWeb3.to_checksum_address(self._wallet),
                "nonce": nonce,
                "gas": 100000,
                "chainId": 8453,
            })
            signed = Account.sign_transaction(approve_tx, self._pk)
            tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info("Approve 已发送: %s", tx_hash.hex()[:20])

            for _ in range(30):
                receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    status = receipt.get("status")
                    if status == 1:
                        logger.info("Approve 确认成功")
                        return True
                    logger.warning("Approve 链上失败")
                    return False
                await asyncio.sleep(1)

            logger.warning("Approve 30s 未确认")
            return False
        except Exception as e:
            logger.warning("Approve 异常: %s", e)
            return False

    async def _confirm_and_parse(self, tx_hash: str, target_token: str) -> int:
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

    async def _wait_for_receipt(self, tx_hash: str, max_wait: int = 15) -> Optional[dict]:
        from web3.exceptions import TransactionNotFound
        for _ in range(max_wait * 2):
            try:
                receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    return dict(receipt)
            except TransactionNotFound:
                logger.warning("[FILL] Tx %s not found on chain, giving up", tx_hash[:10])
                return None
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return None

    def _parse_received_amount(self, logs: list, target_token: str) -> int:
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
            to_hex = ("0x" + to_addr.hex() if isinstance(to_addr, bytes) else to_addr).lower()
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

    async def _send_swap(self, token_in: str, token_out: str, amount_in: int,
                         source_tx: str = "", stage: str = "swap") -> Optional[str]:
        tx_data = await self._okx.build_swap_tx(
            token_in, token_out, amount_in, self._wallet, self._slippage
        )
        if tx_data is None:
            return None

        tx = tx_data.get("tx", {})

        approve_addr = tx_data.get("dexTokenApproveAddress", "") or tx.get("to", "")
        if approve_addr:
            need_approve = await self._check_and_approve(token_in, approve_addr, amount_in)
            if need_approve:
                logger.info("需要 approve %s -> %s，额度 %d", token_in[:12], approve_addr[:12], amount_in)
                if not await self._approve_and_wait(token_in, approve_addr, amount_in):
                    logger.warning("[SKIP] approve 失败")
                    return None

        for key in ("gas", "gasPrice", "maxPriorityFeePerGas", "value", "maxFeePerGas"):
            val = tx.get(key)
            if val and str(val).isdigit():
                tx[key] = int(val)
        for key in list(tx):
            if isinstance(tx[key], str) and tx[key] == "":
                del tx[key]

        if "maxPriorityFeePerGas" in tx:
            tx["type"] = 2
            if "gasPrice" in tx:
                if "maxFeePerGas" not in tx or tx["maxFeePerGas"] is None:
                    tx["maxFeePerGas"] = int(tx["gasPrice"])
                del tx["gasPrice"]
            elif "maxFeePerGas" not in tx:
                tx["maxFeePerGas"] = tx["maxPriorityFeePerGas"]

        for key in ("minReceiveAmount", "signatureData", "slippagePercent"):
            tx.pop(key, None)

        if "gas" in tx and isinstance(tx["gas"], int):
            tx["gas"] = int(tx["gas"] * GAS_LIMIT_MULTIPLIER)

        checksum_wallet = AsyncWeb3.to_checksum_address(self._wallet)
        tx["from"] = checksum_wallet
        nonce = await self._w3.eth.get_transaction_count(checksum_wallet)
        tx["nonce"] = nonce
        tx["chainId"] = 8453

        signed = Account.sign_transaction(tx, self._pk)
        raw = signed.raw_transaction
        tx_hash = await self._w3.eth.send_raw_transaction(raw)
        tx_hash_hex = tx_hash.hex()

        # 非传播验证（drpc.live 等负载均衡 RPC 可能返回哈希但未广播）
        await asyncio.sleep(1.5)
        try:
            post_nonce = await self._w3.eth.get_transaction_count(
                checksum_wallet, block_identifier="pending"
            )
            if post_nonce <= nonce:
                logger.warning(
                    "[RETRY] Tx %s: nonce %d not consumed, trying fallback broadcast",
                    tx_hash_hex[:12], nonce,
                )
                await asyncio.sleep(1)
                tx_hash = await self._w3.eth.send_raw_transaction(raw)
                tx_hash_hex = tx_hash.hex()
                await asyncio.sleep(2)
                post_nonce = await self._w3.eth.get_transaction_count(
                    checksum_wallet, block_identifier="pending"
                )
                if post_nonce <= nonce:
                    logger.warning("[FAIL] Tx %s: still not propagated after retry", tx_hash_hex[:12])
                else:
                    logger.info("[OK] Tx %s: nonce %d consumed on retry", tx_hash_hex[:12], nonce)
        except Exception as e:
            logger.warning("[PROPAGATE] Nonce check failed, continuing anyway: %s", e)

        # 发交易后立即持久化 tx_hash
        if source_tx:
            try:
                await set_tx_pending(source_tx, tx_hash_hex, stage)
                logger.info("[PERSIST] pending tx saved: source=%s tx=%s stage=%s",
                            source_tx[:12], tx_hash_hex[:12], stage)
            except Exception as e:
                logger.warning("[PERSIST] failed to save pending tx: %s", e)

        return tx_hash_hex
