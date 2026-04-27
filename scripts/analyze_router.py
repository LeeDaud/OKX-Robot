"""Analyze router bytecode for known function selectors."""
import asyncio
import sys

sys.path.insert(0, ".")

from web3 import AsyncWeb3
from src.config.loader import load_config


async def main():
    cfg = load_config()
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
    router = "0xC8F6b8Ba0DC0f175B568B99440B0867F69A29265"
    code = await w3.eth.get_code(AsyncWeb3.to_checksum_address(router))
    code_hex = code.hex()

    known = {
        "a9059cbb": "transfer(address,uint256)",
        "23b872dd": "transferFrom(address,address,uint256)",
        "095ea7b3": "approve(address,uint256)",
        "70a08231": "balanceOf(address)",
        "f2c42696": "swap function (unknown)",
        "dd62ed3a": "allowance(address,address)",
    }
    for sel, name in known.items():
        if sel in code_hex:
            print(f"0x{sel} -> {name}")

    print(f"\nSelector we call: 0xf2c42696")
    print(f"Code length: {len(code)} bytes")


asyncio.run(main())
