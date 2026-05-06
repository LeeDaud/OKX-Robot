"""
Unicorn Sniper: Virtuals 打新狙击策略。

流程：
1. 通过 SignalHub API 发现新项目
2. 解析链上 Anti-Sniper 窗口配置
3. 根据大户持仓数据和窗口配置决定是否入场
4. 窗口结束时执行买入
5. 持仓后通过 WhaleExitGuard 监控退出时机
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from src.config.loader import SniperConfig
from src.executor.trader import USDC_BASE
from src.strategy.virtuals_client import VirtualsClubClient, WhalePosition, UpcomingProject
from src.strategy.whale_exit_guard import WhaleExitGuard, WhaleAnalysis
from src.db.database import insert_buy, insert_sell

logger = logging.getLogger(__name__)

# Virtuals 协议常量（Base 链）
VIRTUALS_FACTORY = "0x4A2bB9F1C0E5d42C3f1A1d5D7b8E9F0C1D2E3F4A"
BONDING_CURVE_ABI = '[{"inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"getBuyTax","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"getSellTax","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"getAntiSniperWindow","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"getLaunchTime","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'

# TODO: 以下选择器需用 keccak256 计算，部署前由用户填入。
# Ethereum 函数选择器 = keccak256("fn(type...)")[:4]
_BUY_TAX_SIG = "0x00000000"     # getBuyTax(address)
_SELL_TAX_SIG = "0x00000000"    # getSellTax(address)
_WINDOW_SIG = "0x00000000"      # getAntiSniperWindow(address)
_LAUNCH_SIG = "0x00000000"      # getLaunchTime(address)


@dataclass
class SniperTarget:
    """一个狙击标的。"""
    project: UpcomingProject
    window_seconds: int = 0          # Anti-Sniper 窗口长度（秒）
    launch_time: float = 0           # 上线时间戳
    tax_buy: float = 0.99            # 当前买入税率
    entry_price_v: float | None = None
    status: str = "monitoring"       # monitoring | entered | exited
    position_amount: int = 0         # 持仓量（raw）
    cost_usd: float = 0
    whale_analysis: WhaleAnalysis | None = None
    entered_at: float = 0


class UnicornSniper:
    """Unicorn 打新狙击策略。"""

    def __init__(
        self,
        vclub: VirtualsClubClient,
        w3,
        okx,
        trader,
        config: SniperConfig,
        guard: WhaleExitGuard,
        notifier,
        state_mgr,
        dry_run: bool = True,
    ) -> None:
        self._vclub = vclub
        self._w3 = w3
        self._okx = okx
        self._trader = trader
        self._config = config
        self._guard = guard
        self._notifier = notifier
        self._state_mgr = state_mgr
        self._dry_run = dry_run
        self._targets: list[SniperTarget] = []
        self._known_project_ids: set[str] = set()
        self._initialized = False

    async def initialize(self) -> bool:
        """从持久化恢复状态 + 首次登录。"""
        loaded = self._load_state()
        if loaded:
            self._targets = loaded
            self._known_project_ids = {t.project.project_id for t in loaded}
            logger.info("[SNIPER] 从状态恢复: %d targets", len(self._targets))

        logged_in = await self._vclub.login()
        if not logged_in:
            logger.warning("[SNIPER] virtuals.club 登录失败，仅恢复已有目标")
        self._initialized = True
        return True

    async def tick(self) -> None:
        """单次轮询：发现新项目 + 检查已入场目标的退出信号。"""
        if not self._initialized:
            return

        # 1. 发现新项目
        projects = await self._vclub.fetch_upcoming_projects(
            within_hours=72, limit=self._config.leaderboard_top_n
        )
        for p in projects:
            if p.project_id in self._known_project_ids:
                continue
            self._known_project_ids.add(p.project_id)

            if not p.pool_address:
                logger.debug("[SNIPER] %s 尚无内盘地址，跳过", p.symbol)
                continue

            # 检查池子状态
            launch_info = await self._check_chain_config(p)
            if launch_info is None:
                continue

            window_sec, launch_ts = launch_info
            if window_sec < self._config.min_window_sec:
                logger.info("[SNIPER] %s 窗口太短(%ds)，跳过", p.symbol, window_sec)
                continue

            # 拉 WhaleRadar leaderboard 评估
            h = await self._evaluate_project(p)
            if h is None:
                logger.info("[SNIPER] %s 评估不通过，跳过", p.symbol)
                continue

            target = SniperTarget(
                project=p,
                window_seconds=window_sec,
                launch_time=launch_ts,
                whale_analysis=h,
            )
            self._targets.append(target)
            logger.info("[SNIPER] 新目标: %s window=%ds launch=%.0f",
                        p.symbol, window_sec, launch_ts)

        # 2. 检查目标状态
        changed = False
        for target in self._targets:
            if target.status == "exited":
                continue

            now = time.time()
            elapsed = now - target.launch_time if target.launch_time > 0 else 0

            if target.status == "monitoring":
                # 窗口结束了吗？
                if elapsed >= target.window_seconds:
                    await self._execute_entry(target)
                    changed = True

            elif target.status == "entered":
                # 刷新 WhaleRadar 数据，检查退出信号
                if elapsed % 60 < self._config.poll_interval_sec:
                    positions = await self._vclub.fetch_leaderboard(
                        target.project.token_address,
                        top_n=self._config.leaderboard_top_n,
                    )
                    if positions:
                        current_price = await self._get_token_price_v(target)
                        analysis = self._guard.analyze(positions, current_price)
                        target.whale_analysis = analysis

                        if self._guard.should_exit(analysis):
                            await self._execute_exit(target)
                            changed = True

        if changed:
            self._save_state()

    async def _check_chain_config(self, project: UpcomingProject) -> tuple[int, float] | None:
        """从链上读取 Anti-Sniper 窗口配置和上线时间。"""
        try:
            token = project.token_address or project.contract_address
            if not token:
                return None

            # 查询 Anti-Sniper 窗口长度
            window_data = await self._w3.eth.call({
                "to": VIRTUALS_FACTORY,
                "data": _WINDOW_SIG + token[2:].zfill(64),
            })
            window_sec = int(window_data, 16) if window_data and window_data != "0x" else 0

            # 查询上线时间
            launch_data = await self._w3.eth.call({
                "to": VIRTUALS_FACTORY,
                "data": _LAUNCH_SIG + token[2:].zfill(64),
            })
            launch_ts = int(launch_data, 16) if launch_data and launch_data != "0x" else 0

            return (window_sec, launch_ts)
        except Exception as e:
            logger.warning("[SNIPER] 链上配置查询失败 %s: %s", project.symbol, e)
            return None

    async def _evaluate_project(self, project: UpcomingProject) -> WhaleAnalysis | None:
        """评估一个项目是否值得狙击。"""
        positions = await self._vclub.fetch_leaderboard(
            project.token_address or project.contract_address,
            top_n=self._config.leaderboard_top_n,
        )

        if not positions:
            # leaderboard 无数据，可能初期无 whale 或接口未就绪
            logger.info("[SNIPER] %s: leaderboard 暂无数据，通过", project.symbol)
            return None

        current_price = await self._get_token_price_v(project)
        analysis = self._guard.analyze(positions, current_price)

        # 评估
        if analysis.concentration_pct > self._config.max_concentration_pct:
            logger.info("[SNIPER] %s: 大户集中度过高(%.1f%%), 跳过",
                        project.symbol, analysis.concentration_pct)
            return None

        if self._guard.should_warn(analysis):
            logger.info("[SNIPER] %s: 触发 WARN, 跳过 (avg_cost=%.6f)",
                        project.symbol, analysis.avg_cost_v or 0)
            return None

        logger.info("[SNIPER] %s: 评估通过 whales=%d conc=%.1f%%",
                    project.symbol, analysis.whale_count, analysis.concentration_pct)
        return analysis

    async def _execute_entry(self, target: SniperTarget) -> None:
        """窗口结束执行买入。"""
        if not self._config.buy_amount_usdc > 0:
            return

        token = target.project.token_address or target.project.contract_address
        if not token:
            logger.warning("[SNIPER] %s: 无 token 地址，跳过买入", target.project.symbol)
            target.status = "exited"
            return

        amount_raw = int(self._config.buy_amount_usdc * 1e6)
        source = f"sniper_entry_{target.project.project_id}_{time.time_ns()}"

        tx_hash, filled_raw = await self._trader.buy(
            token, amount_raw,
            payment_token=USDC_BASE, payment_decimals=6,
            source_tx=source,
        )

        if tx_hash and filled_raw > 0:
            cost = self._config.buy_amount_usdc
            target.status = "entered"
            target.position_amount = filled_raw
            target.cost_usd = cost
            target.entered_at = time.time()
            target.entry_price_v = cost / (filled_raw / 1e18) if filled_raw > 0 else 0

            await insert_buy(tx_hash, token, amount_raw, filled_raw,
                             strategy="sniper", cost_usd=cost,
                             filled_amount=str(filled_raw))

            await self._notifier.notify_trade(
                source, target.project.symbol, target.project.symbol,
                USDC_BASE, token, cost, "USDC",
                tx_hash, self._dry_run, side="buy",
                wallet_label="Sniper",
            )
            logger.info("[SNIPER] ✅ %s 买入成功 tx=%s filled=%d",
                        target.project.symbol, tx_hash[:12], filled_raw)
        else:
            reason = self._trader.last_skip_reason or "执行失败"
            await self._notifier.notify_trade(
                source, target.project.symbol, target.project.symbol,
                USDC_BASE, token, self._config.buy_amount_usdc, "USDC",
                None, self._dry_run, side="buy",
                skip_reason=reason, wallet_label="Sniper",
            )
            logger.info("[SNIPER] %s 买入跳过: %s", target.project.symbol, reason)

    async def _execute_exit(self, target: SniperTarget) -> None:
        """执行退出（根据 whale 信号）。"""
        token = target.project.token_address or target.project.contract_address
        if not token or target.position_amount <= 0:
            target.status = "exited"
            return

        source = f"sniper_exit_{target.project.project_id}_{time.time_ns()}"
        tx_hash = await self._trader.sell(
            token, token_out=USDC_BASE,
            amount=target.position_amount, source_tx=source,
        )

        if tx_hash:
            exit_quote = await self._okx.get_quote(token, USDC_BASE, target.position_amount)
            exit_usd = float(exit_quote.get("toTokenAmount", 0)) / 1e6 if exit_quote else 0.0

            pnl = exit_usd - target.cost_usd
            roi = (pnl / target.cost_usd * 100) if target.cost_usd > 0 else 0.0

            await insert_sell(tx_hash, token, target.position_amount, 0,
                             strategy="sniper_exit", cost_usd=target.cost_usd,
                             pnl_usd=pnl, roi=roi)

            await self._notifier.notify_trade(
                source, target.project.symbol, "USDC",
                token, USDC_BASE, exit_usd, "USDC",
                tx_hash, self._dry_run, side="sell",
                roi_pct=roi, pnl_usd=pnl, wallet_label="Sniper",
            )

            target.status = "exited"
            logger.info("[SNIPER] ✅ %s 退出成功 pnl=%.2f roi=%.1f%%",
                        target.project.symbol, pnl, roi)
        else:
            logger.info("[SNIPER] %s 退出跳过: %s",
                        target.project.symbol, self._trader.last_skip_reason)

    async def _get_token_price_v(self, project: UpcomingProject) -> float | None:
        """获取当前代币的 VIRTUAL 计价价格。"""
        token = project.token_address or project.contract_address
        if not token:
            return None
        try:
            quote = await self._okx.get_quote(USDC_BASE, token, int(0.1 * 1e6))
            if quote is None:
                return None
            to_amount = float(quote.get("toTokenAmount", "0"))
            if to_amount <= 0:
                return None
            to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
            token_amount = to_amount / (10 ** to_decimals)
            return 0.1 / token_amount if token_amount > 0 else None
        except Exception as e:
            logger.debug("[SNIPER] 价格查询失败: %s", e)
            return None

    def _save_state(self) -> None:
        self._state_mgr.update(**{
            "sniper_targets": [
                {
                    "project_id": t.project.project_id,
                    "project_name": t.project.name,
                    "project_symbol": t.project.symbol,
                    "token_address": t.project.token_address,
                    "pool_address": t.project.pool_address,
                    "window_seconds": t.window_seconds,
                    "launch_time": t.launch_time,
                    "status": t.status,
                    "position_amount": t.position_amount,
                    "cost_usd": t.cost_usd,
                    "entry_price_v": t.entry_price_v,
                    "entered_at": t.entered_at,
                }
                for t in self._targets
            ],
        })

    def _load_state(self) -> list[SniperTarget] | None:
        state = self._state_mgr.load()
        raw = state.get("sniper_targets")
        if not raw:
            return None
        targets = []
        for r in raw:
            t = SniperTarget(
                project=UpcomingProject(
                    project_id=str(r.get("project_id", "")),
                    name=str(r.get("project_name", "")),
                    symbol=str(r.get("project_symbol", "")),
                    token_address=str(r.get("token_address", "")),
                    contract_address=str(r.get("token_address", "")),
                    pool_address=str(r.get("pool_address", "")),
                    status="",
                    launch_time=str(r.get("launch_time", "")),
                    risk_level="medium",
                    lifecycle_stage="launch_announced",
                    url="",
                ),
                window_seconds=int(r.get("window_seconds", 0)),
                launch_time=float(r.get("launch_time", 0)),
                status=str(r.get("status", "monitoring")),
                position_amount=int(r.get("position_amount", 0)),
                cost_usd=float(r.get("cost_usd", 0)),
                entry_price_v=float(r.get("entry_price_v")) if r.get("entry_price_v") else None,
                entered_at=float(r.get("entered_at", 0)),
            )
            targets.append(t)
        return targets
