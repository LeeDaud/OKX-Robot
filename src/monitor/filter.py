"""
过滤已处理的交易，以及白名单/最小金额规则。
"""
from typing import Set

VIRTUALS_BASE = "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"


class TxFilter:
    """交易去重，自动淘汰旧记录避免内存泄漏。"""
    def __init__(self, maxlen: int = 50000) -> None:
        self._seen: dict[str, None] = {}  # 用 dict 做有序 set
        self._maxlen = maxlen

    def is_new(self, tx_hash: str) -> bool:
        if tx_hash in self._seen:
            return False
        self._evict_if_full()
        self._seen[tx_hash] = None
        return True

    def mark_seen(self, tx_hash: str) -> None:
        if tx_hash not in self._seen:
            self._evict_if_full()
            self._seen[tx_hash] = None

    def _evict_if_full(self) -> None:
        if len(self._seen) >= self._maxlen:
            self._seen.pop(next(iter(self._seen)))


class SwapFilter:
    def __init__(
        self,
        token_whitelist: list[str],
        min_trade_usd: float,
    ) -> None:
        self._whitelist = {t.lower() for t in token_whitelist}
        self._min_raw = int(min_trade_usd * 10 ** 6)

    def update(self, token_whitelist: list[str], min_trade_usd: float) -> None:
        self._whitelist = {t.lower() for t in token_whitelist}
        self._min_raw = int(min_trade_usd * 10 ** 6)

    def allow(self, token_in: str, token_out: str, amount_in: int) -> tuple[bool, str]:
        """返回 (是否放行, 跳过原因)。"""
        if self._whitelist:
            if token_in.lower() not in self._whitelist and token_out.lower() not in self._whitelist:
                return False, f"token not in whitelist: {token_out[:10]}"

        if self._min_raw > 0:
            # Virtuals 是 18 位小数，统一缩放到 6 位再比较
            scaled = amount_in // 10**12 if token_in.lower() == VIRTUALS_BASE else amount_in
            if scaled < self._min_raw:
                return False, f"amount {scaled} < min {self._min_raw}"

        return True, ""
