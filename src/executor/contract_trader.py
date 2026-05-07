"""
合约交易执行器。

职责：
  - 开多/开空（自动换算合约张数、设置杠杆）
  - 平仓（市价全平）
  - 止损止盈管理
  - 保证金调整

依赖 OKXCexClient（v5 API），不涉及链上操作。
"""
import asyncio
import logging
from typing import Optional

from src.executor.okx_cex_client import OKXCexClient

logger = logging.getLogger(__name__)

# 品种分类（用于杠杆约束）
MAIN_PAIRS = {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}


class ContractTrader:
    """合约交易执行器。"""

    def __init__(
        self,
        cex: OKXCexClient,
        default_leverage: int = 3,
        max_leverage_main: int = 5,
        max_leverage_alt: int = 3,
        dry_run: bool = True,
    ) -> None:
        self._cex = cex
        self._default_leverage = default_leverage
        self._max_leverage_main = max_leverage_main
        self._max_leverage_alt = max_leverage_alt
        self._dry_run = dry_run
        # 缓存合约规格 {instId: {ctVal, ctMult, minSz, ...}}
        self._instruments: dict[str, dict] = {}

    # ── 公开 API ─────────────────────────────────────────────────────

    async def open_long(
        self,
        pair: str,
        size_usd: float,
        leverage: int = 0,
    ) -> Optional[str]:
        """
        开多。

        Args:
            pair: 交易对，如 "BTC-USDT-SWAP"
            size_usd: 投入保证金金额（USD）
            leverage: 杠杆倍数，0 使用默认

        Returns:
            ord_id (or None on failure / dry-run)
        """
        lev = await self._resolve_leverage(pair, leverage)
        sz = await self._usd_to_size(pair, size_usd, lev)
        if sz <= 0:
            logger.warning("[CONTRACT] %s: size_usd=%.2f 折算张数为 0，跳过", pair, size_usd)
            return None

        logger.info(
            "[CONTRACT] %s OPEN LONG | margin=%.2fUSD lev=%dx sz=%d%s",
            pair, size_usd, lev, sz,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        # 设置杠杆
        await self._cex.set_leverage(pair, str(lev), pos_side="long")

        result = await self._cex.place_order(
            inst_id=pair,
            td_mode="cross",
            side="buy",
            pos_side="long",
            ord_type="market",
            sz=str(sz),
        )
        if result:
            ord_id = result.get("data", [{}])[0].get("ordId", "")
            logger.info("[CONTRACT] %s 开多成功: ordId=%s", pair, ord_id)
            return ord_id
        return None

    async def open_short(
        self,
        pair: str,
        size_usd: float,
        leverage: int = 0,
    ) -> Optional[str]:
        """
        开空。

        Args:
            pair: 交易对，如 "BTC-USDT-SWAP"
            size_usd: 投入保证金金额（USD）
            leverage: 杠杆倍数，0 使用默认

        Returns:
            ord_id (or None)
        """
        lev = await self._resolve_leverage(pair, leverage)
        sz = await self._usd_to_size(pair, size_usd, lev)
        if sz <= 0:
            logger.warning("[CONTRACT] %s: size_usd=%.2f 折算张数为 0，跳过", pair, size_usd)
            return None

        logger.info(
            "[CONTRACT] %s OPEN SHORT | margin=%.2fUSD lev=%dx sz=%d%s",
            pair, size_usd, lev, sz,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        await self._cex.set_leverage(pair, str(lev), pos_side="short")

        result = await self._cex.place_order(
            inst_id=pair,
            td_mode="cross",
            side="sell",
            pos_side="short",
            ord_type="market",
            sz=str(sz),
        )
        if result:
            ord_id = result.get("data", [{}])[0].get("ordId", "")
            logger.info("[CONTRACT] %s 开空成功: ordId=%s", pair, ord_id)
            return ord_id
        return None

    async def close_position(self, pair: str, pos_side: str = "") -> Optional[str]:
        """
        市价全平。

        Args:
            pair: 交易对
            pos_side: "long" | "short" | 空串（自动识别）

        Returns:
            ord_id (or None)
        """
        logger.info(
            "[CONTRACT] %s CLOSE POSITION (pos_side=%s)%s",
            pair, pos_side or "auto",
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        result = await self._cex.close_position(pair, pos_side=pos_side)
        if result:
            data = result.get("data", [{}])[0]
            ord_id = data.get("ordId", "")
            logger.info("[CONTRACT] %s 平仓成功: ordId=%s", pair, ord_id)
            return ord_id
        return None

    async def set_stop_loss(self, pair: str, stop_price: float,
                            pos_side: str = "long", size_contracts: str = "") -> Optional[str]:
        """
        设置止损。

        Args:
            pair: 交易对
            stop_price: 止损触发价
            pos_side: "long" | "short"
            size_contracts: 止损张数，空串表示全部

        Returns:
            algo_id (or None)
        """
        sz = size_contracts or await self._get_position_sz(pair, pos_side)
        if not sz or int(sz) <= 0:
            logger.warning("[CONTRACT] %s 无持仓，无法设置止损", pair)
            return None

        side = "sell" if pos_side == "long" else "buy"

        logger.info(
            "[CONTRACT] %s SET SL @ %.2f | pos=%s sz=%s%s",
            pair, stop_price, pos_side, sz,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        result = await self._cex.place_algo_order(
            inst_id=pair, td_mode="cross",
            side=side, pos_side=pos_side, sz=sz,
            sl_trigger_px=str(stop_price),
        )
        if result:
            data = result.get("data", [{}])[0]
            algo_id = data.get("algoId", "")
            logger.info("[CONTRACT] %s 止损设置成功: algoId=%s", pair, algo_id)
            return algo_id
        return None

    async def set_take_profit(self, pair: str, target_price: float,
                              pos_side: str = "long", size_contracts: str = "") -> Optional[str]:
        """
        设置止盈。

        Args:
            pair: 交易对
            target_price: 止盈触发价
            pos_side: "long" | "short"
            size_contracts: 止盈张数，空串表示全部

        Returns:
            algo_id (or None)
        """
        sz = size_contracts or await self._get_position_sz(pair, pos_side)
        if not sz or int(sz) <= 0:
            logger.warning("[CONTRACT] %s 无持仓，无法设置止盈", pair)
            return None

        side = "sell" if pos_side == "long" else "buy"

        logger.info(
            "[CONTRACT] %s SET TP @ %.2f | pos=%s sz=%s%s",
            pair, target_price, pos_side, sz,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        result = await self._cex.place_algo_order(
            inst_id=pair, td_mode="cross",
            side=side, pos_side=pos_side, sz=sz,
            tp_trigger_px=str(target_price),
        )
        if result:
            data = result.get("data", [{}])[0]
            algo_id = data.get("algoId", "")
            logger.info("[CONTRACT] %s 止盈设置成功: algoId=%s", pair, algo_id)
            return algo_id
        return None

    async def adjust_margin(self, pair: str, amount: float,
                            pos_side: str = "long",
                            direction: str = "add") -> Optional[dict]:
        """
        调整保证金。

        Args:
            pair: 交易对
            amount: 保证金金额（USD）
            pos_side: "long" | "short"
            direction: "add" | "reduce"
        """
        logger.info(
            "[CONTRACT] %s ADJUST MARGIN %s %.2f USD | pos=%s%s",
            pair, direction, amount, pos_side,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            return None

        return await self._cex.adjust_margin(pair, pos_side, str(amount), direction)

    # ── 查询 ────────────────────────────────────────────────────────

    async def get_position(self, pair: str) -> Optional[dict]:
        """获取当前持仓信息。"""
        return await self._cex.get_position(pair)

    async def get_positions(self) -> list[dict]:
        """获取所有合约持仓。"""
        return await self._cex.get_positions()

    async def get_balance(self) -> Optional[dict]:
        """获取账户余额。"""
        return await self._cex.get_balance()

    async def get_ticker(self, pair: str) -> Optional[dict]:
        """获取最新 ticker。"""
        return await self._cex.get_ticker(pair)

    # ── 内部辅助 ────────────────────────────────────────────────────

    async def _resolve_leverage(self, pair: str, leverage: int) -> int:
        """确定杠杆倍数，受最大杠杆约束。"""
        lev = leverage if leverage > 0 else self._default_leverage
        max_lev = self._max_leverage_main if pair in MAIN_PAIRS else self._max_leverage_alt
        if lev > max_lev:
            logger.warning("[CONTRACT] %s: 杠杆 %dx 超过最大值 %dx，降级", pair, lev, max_lev)
            lev = max_lev
        return lev

    async def _usd_to_size(self, pair: str, size_usd: float, leverage: int) -> int:
        """
        将保证金美元金额换算为合约张数。

        公式：
          名义价值 = size_usd * leverage
          张数 = 名义价值 / (ctVal * 最新价)
        """
        inst = await self._get_instrument(pair)
        if not inst:
            return 0

        ct_val = float(inst.get("ctVal", "0"))
        if ct_val <= 0:
            logger.error("[CONTRACT] %s: 无效面值 ctVal=%s", pair, ct_val)
            return 0

        ticker = await self._cex.get_ticker(pair)
        if not ticker:
            return 0
        price = float(ticker.get("last", "0"))
        if price <= 0:
            logger.error("[CONTRACT] %s: 无效价格 %s", pair, price)
            return 0

        notional = size_usd * leverage          # 名义价值
        sz = notional / (ct_val * price)        # 合约张数（浮点）
        sz_int = int(sz)
        if sz_int < 1:
            logger.warning("[CONTRACT] %s: %s USD@%dx 不足 1 张 (%.4f < 1)，跳过",
                           pair, size_usd, leverage, sz)
            return 0
        return sz_int

    async def _get_instrument(self, pair: str) -> Optional[dict]:
        """获取合约规格（带缓存）。"""
        if pair in self._instruments:
            return self._instruments[pair]

        instruments = await self._cex.get_instruments("SWAP")
        for inst in instruments:
            if inst.get("instId") == pair:
                self._instruments[pair] = inst
                return inst
        logger.error("[CONTRACT] %s: 未找到合约规格", pair)
        return None

    async def _get_position_sz(self, pair: str, pos_side: str) -> str:
        """查询当前持仓张数。"""
        pos = await self._cex.get_position(pair)
        if pos and pos.get("posSide") == pos_side:
            return pos.get("sz", "0")
        return "0"
