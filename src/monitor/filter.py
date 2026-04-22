"""
过滤已处理的交易，以及白名单/最小金额规则。
"""
from typing import Set


class TxFilter:
    def __init__(self) -> None:
        self._seen: Set[str] = set()

    def is_new(self, tx_hash: str) -> bool:
        if tx_hash in self._seen:
            return False
        self._seen.add(tx_hash)
        return True

    def mark_seen(self, tx_hash: str) -> None:
        self._seen.add(tx_hash)


class SwapFilter:
    def __init__(
        self,
        token_whitelist: list[str],
        min_trade_usd: float,
        stable_decimals: int = 6,
    ) -> None:
        self._whitelist = {t.lower() for t in token_whitelist}
        self._min_raw = int(min_trade_usd * 10 ** stable_decimals)

    def update(self, token_whitelist: list[str], min_trade_usd: float) -> None:
        self._whitelist = {t.lower() for t in token_whitelist}
        self._min_raw = int(min_trade_usd * 10 ** 6)

    def allow(self, token_in: str, token_out: str, amount_in: int) -> tuple[bool, str]:
        """返回 (是否放行, 跳过原因)。"""
        if self._whitelist:
            if token_in.lower() not in self._whitelist and token_out.lower() not in self._whitelist:
                return False, f"token not in whitelist: {token_out[:10]}"

        if self._min_raw > 0 and amount_in < self._min_raw:
            return False, f"amount_in {amount_in} < min {self._min_raw}"

        return True, ""
