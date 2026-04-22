"""
ERC20 代币信息解析，带内存缓存避免重复 RPC 调用。
"""
import logging
from web3 import AsyncWeb3

logger = logging.getLogger(__name__)

ERC20_META_ABI = [
    {"name": "symbol", "type": "function", "inputs": [],
     "outputs": [{"type": "string"}], "stateMutability": "view"},
    {"name": "name", "type": "function", "inputs": [],
     "outputs": [{"type": "string"}], "stateMutability": "view"},
]

# 常见稳定币/基础代币直接映射，省 RPC
KNOWN = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": "USDT",
    "0x4200000000000000000000000000000000000006": "WETH",
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": "USDbC",
}


class TokenResolver:
    def __init__(self, w3: AsyncWeb3) -> None:
        self._w3 = w3
        self._cache: dict[str, str] = dict(KNOWN)

    async def symbol(self, addr: str) -> str:
        key = addr.lower()
        if key in self._cache:
            return self._cache[key]
        try:
            contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(key),
                abi=ERC20_META_ABI,
            )
            sym = await contract.functions.symbol().call()
            self._cache[key] = sym
            return sym
        except Exception:
            short = key[:6] + "..." + key[-4:]
            self._cache[key] = short
            return short
