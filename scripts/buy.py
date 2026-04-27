#!/usr/bin/env python
"""
One-shot buy: swap N USDC for a target token via the OKX DEX Aggregator.
Reuses the bot's existing configuration and web3 connection.

Usage:
  python scripts/buy.py <token_address> [amount_usdc]

Examples:
  python scripts/buy.py 0xc2bceb0ee69455da32abb10a5ba81c0299a925c8
  python scripts/buy.py 0xc2bceb0ee69455da32abb10a5ba81c0299a925c8 5
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web3 import AsyncWeb3
from eth_account import Account

from src.config.loader import load_config
from src.executor.okx_client import OKXDexClient
from src.monitor.decoder import USDC_BASE, TRANSFER_TOPIC

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("buy")

GAS_LIMIT_MULTIPLIER = 1.2


def parse_received_amount(logs: list, target_token: str, wallet_addr: str) -> int:
    target_lower = target_token.lower()
    wallet_padded = "0x" + "0" * 24 + wallet_addr[2:]
    topic0_hex = TRANSFER_TOPIC.lstrip("0x").lower() if isinstance(TRANSFER_TOPIC, str) else ""

    total = 0
    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        raw = topics[0]
        t0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
        if t0 != topic0_hex:
            continue
        if log.get("address", "").lower() != target_lower:
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


def validate_quote(quote: dict) -> None:
    to_token = quote.get("toToken", {}) or {}
    from_token = quote.get("fromToken", {}) or {}

    if to_token.get("isHoneyPot") or from_token.get("isHoneyPot"):
        raise ValueError("Honeypot token detected")
    price_impact = abs(float(quote.get("priceImpactPercent", 0)))
    if price_impact > 5.0:
        raise ValueError(f"Price impact {price_impact:.1f}% exceeds 5% limit")
    tax_rate = float(to_token.get("taxRate", 0))
    if tax_rate > 0.05:
        raise ValueError(f"Tax rate {tax_rate*100:.1f}% exceeds 5% limit")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token_address = sys.argv[1].strip().lower()
    amount_usdc = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    slippage = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10  # default 10%
    amount_raw = int(amount_usdc * 1_000_000)

    cfg = load_config()
    rpc_url = str(cfg.rpc_http_url)
    wallet = cfg.wallet_address.lower()
    pk = cfg.private_key

    if not pk:
        logger.error("No private key configured. Set PRIVATE_KEY in .env")
        sys.exit(1)

    logger.info("Target token : %s", token_address)
    logger.info("Amount       : %s USDC (%d raw)", amount_usdc, amount_raw)
    logger.info("Wallet       : %s", wallet)
    logger.info("RPC          : %s", rpc_url)

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))

    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        # ── 1. Quote ──
        logger.info("Fetching quote...")
        quote = await okx.get_quote(USDC_BASE, token_address, amount_raw, slippage)
        if not quote:
            logger.error("No quote available")
            sys.exit(1)

        try:
            validate_quote(quote)
        except ValueError as e:
            logger.error("Quote validation failed: %s", e)
            sys.exit(1)

        expected_out = int(quote.get("toTokenAmount", 0))
        route = " -> ".join(
            d.get("dexProtocol", {}).get("dexName", "?")
            for d in quote.get("dexRouterList", [])
        ) or "n/a"
        logger.info("Quote OK: %s USDC -> %s (route: %s)", amount_usdc, expected_out, route)
        logger.info("Price impact: %s%%", quote.get("priceImpactPercent", "?"))

        # ── 2. Build swap tx ──
        logger.info("Building swap transaction...")
        tx_data = await okx.build_swap_tx(USDC_BASE, token_address, amount_raw, wallet, slippage)
        if not tx_data:
            logger.error("Failed to build swap transaction")
            sys.exit(1)

        # ── 3. Approve if needed ──
        approve_addr = tx_data.get("dexTokenApproveAddress", "")
        if approve_addr:
            usdc_contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(USDC_BASE),
                abi=[
                    {"name": "allowance", "type": "function",
                     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
                     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
                    {"name": "approve", "type": "function",
                     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
                ],
            )
            allowance = await usdc_contract.functions.allowance(
                AsyncWeb3.to_checksum_address(wallet),
                AsyncWeb3.to_checksum_address(approve_addr),
            ).call()

            if allowance < amount_raw:
                logger.info("Allowance insufficient (%d < %d). Approving...", allowance, amount_raw)
                nonce = await w3.eth.get_transaction_count(AsyncWeb3.to_checksum_address(wallet))
                approve_tx = await usdc_contract.functions.approve(
                    AsyncWeb3.to_checksum_address(approve_addr),
                    amount_raw * 10,  # over-approve
                ).build_transaction({
                    "from": AsyncWeb3.to_checksum_address(wallet),
                    "nonce": nonce,
                    "gas": 100_000,
                    "chainId": 8453,
                })
                signed = Account.sign_transaction(approve_tx, pk)
                tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
                logger.info("Approve sent: %s", tx_hash.hex())

                # Wait for approval
                for _ in range(60):
                    receipt = await w3.eth.get_transaction_receipt(tx_hash)
                    if receipt:
                        if receipt.get("status") == 1:
                            logger.info("Approve confirmed")
                            break
                        else:
                            logger.error("Approve failed on-chain")
                            sys.exit(1)
                    await asyncio.sleep(1)
                else:
                    logger.error("Approve timeout")
                    sys.exit(1)
            else:
                logger.info("Allowance sufficient (%d >= %d), skip approve", allowance, amount_raw)

        # ── 4. Prepare & simulate ──
        tx = tx_data.get("tx", {})
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

        if "gas" in tx and isinstance(tx["gas"], int):
            tx["gas"] = int(tx["gas"] * GAS_LIMIT_MULTIPLIER)

        checksum_wallet = AsyncWeb3.to_checksum_address(wallet)
        tx["from"] = checksum_wallet
        nonce = await w3.eth.get_transaction_count(checksum_wallet)
        tx["nonce"] = nonce
        tx["chainId"] = 8453

        # Simulate
        logger.info("Simulating transaction...")
        try:
            await w3.eth.call({
                "from": AsyncWeb3.to_checksum_address(wallet),
                "to": tx["to"],
                "data": tx.get("data", ""),
                "value": tx.get("value", 0),
            }, "latest")
            logger.info("Simulation OK")
        except Exception as e:
            logger.error("Simulation failed: %s", e)
            sys.exit(1)

        # ── 5. Sign & broadcast ──
        signed = Account.sign_transaction(tx, pk)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        logger.info("Swap sent: %s", tx_hash_hex)
        logger.info("Explorer: https://basescan.org/tx/%s", tx_hash_hex)

        # ── 6. Wait & confirm ──
        logger.info("Waiting for confirmation...")
        for _ in range(90):
            receipt = await w3.eth.get_transaction_receipt(tx_hash_hex)
            if receipt:
                if receipt.get("status") == 1:
                    filled = parse_received_amount(
                        [dict(log) for log in receipt.get("logs", [])],
                        token_address,
                        wallet,
                    )
                    logger.info("Swap confirmed! Block: %s", receipt.get("blockNumber"))
                    logger.info("Received: %s raw (%.8f)", filled, filled / 10 ** 18)
                    logger.info("Explorer: https://basescan.org/tx/%s", tx_hash_hex)
                    return
                else:
                    logger.error("Swap reverted on-chain")
                    sys.exit(1)
            await asyncio.sleep(1)
        else:
            logger.warning("Swap sent but not yet confirmed (tx may still be pending)")
            logger.info("Check: https://basescan.org/tx/%s", tx_hash_hex)


if __name__ == "__main__":
    asyncio.run(main())
