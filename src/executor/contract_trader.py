"""
链上永续合约交易执行器（SynFutures V3）。

职责：
  - 开多/开空（自动换算仓位规模、设置杠杆）
  - 平仓（市价全平）
  - 仓位查询、余额查询、价格查询
  - 止损止盈声明（链上无原生支持，由上层应用管理）

与原 OKX CEX 版本保持相同公开接口，底层以 SynFutures V3 链上合约替代。
"""
import asyncio
import logging
from typing import Optional

from web3 import AsyncWeb3

from src.executor.synfutures_client import (
    SynFuturesClient, USDC_DECIMALS,
)

logger = logging.getLogger(__name__)

# 主流品种（对应 max_leverage_main 约束）
MAIN_PAIRS = {"BTC/USDC", "ETH/USDC"}


class ContractTrader:
    """链上永续合约交易执行器。

    封装 SynFuturesClient，提供与原有 CEX 版本相同的公开接口。
    """

    def __init__(
        self,
        w3: AsyncWeb3,
        private_key: str,
        wallet_address: str,
        default_leverage: int = 3,
        max_leverage_main: int = 5,
        max_leverage_alt: int = 3,
        dry_run: bool = True,
    ) -> None:
        self._sf = SynFuturesClient(w3, private_key, wallet_address, dry_run=dry_run)
        self._default_leverage = default_leverage
        self._max_leverage_main = max_leverage_main
        self._max_leverage_alt = max_leverage_alt
        self._dry_run = dry_run
        self._wallet = wallet_address.lower()
        # 记录当前保证金分配 {pair: margin_usd}，用于 close/balance 查询
        self._active_margins: dict[str, float] = {}

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
            pair: 交易对，如 "BTC/USDC"
            size_usd: 投入保证金金额（USD）
            leverage: 杠杆倍数，0 使用默认

        Returns:
            tx_hash or None
        """
        return await self._open_position(pair, size_usd, leverage, is_long=True)

    async def open_short(
        self,
        pair: str,
        size_usd: float,
        leverage: int = 0,
    ) -> Optional[str]:
        """
        开空。

        Args:
            pair: 交易对
            size_usd: 保证金金额（USD）
            leverage: 杠杆倍数

        Returns:
            tx_hash or None
        """
        return await self._open_position(pair, size_usd, leverage, is_long=False)

    async def close_position(self, pair: str, pos_side: str = "") -> Optional[str]:
        """
        市价全平。

        Args:
            pair: 交易对
            pos_side: 仅用于接口兼容（链上自动识别方向）

        Returns:
            tx_hash or None
        """
        instr = await self._sf.resolve_instrument(pair)
        if not instr:
            logger.warning("[CONTRACT] %s: 无法解析 instrument", pair)
            return None

        pos = await self._sf.get_position(instr["address"])
        if not pos or pos["size"] == 0:
            logger.info("[CONTRACT] %s: 无持仓，无需平仓", pair)
            return None

        # 反向交易：size → -size
        close_size = -pos["size"]
        price = await self._sf.get_mark_price(instr["address"])
        if not price or price <= 0:
            logger.warning("[CONTRACT] %s: 无法获取价格，无法平仓", pair)
            return None

        # 以当前 margin 为 amount
        margin_raw = pos["balance"]

        logger.info(
            "[CONTRACT] %s CLOSE | size=%d → %d | margin=%d%s",
            pair, pos["size"], close_size, margin_raw,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            self._active_margins.pop(pair, None)
            return None

        tx_hash = await self._sf.trade(instr["address"], close_size, margin_raw)
        if tx_hash:
            logger.info("[CONTRACT] %s 平仓成功: %s", pair, tx_hash[:20])
            self._active_margins.pop(pair, None)
        return tx_hash

    async def set_stop_loss(self, pair: str, stop_price: float,
                            pos_side: str = "long", size_contracts: str = "") -> Optional[str]:
        """
        止损声明。

        注意：链上无原生止损，此方法仅记录日志并由上层应用定期检查并执行平仓。

        Returns:
            "managed" — 止损由应用层管理
        """
        logger.info(
            "[CONTRACT] %s SL @ %.2f | pos=%s | 注意: 止损由应用层轮询执行",
            pair, stop_price, pos_side,
        )
        return "managed"

    async def set_take_profit(self, pair: str, target_price: float,
                              pos_side: str = "long", size_contracts: str = "") -> Optional[str]:
        """
        止盈声明。

        注意：链上无原生止盈，此方法仅记录日志并由上层应用定期检查并执行平仓。

        Returns:
            "managed" — 止盈由应用层管理
        """
        logger.info(
            "[CONTRACT] %s TP @ %.2f | pos=%s | 注意: 止盈由应用层轮询执行",
            pair, target_price, pos_side,
        )
        return "managed"

    async def adjust_margin(self, pair: str, amount: float,
                            pos_side: str = "long",
                            direction: str = "add") -> Optional[dict]:
        """
        调整保证金（链上当前不支持独立的 margin 调整，通过 trade 实现）。

        Args:
            pair: 交易对
            amount: USD 金额
            pos_side: 方向
            direction: "add" | "reduce"

        Returns:
            dict with {"info": "app_managed", ...} or None
        """
        raw_amount = int(amount * (10 ** USDC_DECIMALS))
        logger.info(
            "[CONTRACT] %s %s margin %.2f USD (%d raw) | 注意: 需通过调整仓位实现",
            pair, direction, amount, raw_amount,
        )
        return {"info": "app_managed", "amount_raw": raw_amount}

    # ── 查询 ────────────────────────────────────────────────────────

    async def get_position(self, pair: str) -> Optional[dict]:
        """获取当前持仓信息。

        Returns:
            {size, balance, entryNotional, ...} or None
        """
        instr = await self._sf.resolve_instrument(pair)
        if not instr:
            return None
        pos = await self._sf.get_position(instr["address"])
        if not pos or pos["size"] == 0:
            return None

        price = await self._sf.get_mark_price(instr["address"])
        side = "long" if pos["size"] > 0 else "short"
        size_usd = abs(pos["size"]) * (price or 0) / (10 ** 18)

        return {
            "pair": pair,
            "side": side,
            "size": pos["size"],
            "size_usd": size_usd,
            "margin_usd": pos["balance"] / (10 ** USDC_DECIMALS),
            "entry_notional": pos["entry_notional"],
            "mark_price": price,
        }

    async def get_positions(self) -> list[dict]:
        """获取所有 USDC 质押品的合约持仓。"""
        await self._sf.discover_instruments()
        positions = []
        for symbol, instr in self._sf._instruments.items():
            pos = await self._sf.get_position(instr["address"])
            if pos and pos["size"] != 0:
                price = await self._sf.get_mark_price(instr["address"])
                side = "long" if pos["size"] > 0 else "short"
                positions.append({
                    "pair": symbol,
                    "side": side,
                    "size": pos["size"],
                    "size_usd": abs(pos["size"]) * (price or 0) / (10 ** 18),
                    "margin_usd": pos["balance"] / (10 ** USDC_DECIMALS),
                    "entry_notional": pos["entry_notional"],
                    "mark_price": price,
                })
        return positions

    async def get_balance(self) -> Optional[dict]:
        """获取账户余额（Vault 中的 USDC + 钱包 USDC）。"""
        vault_raw = await self._sf.get_vault_balance()
        wallet_raw = await self._sf.get_usdc_balance()
        if vault_raw is None and wallet_raw is None:
            return None
        return {
            "vault_usdc": (vault_raw or 0) / (10 ** USDC_DECIMALS),
            "wallet_usdc": (wallet_raw or 0) / (10 ** USDC_DECIMALS),
            "total_usdc": ((vault_raw or 0) + (wallet_raw or 0)) / (10 ** USDC_DECIMALS),
        }

    async def get_ticker(self, pair: str) -> Optional[dict]:
        """获取最新 ticker（标记价格）。"""
        instr = await self._sf.resolve_instrument(pair)
        if not instr:
            return None
        price = await self._sf.get_mark_price(instr["address"])
        if price is None:
            return None
        return {
            "pair": pair,
            "last": price,
            "mark": price,
        }

    # ── 内部 ─────────────────────────────────────────────────────────

    async def _open_position(
        self,
        pair: str,
        margin_usd: float,
        leverage: int,
        is_long: bool,
    ) -> Optional[str]:
        """开仓通用逻辑。"""
        instr = await self._sf.resolve_instrument(pair)
        if not instr:
            logger.warning("[CONTRACT] %s: 无法解析 instrument", pair)
            return None

        instrument_addr = instr["address"]
        lev = self._resolve_leverage(pair, leverage)

        # 1. 获取当前价格
        price = await self._sf.get_mark_price(instrument_addr)
        if not price or price <= 0:
            logger.warning("[CONTRACT] %s: 无法获取价格", pair)
            return None

        # 2. 计算参数
        margin_raw = int(margin_usd * (10 ** USDC_DECIMALS))          # USDC raw
        notional = margin_usd * lev                                    # 名义价值 USD
        base_decimals = 18                                              # 多数 base token 用 18
        size_raw = int(notional / price * (10 ** base_decimals))        # base raw
        if size_raw <= 0:
            logger.warning("[CONTRACT] %s: margin=%.2f lev=%d → size=%d 为 0，跳过",
                           pair, margin_usd, lev, size_raw)
            return None

        size_signed = size_raw if is_long else -size_raw

        direction = "LONG" if is_long else "SHORT"
        logger.info(
            "[CONTRACT] %s OPEN %s | margin=%.2fUSD lev=%dx size=%d%s",
            pair, direction, margin_usd, lev, size_raw,
            " (DRY-RUN)" if self._dry_run else "",
        )

        if self._dry_run:
            self._active_margins[pair] = margin_usd
            return None

        # 3. 检查 Vault 余额，不足时从钱包充值
        vault_bal = await self._sf.get_vault_balance()
        if vault_bal is None or vault_bal < margin_raw:
            shortage = margin_raw - (vault_bal or 0)
            logger.info("[CONTRACT] %s: Vault 余额不足(need=%d have=%d)，存入 %d",
                        pair, margin_raw, vault_bal or 0, shortage)
            # 检查钱包 USDC 余额
            wallet_bal = await self._sf.get_usdc_balance()
            if wallet_bal is None or wallet_bal < shortage:
                logger.warning("[CONTRACT] %s: 钱包余额也不足 (need=%d have=%d)",
                               pair, shortage, wallet_bal or 0)
                return None
            # 检查 approve
            gate_addr = "0x208B443983D8BcC8578e9D86Db23FbA547071270"
            allowance = await self._sf.get_usdc_allowance(gate_addr)
            if allowance is None or allowance < shortage:
                logger.info("[CONTRACT] Approve USDC → Gate")
                tx = await self._sf.approve_usdc(gate_addr)
                if not tx:
                    logger.warning("[CONTRACT] Approve 失败")
                    return None
                # 等待 approve 确认（简化：仅用 3 秒）
                await asyncio.sleep(3)

            # 存款
            tx = await self._sf.deposit(shortage)
            if not tx:
                logger.warning("[CONTRACT] 存入 Vault 失败")
                return None
            await asyncio.sleep(3)

        # 4. 设置杠杆
        await self._sf.set_leverage(instrument_addr, lev)
        await asyncio.sleep(0.5)

        # 5. 执行交易
        tx_hash = await self._sf.trade(instrument_addr, size_signed, margin_raw)
        if tx_hash:
            logger.info("[CONTRACT] %s %s 成功: %s", pair, direction, tx_hash[:20])
            self._active_margins[pair] = margin_usd

        return tx_hash

    def _resolve_leverage(self, pair: str, leverage: int) -> int:
        """确定杠杆倍数，受最大杠杆约束。"""
        lev = leverage if leverage > 0 else self._default_leverage
        max_lev = self._max_leverage_main if pair in MAIN_PAIRS else self._max_leverage_alt
        if lev > max_lev:
            logger.warning("[CONTRACT] %s: 杠杆 %dx 超过最大值 %dx，降级", pair, lev, max_lev)
            lev = max_lev
        return lev
