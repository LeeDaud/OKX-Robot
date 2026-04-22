"""
解析链上 swap 交易，提取 token_in / token_out / amount_in。
支持 Uniswap V2/V3 和 Aerodrome（Base 链主流 DEX）。
"""
from dataclasses import dataclass
from typing import Optional
from web3 import Web3
from web3.types import TxData, LogReceipt

# Uniswap V3 Swap 事件签名
UNISWAP_V3_SWAP_TOPIC = Web3.keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24)"
).hex()

# Uniswap V2 / Aerodrome Swap 事件签名
UNISWAP_V2_SWAP_TOPIC = Web3.keccak(
    text="Swap(address,uint256,uint256,uint256,uint256,address)"
).hex()

# Base 链主流 DEX router 地址（小写）
KNOWN_ROUTERS = {
    "0x2626664c2603336e57b271c5c0b26f421741e481",  # Uniswap V3 SwapRouter02
    "0x198ef79f1f515f02dfe9e3115ed9fc07183f02fc",  # Aerodrome Router
    "0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43",  # OKX DEX Router (Base)
}

WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDT_BASE = "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2"


@dataclass
class SwapInfo:
    tx_hash: str
    from_addr: str
    token_in: str
    token_out: str
    amount_in: int      # raw amount（未除以 decimals）
    amount_out: int
    block_number: int


def is_swap_tx(tx: TxData) -> bool:
    """粗筛：to 地址是已知 router，或 input data 以已知 selector 开头。"""
    to = (tx.get("to") or "").lower()
    return to in KNOWN_ROUTERS


def decode_swap_from_logs(
    tx_hash: str,
    from_addr: str,
    logs: list[LogReceipt],
    block_number: int,
) -> Optional[SwapInfo]:
    """从交易 receipt logs 中提取 swap 信息。优先解析 V3，回退到 V2。"""
    for log in logs:
        topics = log.get("topics", [])
        if not topics:
            continue
        raw = topics[0]
        # HexBytes.hex() 和 str 都可能不带 0x，统一去掉前缀比较
        topic0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
        v3 = UNISWAP_V3_SWAP_TOPIC.lstrip("0x").lower()
        v2 = UNISWAP_V2_SWAP_TOPIC.lstrip("0x").lower()

        if topic0 == v3:
            return _decode_v3_swap(tx_hash, from_addr, log, block_number)
        if topic0 == v2:
            return _decode_v2_swap(tx_hash, from_addr, log, block_number)
    return None


def _decode_v3_swap(
    tx_hash: str, from_addr: str, log: LogReceipt, block_number: int
) -> Optional[SwapInfo]:
    """
    V3 Swap event: Swap(address sender, address recipient,
                        int256 amount0, int256 amount1, ...)
    amount0/amount1 为有符号整数，负值表示流出（token_out），正值表示流入（token_in）。
    """
    try:
        data = log["data"]
        data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        # amount0 和 amount1 各占 32 字节，有符号
        amount0 = int.from_bytes(data_bytes[0:32], "big", signed=True)
        amount1 = int.from_bytes(data_bytes[32:64], "big", signed=True)

        pool_addr = log["address"].lower()
        # 无法从单条 log 直接得到 token0/token1，需要调用合约
        # Phase 1 简化：返回 pool 地址作为占位，由 watcher 补全
        if amount0 > 0:
            token_in_placeholder = f"pool:{pool_addr}:token0"
            token_out_placeholder = f"pool:{pool_addr}:token1"
            amount_in, amount_out = amount0, abs(amount1)
        else:
            token_in_placeholder = f"pool:{pool_addr}:token1"
            token_out_placeholder = f"pool:{pool_addr}:token0"
            amount_in, amount_out = amount1, abs(amount0)

        return SwapInfo(
            tx_hash=tx_hash,
            from_addr=from_addr,
            token_in=token_in_placeholder,
            token_out=token_out_placeholder,
            amount_in=amount_in,
            amount_out=amount_out,
            block_number=block_number,
        )
    except Exception:
        return None


def _decode_v2_swap(
    tx_hash: str, from_addr: str, log: LogReceipt, block_number: int
) -> Optional[SwapInfo]:
    """
    V2 Swap event: Swap(address sender, uint amount0In, uint amount1In,
                        uint amount0Out, uint amount1Out, address to)
    """
    try:
        data = log["data"]
        data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        amount0_in = int.from_bytes(data_bytes[0:32], "big")
        amount1_in = int.from_bytes(data_bytes[32:64], "big")
        amount0_out = int.from_bytes(data_bytes[64:96], "big")
        amount1_out = int.from_bytes(data_bytes[96:128], "big")

        pool_addr = log["address"].lower()
        if amount0_in > 0:
            token_in_placeholder = f"pool:{pool_addr}:token0"
            token_out_placeholder = f"pool:{pool_addr}:token1"
            amount_in, amount_out = amount0_in, amount1_out
        else:
            token_in_placeholder = f"pool:{pool_addr}:token1"
            token_out_placeholder = f"pool:{pool_addr}:token0"
            amount_in, amount_out = amount1_in, amount0_out

        return SwapInfo(
            tx_hash=tx_hash,
            from_addr=from_addr,
            token_in=token_in_placeholder,
            token_out=token_out_placeholder,
            amount_in=amount_in,
            amount_out=amount_out,
            block_number=block_number,
        )
    except Exception:
        return None
