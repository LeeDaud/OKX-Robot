"""
跟单执行器：计算跟单金额 → 获取报价 → 签名 → 广播（或 dry-run 打印）。
"""
import asyncio
import logging
from decimal import Decimal
from typing import Optional

from web3 import AsyncWeb3
from eth_account import Account

from src.executor.okx_client import OKXDexClient
from src.monitor.decoder import SwapInfo, USDC_BASE, USDT_BASE, VIRTUALS_BASE, TRANSFER_TOPIC
from src.db.database import set_tx_pending

logger = logging.getLogger(__name__)

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

STABLE_TOKENS = {USDC_BASE.lower(), USDT_BASE.lower(), VIRTUALS_BASE.lower()}


class Trader:
    def __init__(
        self,
        w3: AsyncWeb3,
        okx: OKXDexClient,
        wallet_addr: str,
        private_key: str,
        base_token: str,
        trade_mode: str,
        trade_ratio: float,
        trade_fixed_usd: float,
        trade_max_usd: float,
        trade_fixed_virtuals: float,
        slippage: float,
        gas_limit_gwei: float,
        dry_run: bool,
        trade_retry: int = 0,
    ) -> None:
        self._w3 = w3
        self._okx = okx
        self._wallet = wallet_addr.lower()
        self._pk = private_key
        self._base_token = base_token.upper()
        self._mode = trade_mode
        self._ratio = trade_ratio
        self._fixed_usd = trade_fixed_usd
        self._max_usd = trade_max_usd
        self._fixed_virtuals = trade_fixed_virtuals
        self._slippage = slippage
        self._gas_limit_gwei = gas_limit_gwei
        self._dry_run = dry_run
        self._trade_retry = trade_retry
        self.last_skip_reason = ""

    @property
    def _base_address(self) -> str:
        return VIRTUALS_BASE if self._base_token == "VIRTUAL" else USDC_BASE

    @property
    def _base_decimals(self) -> int:
        return 18 if self._base_token == "VIRTUAL" else 6

    async def execute(self, swap: SwapInfo, source_tx: str = "") -> tuple[Optional[str], float, int]:
        """
        执行跟单。返回 (txhash, amount_usd, filled_amount_raw)。
        - 跳过时返回 (None, 0.0, 0)
        - dry-run 时返回 (None, 计算金额, 0)
        - 成功时返回 (txhash, 实际跟单金额, 实际收到 token 原始数量)
        支付代币由 base_token 配置决定（VIRTUAL 或 USDC）。
        source_tx 用于发交易后立即持久化 tx_hash，支持 crash 恢复。
        """
        self.last_skip_reason = ""

        if self._mode == "monitor":
            self.last_skip_reason = "监测模式，跳过跟单"
            logger.info("[SKIP] %s", self.last_skip_reason)
            return (None, 0.0, 0)

        is_buy = swap.token_out.lower() not in STABLE_TOKENS
        payment_token = self._base_address if is_buy else swap.token_in
        target_token = swap.token_out if is_buy else self._base_address

        amount_in = await self._calculate_amount()
        if amount_in is None or amount_in <= 0:
            self.last_skip_reason = f"{self._base_token} 余额不足或金额太小"
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

        # 报价安全校验
        try:
            self._validate_quote(quote)
        except ValueError as e:
            self.last_skip_reason = f"报价校验不通过: {e}"
            logger.warning("[SKIP] %s", self.last_skip_reason)
            return (None, 0.0, 0)

        amount_base = amount_in / (10 ** self._base_decimals)
        max_attempts = 1 + self._trade_retry

        for attempt in range(max_attempts):
            if attempt > 0:
                logger.info("[RETRY] 第 %d/%d 次重试 %s -> %s",
                            attempt, self._trade_retry,
                            payment_token[:10], target_token[:10])
                await asyncio.sleep(2)

            quote = await self._okx.get_quote(
                payment_token, target_token, amount_in, self._slippage
            )
            if quote is None:
                msg = "OKX 无可用买入报价路由"
                if attempt < max_attempts - 1:
                    logger.warning("[RETRY] %s，等待重试", msg)
                    continue
                self.last_skip_reason = msg
                logger.warning("[SKIP] %s", msg)
                return (None, 0.0, 0)

            # 报价安全校验
            try:
                self._validate_quote(quote)
            except ValueError as e:
                msg = f"报价校验不通过: {e}"
                if attempt < max_attempts - 1:
                    logger.warning("[RETRY] %s，等待重试", msg)
                    continue
                self.last_skip_reason = msg
                logger.warning("[SKIP] %s", msg)
                return (None, 0.0, 0)

            to_amount = quote.get("toTokenAmount", "?")
            logger.info(
                "[%s] attempt %d/%d %s -> %s | amount_in=%d (%.2f %s) | expected_out=%s",
                "DRY-RUN" if self._dry_run else "LIVE",
                attempt + 1, max_attempts,
                payment_token[:10], target_token[:10],
                amount_in, amount_base, self._base_token,
                to_amount,
            )

            if self._dry_run:
                return (None, amount_base, 0)

            tx_hash = await self._send_swap(payment_token, target_token, amount_in,
                                             source_tx=source_tx, stage="swap")
            if not tx_hash:
                if attempt < max_attempts - 1:
                    logger.warning("[RETRY] 发交易失败，等待重试")
                    continue
                return (None, amount_base, 0)

            filled_raw = await self._confirm_and_parse(tx_hash, target_token)
            if filled_raw > 0:
                return (tx_hash, amount_base, filled_raw)

            logger.warning("[RETRY] attempt %d/%d 链上失败或未收到代币 (tx=%s)",
                           attempt + 1, max_attempts, tx_hash[:10])

        return (None, amount_base, 0)

    async def _calculate_amount(self) -> Optional[int]:
        """根据 base_token 计算跟单金额。
        VIRTUAL 模式：固定 fixed_virtuals 个 VIRTUAL。
        USDC 模式：根据 trade_mode (ratio/fixed) 计算 USDC 数量。
        """
        if self._base_token == "VIRTUAL":
            balance = await self._get_token_balance(VIRTUALS_BASE)
            if balance is None:
                return None

            amount = int(Decimal(str(self._fixed_virtuals)) * Decimal("1e18"))
            if balance < amount:
                self.last_skip_reason = (
                    f"VIRTUAL 余额不足: {balance / 1e18:.2f} < {self._fixed_virtuals}"
                )
                return None
            return amount
        else:
            # USDC 模式
            balance = await self._get_token_balance(USDC_BASE)
            if balance is None:
                return None

            usdc_balance = balance / 1e6
            if usdc_balance <= 0:
                self.last_skip_reason = f"USDC 余额为 0"
                return None

            if self._mode == "ratio":
                amount = int(usdc_balance * self._ratio)
            else:  # fixed
                amount = self._fixed_usd

            if self._max_usd > 0:
                amount = min(amount, self._max_usd)

            if amount <= 0:
                self.last_skip_reason = f"计算金额 <= 0: mode={self._mode} amount={amount}"
                return None

            return int(amount * 1e6)

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

    def _validate_quote(self, quote: dict) -> None:
        """对 OKX 报价做安全校验，借鉴 aidog-auto-buy-bot 的风控模式。"""
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
        """检查 allowance，不够则返回 True 表示需要 approve。"""
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
            return True  # 保守起见，查询失败时也 approve

    async def _approve_and_wait(self, token_addr: str, spender: str, amount: int) -> bool:
        """发送 approve 交易并等待确认。返回 True 表示成功。"""
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(token_addr),
                abi=ERC20_SHORT_ABI,
            )
            nonce = await self._w3.eth.get_transaction_count(
                AsyncWeb3.to_checksum_address(self._wallet)
            )
            approve_tx = await contract.functions.approve(
                AsyncWeb3.to_checksum_address(spender), amount
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

        # OKX V6 可能返回 dexTokenApproveAddress，需要先 approve 该地址
        approve_addr = tx_data.get("dexTokenApproveAddress", "")
        if approve_addr:
            need_approve = await self._check_and_approve(
                token_in, approve_addr, amount_in
            )
            if need_approve and not await self._approve_and_wait(token_in, approve_addr, amount_in):
                logger.warning("[SKIP] approve 失败")
                return None

        tx = tx_data.get("tx", {})
        # OKX 返回的数字字段全是字符串，需要转 int
        for key in ("gas", "gasPrice", "maxPriorityFeePerGas", "value", "maxFeePerGas"):
            val = tx.get(key)
            if val and str(val).isdigit():
                tx[key] = int(val)
        # 移除空字符串字段
        for key in list(tx):
            if isinstance(tx[key], str) and tx[key] == "":
                del tx[key]
        # EIP-1559 模式下需要 maxFeePerGas 和 maxPriorityFeePerGas
        if "maxPriorityFeePerGas" in tx:
            if "gasPrice" in tx:
                tx["maxFeePerGas"] = int(tx["gasPrice"])
                del tx["gasPrice"]
            elif "maxFeePerGas" not in tx:
                tx["maxFeePerGas"] = tx["maxPriorityFeePerGas"]
        # OKX 附加字段，非交易字段，需要移除
        for key in ("minReceiveAmount", "signatureData", "slippagePercent"):
            tx.pop(key, None)

        # Gas padding（借鉴 aidog-auto-buy-bot）
        if "gas" in tx and isinstance(tx["gas"], int):
            tx["gas"] = int(tx["gas"] * GAS_LIMIT_MULTIPLIER)

        checksum_wallet = AsyncWeb3.to_checksum_address(self._wallet)
        tx["from"] = checksum_wallet
        nonce = await self._w3.eth.get_transaction_count(checksum_wallet)
        tx["nonce"] = nonce
        tx["chainId"] = 8453

        signed = Account.sign_transaction(tx, self._pk)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        # 发交易后立即持久化 tx_hash（借鉴 aidog-auto-buy-bot 的 pendingTx 机制）
        if source_tx:
            try:
                await set_tx_pending(source_tx, tx_hash_hex, stage)
                logger.info("[PERSIST] pending tx saved: source=%s tx=%s stage=%s",
                            source_tx[:12], tx_hash_hex[:12], stage)
            except Exception as e:
                logger.warning("[PERSIST] failed to save pending tx: %s", e)

        return tx_hash_hex

    async def sell(self, token_in: str, token_out: str, amount: int,
                   source_tx: str = "") -> Optional[str]:
        """止盈/回购卖出：将持仓代币换回稳定币。
        source_tx 用于发交易后立即持久化 tx_hash，支持 crash 恢复。"""
        self.last_skip_reason = ""

        if self._mode == "monitor":
            self.last_skip_reason = "监测模式，跳过卖出"
            logger.info("[SKIP SELL] %s", self.last_skip_reason)
            return None

        if not await self._check_gas():
            self.last_skip_reason = f"Gas 过高（>{self._gas_limit_gwei} gwei）"
            logger.info("[SKIP SELL] %s", self.last_skip_reason)
            return None

        quote = await self._okx.get_quote(token_in, token_out, amount, self._slippage)
        if quote is None:
            self.last_skip_reason = "OKX 无可用卖出报价路由"
            logger.warning("[SKIP SELL] %s", self.last_skip_reason)
            return None

        # 报价安全校验
        try:
            self._validate_quote(quote)
        except ValueError as e:
            self.last_skip_reason = f"卖出报价校验不通过: {e}"
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

        return await self._send_swap(token_in, token_out, amount,
                                     source_tx=source_tx, stage="sell")
