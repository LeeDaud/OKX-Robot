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
    "0xa31bd6a0edbc4da307b8fa92bd6cf39e0fae262c",  # Virtuals Protocol Router
    "0xf66dea7b3e897cd44a5a231c61b6b4423d613259",  # Virtuals Protocol Router v2
    "0x8292b43ab73efac11faf357419c38acf448202c5",  # Virtuals Protocol Router v3
}

WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDT_BASE = "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2"
VIRTUALS_BASE = "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"  # Virtuals Protocol 代币

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

# 原生 ETH 地址（OKX DEX 和大多数聚合器用此表示原生代币）
ETH_BASE = "0x0000000000000000000000000000000000000000"


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
    tx_value: int = 0,
) -> Optional[SwapInfo]:
    """从交易 receipt logs 中提取 swap 信息。优先解析 V3，回退到 V2，最后用 Transfer 兜底。
    tx_value 用于处理目标地址用原生 ETH 支付的场景（无 ERC20 Transfer 从目标发出）。
    """
    for log in logs:
        topics = log.get("topics", [])
        if not topics:
            continue
        raw = topics[0]
        topic0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
        v3 = UNISWAP_V3_SWAP_TOPIC.lstrip("0x").lower()
        v2 = UNISWAP_V2_SWAP_TOPIC.lstrip("0x").lower()

        if topic0 == v3:
            result = _decode_v3_swap(tx_hash, from_addr, log, block_number)
            if result is not None:
                return result
            continue
        if topic0 == v2:
            result = _decode_v2_swap(tx_hash, from_addr, log, block_number)
            if result is not None:
                return result
            continue

    return _decode_swap_from_transfers(tx_hash, from_addr, logs, block_number, tx_value)


def _decode_v3_swap(
    tx_hash: str, from_addr: str, log: LogReceipt, block_number: int
) -> Optional[SwapInfo]:
    """
    V3 Swap event: Swap(address sender, address recipient,
                        int256 amount0, int256 amount1, ...)
    amount0/amount1 为有符号整数，负值表示流出（token_out），正值表示流入（token_in）。
    只匹配 sender 或 recipient 是 from_addr 的事件，跳过路由器的内部交换。
    """
    topics = log.get("topics", [])
    if len(topics) < 3:
        return None
    sender = _topic_to_addr(topics[1])
    recipient = _topic_to_addr(topics[2])
    if from_addr.lower() not in (sender.lower(), recipient.lower()):
        return None

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


def _topic_to_addr(topic) -> str:
    raw = topic.hex() if isinstance(topic, bytes) else topic
    return "0x" + raw[-40:].lower()


def _decode_v2_swap(
    tx_hash: str, from_addr: str, log: LogReceipt, block_number: int
) -> Optional[SwapInfo]:
    """
    V2 Swap event: Swap(address sender, uint amount0In, uint amount1In,
                        uint amount0Out, uint amount1Out, address to)
    只匹配 sender 或 to 是 from_addr 的事件，跳过路由器的内部交换。
    """
    topics = log.get("topics", [])
    if len(topics) < 3:
        return None
    sender = _topic_to_addr(topics[1])
    to = _topic_to_addr(topics[2])
    if from_addr.lower() not in (sender.lower(), to.lower()):
        return None

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


def _decode_swap_from_transfers(
    tx_hash: str,
    from_addr: str,
    logs: list[LogReceipt],
    block_number: int,
    tx_value: int = 0,
) -> Optional[SwapInfo]:
    """
    Transfer 事件兜底解析，用于 Virtuals 等不发标准 Swap 事件的 DEX。
    找 from_addr 转出的最大 Transfer 作为 token_in，转入 from_addr 的 Transfer 作为 token_out。
    如果转出方向无 ERC20 Transfer（即用原生 ETH 支付），用 tx_value 兜底。
    """
    transfer_topic = TRANSFER_TOPIC.lstrip("0x").lower()
    transfers_out: list[tuple[str, int]] = []
    transfers_in: list[tuple[str, int]] = []

    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        raw = topics[0]
        topic0 = (raw.hex() if isinstance(raw, bytes) else raw).lstrip("0x").lower()
        if topic0 != transfer_topic:
            continue

        from_ = _topic_to_addr(topics[1])
        to_ = _topic_to_addr(topics[2])

        data = log.get("data", b"")
        if isinstance(data, bytes):
            data_bytes = data
        else:
            data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        if len(data_bytes) < 32:
            continue
        amount = int.from_bytes(data_bytes[:32], "big")
        token = log["address"].lower()

        if from_ == from_addr.lower():
            transfers_out.append((token, amount))
        if to_ == from_addr.lower():
            transfers_in.append((token, amount))

    # 如果用户用原生 ETH 支付（无 ERC20 Transfer 从目标发出），用 tx_value 兜底
    if not transfers_out and tx_value > 0:
        transfers_out.append((ETH_BASE, tx_value))

    if not transfers_out or not transfers_in:
        return None

    # 取转出最大的作为 token_in（排除 fee 小额转出）
    token_in, amount_in = max(transfers_out, key=lambda x: x[1])
    # 取转入最大的作为 token_out
    token_out, amount_out = max(transfers_in, key=lambda x: x[1])

    if token_in == token_out:
        return None

    return SwapInfo(
        tx_hash=tx_hash,
        from_addr=from_addr,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=amount_out,
        block_number=block_number,
    )
