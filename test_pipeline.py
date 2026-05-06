"""
交易管道测试：0.1 USDC 极小仓位测试。
先 dry-run 验证报价和构建，再 --live 执行真实买入。
"""
import asyncio
import argparse
import logging
import sys

# 确保 stdout 支持 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

from web3 import AsyncWeb3

from src.config.loader import load_config
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader, USDC_BASE
from src.rpc.router import RPCRouter
from src.db.database import init_db

TOKEN = "0xc2bceb0ee69455da32abb10a5ba81c0299a925c8"
AMOUNT_USDC = 0.1


async def test_pipeline(live: bool):
    cfg = load_config()
    await init_db()

    wallet = AsyncWeb3.to_checksum_address(cfg.wallet_address)
    w3 = RPCRouter(cfg.rpc_http_url, cfg.rpc_http_url_fallback)

    # 检查钱包余额
    eth_balance = await w3.eth.get_balance(wallet)
    eth_eth = eth_balance / 1e18
    print(f"\n钱包: {cfg.wallet_address[:12]}...")
    print(f"ETH 余额: {eth_eth:.6f} ETH")

    if eth_eth < 0.0001:
        print("❌ ETH 余额不足，至少需要 0.0001 ETH 用于 gas")
        return

    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        amount_raw = int(AMOUNT_USDC * 1e6)  # 0.1 USDC = 100000

        # ── Step 1: 报价测试 ──
        print(f"\n{'='*50}")
        print(f"STEP 1: 报价测试  {AMOUNT_USDC} USDC → {TOKEN[:10]}")
        print(f"{'='*50}")

        quote = await okx.get_quote(USDC_BASE, TOKEN, amount_raw, 0.01)
        if quote is None:
            print("❌ OKX 报价失败，检查 API Key 和网络")
            return
        print("✅ 报价成功")
        print(f"   fromTokenAmount: {quote.get('fromTokenAmount')}")
        print(f"   toTokenAmount: {quote.get('toTokenAmount')}")
        print(f"   priceImpact: {quote.get('priceImpactPercent')}%")

        to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
        to_amount = float(quote.get("toTokenAmount", "0")) / (10 ** to_decimals)
        price = AMOUNT_USDC / to_amount if to_amount > 0 else 0
        print(f"   预计获得: {to_amount:.8f} TOKEN")
        print(f"   估算价格: ${price:.6f}")

        # ── Step 2: 交易构建测试 ──
        print(f"\n{'='*50}")
        print(f"STEP 2: 交易构建")
        print(f"{'='*50}")

        tx_data = await okx.build_swap_tx(USDC_BASE, TOKEN, amount_raw, cfg.wallet_address, 0.01)
        if tx_data is None:
            print("❌ 交易构建失败")
            return
        print("✅ 交易构建成功")
        print(f"   DEX: {tx_data.get('dexName', '?')}")
        tx = tx_data.get("tx", {})
        print(f"   to: {str(tx.get('to', ''))[:20]}")
        print(f"   gas: {tx.get('gas', '?')}")
        need_approve = bool(tx_data.get("dexTokenApproveAddress"))
        if need_approve:
            print(f"   需要 Approve: {str(tx_data['dexTokenApproveAddress'])[:20]}")

        # ── Step 3: 检查 USDC 余额 ──
        print(f"\n{'='*50}")
        print(f"STEP 3: USDC 余额检查")
        print(f"{'='*50}")

        usdc_contract = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(USDC_BASE),
            abi=[{
                "name": "balanceOf",
                "type": "function",
                "inputs": [{"name": "account", "type": "address"}],
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
            }],
        )
        usdc_raw = await usdc_contract.functions.balanceOf(wallet).call()
        usdc_balance = usdc_raw / 1e6
        print(f"USDC 余额: {usdc_balance:.2f} USDC")
        if usdc_balance < AMOUNT_USDC:
            print(f"❌ USDC 不足，需要至少 {AMOUNT_USDC} USDC")
            return
        print(f"✅ USDC 充足")

        # ── Step 4: 执行买入 ──
        if live:
            print(f"\n{'='*50}")
            print(f"STEP 4: 执行买入  {AMOUNT_USDC} USDC → {TOKEN[:10]}")
            print(f"{'='*50}")

            trader = Trader(
                w3=w3, okx=okx,
                wallet_addr=cfg.wallet_address,
                private_key=cfg.private_key,
                base_token=cfg.base_token,
                slippage=cfg.slippage,
                gas_limit_gwei=cfg.gas_limit_gwei,
                dry_run=False,
            )

            tx_hash, filled_raw = await trader.buy(
                TOKEN, amount_raw,
                payment_token=USDC_BASE, payment_decimals=6,
                source_tx=f"test_0.1u",
            )

            if tx_hash and filled_raw > 0:
                print(f"\n{'='*50}")
                print(f"✅ 买入成功!")
                print(f"{'='*50}")
                print(f"   tx_hash: {tx_hash}")
                print(f"   filled_raw: {filled_raw}")
                filled_token = filled_raw / (10 ** to_decimals)
                print(f"   filled: {filled_token:.8f} TOKEN")

                receipt = await w3.eth.get_transaction_receipt(tx_hash)
                if receipt and receipt.get("status") == 1:
                    print(f"   🟢 链上状态: success")
                    print(f"   block: {receipt.get('blockNumber')}")
                else:
                    print(f"   🔴 链上状态异常")
            else:
                print(f"\n❌ 买入失败: {trader.last_skip_reason}")
        else:
            print(f"\n{'='*50}")
            print(f"🔵 Dry-run: 未发送交易")
            print(f"{'='*50}")
            print(f"   加上 --live 参数执行真实买入")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="执行 0.1 USDC 真实买入测试")
    args = parser.parse_args()
    asyncio.run(test_pipeline(live=args.live))
