"""
测试买入链路：用 5 USDC 买入指定代币。
OKX V6 流程：approve dexTokenApproveAddress → 执行 swap（不要 pre-transfer 到路由器）。
"""
import asyncio
import logging
import sys

sys.path.insert(0, ".")

from web3 import AsyncWeb3
from eth_account import Account
from src.config.loader import load_config
from src.executor.okx_client import OKXDexClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TARGET_TOKEN = "0xc2bceb0ee69455da32abb10a5ba81c0299a925c8"
AMOUNT_USD = 5
AMOUNT_RAW = AMOUNT_USD * 10**6
SLIPPAGE = 0.01

ERC20_ABI = [
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
]


def sanitize_tx(tx: dict, wallet: str) -> dict:
    """OKX 返回的 tx 字段类型修复"""
    for key in ("gas", "gasPrice", "maxPriorityFeePerGas", "value", "maxFeePerGas"):
        val = tx.get(key)
        if val and str(val).isdigit():
            tx[key] = int(val)
    for key in list(tx):
        if isinstance(tx[key], str) and tx[key] == "":
            del tx[key]
    if "maxPriorityFeePerGas" in tx:
        if "gasPrice" in tx:
            tx["maxFeePerGas"] = int(tx["gasPrice"])
            del tx["gasPrice"]
        elif "maxFeePerGas" not in tx:
            tx["maxFeePerGas"] = tx["maxPriorityFeePerGas"]
    for key in ("minReceiveAmount", "signatureData", "slippagePercent"):
        tx.pop(key, None)
    tx["from"] = AsyncWeb3.to_checksum_address(wallet)
    return tx


async def send_and_wait(w3: AsyncWeb3, tx: dict, pk: str, desc: str = "tx") -> tuple[int, str]:
    """签名、广播、等确认。返回 (status, tx_hash)。"""
    signed = Account.sign_transaction(tx, pk)
    tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info("[%s] sent: %s", desc, tx_hash.hex())
    for _ in range(60):
        receipt = await w3.eth.get_transaction_receipt(tx_hash)
        if receipt is not None:
            status = receipt.get("status")
            logger.info("[%s] status=%s | gasUsed=%s | block=%s",
                         desc, status, receipt.get("gasUsed"), receipt.get("blockNumber"))
            return status, tx_hash.hex()
        await asyncio.sleep(1)
    logger.warning("[%s] not confirmed in 60s", desc)
    return 0, tx_hash.hex()


async def main():
    cfg = load_config()
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
    logger.info("RPC connected: %s", await w3.is_connected())

    wallet = cfg.wallet_address
    pk = cfg.private_key
    chk = lambda addr: AsyncWeb3.to_checksum_address(addr)  # noqa: E731

    usdc_contract = w3.eth.contract(address=chk(USDC_BASE), abi=ERC20_ABI)

    # 检查余额
    balance = await usdc_contract.functions.balanceOf(chk(wallet)).call()
    logger.info("USDC balance: %.2f", balance / 1e6)
    if balance < AMOUNT_RAW:
        logger.error("余额不足: %.2f < %d", balance / 1e6, AMOUNT_USD)
        return

    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        quote = await okx.get_quote(USDC_BASE, TARGET_TOKEN, AMOUNT_RAW, SLIPPAGE)
        if quote is None:
            logger.error("OKX 报价失败")
            return
        logger.info("Quote: %s USDC -> %s", quote.get("fromTokenAmount"), quote.get("toTokenAmount"))

        tx_data = await okx.build_swap_tx(USDC_BASE, TARGET_TOKEN, AMOUNT_RAW, wallet, SLIPPAGE)
        if tx_data is None:
            logger.error("OKX 构建交易失败")
            return

        # approve 步骤：检查并批准 dexTokenApproveAddress
        approve_addr = tx_data.get("dexTokenApproveAddress", "")
        if approve_addr:
            logger.info("dexTokenApproveAddress: %s", approve_addr)
            allow = await usdc_contract.functions.allowance(chk(wallet), chk(approve_addr)).call()
            if allow < AMOUNT_RAW:
                logger.info("Allowance 不足 (%d < %d), 发起 approve...", allow, AMOUNT_RAW)
                nonce = await w3.eth.get_transaction_count(chk(wallet), "pending")
                approve_tx = await usdc_contract.functions.approve(chk(approve_addr), AMOUNT_RAW).build_transaction({
                    "from": chk(wallet), "nonce": nonce, "gas": 100000, "chainId": 8453,
                })
                status, tx_hash = await send_and_wait(w3, approve_tx, pk, "approve")
                if status != 1:
                    logger.error("Approve 失败")
                    return
                logger.info("Approve 成功!")
            else:
                logger.info("Allowance 充足 (%d >= %d)，跳过 approve", allow, AMOUNT_RAW)
        else:
            logger.info("无 dexTokenApproveAddress，跳过 approve")

        # 执行 swap（不再 pre-transfer 到路由器）
        tx = sanitize_tx(tx_data.get("tx", {}), wallet)
        router = tx["to"]
        logger.info("Swap router: %s", router)
        logger.info("Gas limit: %s", tx.get("gas"))

        nonce2 = await w3.eth.get_transaction_count(chk(wallet), "pending")
        tx["nonce"] = nonce2
        tx["chainId"] = 8453

        status2, tx_hash2 = await send_and_wait(w3, tx, pk, "swap")
        if status2 == 1:
            token_contract = w3.eth.contract(address=chk(TARGET_TOKEN), abi=ERC20_ABI)
            token_bal = await token_contract.functions.balanceOf(chk(wallet)).call()
            logger.info("买入成功! tx: https://basescan.org/tx/%s", tx_hash2)
            logger.info("钱包余额: %s raw tokens", token_bal)
        else:
            logger.error("交易失败")


if __name__ == "__main__":
    asyncio.run(main())
