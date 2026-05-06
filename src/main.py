"""
主入口：套利机器人，启动所有策略监控器。
用法：
  python src/main.py --dry-run
  python src/main.py --live
  python src/main.py --check-config
"""
import asyncio
import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal

from src.config.loader import load_config, Config
from src.db.database import (
    init_db, insert_buy, insert_sell, get_pending_trades,
    confirm_tx, get_open_positions, get_open_position_by_token,
    get_today_pnl,
)
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader, USDC_BASE
from src.monitor.buyback import BuybackMonitor, BuybackEvent
from src.notify.feishu import FeishuNotifier
from src.strategy.grid import GridStrategy
from src.strategy.unicorn_sniper import UnicornSniper
from src.strategy.whale_exit_guard import WhaleExitGuard
from src.strategy.virtuals_client import VirtualsClubClient
from src.risk.guard import DailyLossGuard
from src.risk.take_profit import TakeProfitMonitor
from src.rpc.router import RPCRouter
from src.state.persistence import StateManager, StrategyState, ProcessLock

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

CST = timezone(timedelta(hours=8))


def _parse_received_amount(logs: list, target_token: str, wallet_addr: str) -> int:
    """从 receipt logs 解析 target_token 转入钱包的总量（独立版）。"""
    from src.executor.trader import TRANSFER_TOPIC
    target_lower = target_token.lower()
    wallet_padded = "0x" + "0" * 24 + wallet_addr[2:]
    topic0_hex = TRANSFER_TOPIC.lstrip("0x").lower()

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


def validate_config(cfg: Config) -> list[str]:
    issues: list[str] = []
    if cfg.base_token not in {"VIRTUAL", "USDC"}:
        issues.append(f"base_token must be 'VIRTUAL' or 'USDC', got '{cfg.base_token}'")
    if cfg.daily_loss_limit_usd < 0:
        issues.append("daily_loss_limit_usd must be >= 0")
    if not 0 <= cfg.slippage <= 1:
        issues.append("slippage must be between 0 and 1")
    if cfg.gas_limit_gwei < 0:
        issues.append("gas_limit_gwei must be >= 0")
    if cfg.take_profit_roi < 0:
        issues.append("take_profit_roi must be >= 0")
    if cfg.take_profit_check_sec <= 0:
        issues.append("take_profit_check_sec must be > 0")
    if cfg.poll_interval_sec <= 0:
        issues.append("poll_interval_sec must be > 0")

    required = {
        "RPC_HTTP_URL": cfg.rpc_http_url,
        "PRIVATE_KEY": cfg.private_key,
        "WALLET_ADDRESS": cfg.wallet_address,
        "OKX_API_KEY": cfg.okx_api_key,
        "OKX_SECRET_KEY": cfg.okx_secret_key,
        "OKX_PASSPHRASE": cfg.okx_passphrase,
    }
    for name, val in required.items():
        if not str(val).strip():
            issues.append(f"{name} must not be empty")
    return issues


def check_config() -> None:
    cfg = load_config()
    issues = validate_config(cfg)
    if issues:
        for i in issues:
            logger.error("Config check failed: %s", i)
        raise SystemExit(1)

    logger.info("Config check passed | dry_run=%s | base_token=%s | buyback_pairs=%d | dca=%s",
                cfg.dry_run, cfg.base_token, len(cfg.buyback_watch),
                "enabled" if cfg.dca.enabled else "disabled")


async def _get_usdc_balance(w3, wallet: str) -> float:
    """查询钱包 USDC 余额。"""
    from src.executor.trader import ERC20_BALANCE_ABI
    from web3 import AsyncWeb3
    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(USDC_BASE),
        abi=ERC20_BALANCE_ABI,
    )
    raw = await contract.functions.balanceOf(AsyncWeb3.to_checksum_address(wallet)).call()
    return raw / 1e6


async def _get_token_price_usd(okx: OKXDexClient, token: str) -> float | None:
    """通过 OKX 报价估算代币的 USDC 价格。"""
    # 用 0.1 USDC 查价，避免大额报价影响
    quote = await okx.get_quote(USDC_BASE, token, int(0.1 * 1e6))
    if quote is None:
        return None
    to_amount = float(quote.get("toTokenAmount", "0"))
    if to_amount <= 0:
        return None
    # 0.1 USDC 能买 to_amount 个代币（raw），价格 = 0.1 / (to_amount / 10^18)
    # 但我们不知道 decimals，所以用 toToken 返回的 decimals
    to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
    token_amount = to_amount / (10 ** to_decimals)
    return 0.1 / token_amount if token_amount > 0 else None


def _in_window(hour: int, minute: int, window_minutes: int, now: datetime) -> bool:
    """检查当前时间是否在 (hour:minute, hour:minute + window_minutes) 窗口内。
    特殊处理 hour=24（次日 00:00）。"""
    if hour == 24:
        # 假设 day_start 是当天的 00:00，窗口是次日的 00:00 ~ 00:00+window
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = day_start + timedelta(days=1)
    else:
        window_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=window_minutes)
    return window_start <= now < window_end


async def recover_pending_trades(w3: RPCRouter, wallet_addr: str) -> None:
    """启动时恢复 pending 交易。"""
    from web3.exceptions import TransactionNotFound
    pending = await get_pending_trades()
    if not pending:
        return

    wallet_addr = wallet_addr.lower()
    logger.info("发现 %d 笔待恢复交易", len(pending))
    for pt in pending:
        tx_hash = pt.get("our_tx_hash", "")
        if not tx_hash:
            continue
        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
            if receipt is None:
                logger.info("[RECOVER] %s: 尚未上链，跳过", tx_hash[:12])
                continue
            if receipt.get("status") == 1:
                filled_raw = 0
                try:
                    filled_raw = _parse_received_amount(
                        [dict(log) for log in receipt.get("logs", [])],
                        pt.get("token_address", ""),
                        wallet_addr,
                    )
                except Exception:
                    pass
                await confirm_tx(pt["tx_hash"], "success", str(filled_raw))
                logger.info("[RECOVER] %s: 交易已确认，回填成交", tx_hash[:12])
            else:
                await confirm_tx(pt["tx_hash"], "failed")
                logger.warning("[RECOVER] %s: 链上失败", tx_hash[:12])
        except TransactionNotFound:
            logger.warning("[RECOVER] %s: 交易未上链，标记为失败", tx_hash[:12])
            await confirm_tx(pt["tx_hash"], "failed")
        except Exception as e:
            logger.warning("[RECOVER] %s: 恢复失败: %s", tx_hash[:12], e)


async def run(dry_run_override: bool | None = None) -> None:
    cfg = load_config()
    if dry_run_override is not None:
        cfg.dry_run = dry_run_override

    logger.info("Starting Auto Trader | dry_run=%s | base_token=%s",
                cfg.dry_run, cfg.base_token)

    # 进程锁
    lock = ProcessLock()
    if not lock.acquire():
        logger.error("无法获取进程锁，可能已有实例在运行")
        return

    await init_db()

    w3 = RPCRouter(cfg.rpc_http_url, cfg.rpc_http_url_fallback)

    await recover_pending_trades(w3, cfg.wallet_address)

    guard = DailyLossGuard(cfg.daily_loss_limit_usd)
    guard.record_pnl(await get_today_pnl())

    notifier = FeishuNotifier(cfg.feishu_webhook_url)
    state_mgr = StateManager()
    strategy_state = StrategyState(state_mgr)

    # ── DCA 每日定投 ──────────────────────────────────────────────

    async def dca_loop(stop: asyncio.Event):
        if not cfg.dca.enabled or not cfg.dca.tokens:
            logger.info("DCA 未启用，跳过")
            return

        logger.info("DCA 启动: %d 个代币", len(cfg.dca.tokens))
        while not stop.is_set():
            try:
                now_utc = datetime.now(timezone.utc)
                # 转换为北京时间用于策略时间判断
                now_cst = datetime.now(CST)
                for dc in cfg.dca.tokens:
                    in_win = _in_window(dc.hour, dc.minute, dc.window_minutes, now_cst)
                    if not in_win:
                        continue

                    # 检查今天是否已经买过
                    last_run = strategy_state.get_last_run(f"dca_{dc.address}")
                    if last_run and last_run.date() == now_utc.date():
                        continue

                    if not guard.can_trade():
                        logger.warning("[DCA] 风控已触发，跳过定投")
                        continue

                    amount = int(dc.amount_usdc * 1e6)  # USDC 6 decimals
                    logger.info("[DCA] 执行定投: %s amount=%.2f USDC",
                                dc.address[:10], dc.amount_usdc)

                    # 先通知
                    await notifier.notify_alert(f"🔄 DCA 定投触发: {dc.amount_usdc} USDC → {dc.address[:10]}")

                    tx_hash, filled_raw = await trader.buy(
                        dc.address, amount,
                        payment_token=USDC_BASE, payment_decimals=6,
                        source_tx=f"dca_{time.time_ns()}",
                    )

                    if tx_hash and filled_raw > 0:
                        cost_usd = dc.amount_usdc
                        price_usd = cost_usd / (filled_raw / 1e18) if filled_raw > 0 else 0
                        await insert_buy(tx_hash, dc.address, amount, filled_raw,
                                         strategy="dca", cost_usd=cost_usd,
                                         filled_amount=str(filled_raw))
                        strategy_state.set_last_run(f"dca_{dc.address}")
                        guard.record_pnl(-cost_usd)
                        await notifier.notify_trade("dca", dc.address[:10], dc.address[:10],
                                                     USDC_BASE, dc.address, cost_usd, "USDC",
                                                     tx_hash, cfg.dry_run, side="buy",
                                                     wallet_label="DCA")
                        logger.info("[DCA] 定投成功: tx=%s filled=%d", tx_hash[:12], filled_raw)
                    else:
                        reason = trader.last_skip_reason or "执行失败"
                        await notifier.notify_trade("dca", dc.address[:10], dc.address[:10],
                                                     USDC_BASE, dc.address, dc.amount_usdc, "USDC",
                                                     None, cfg.dry_run, side="buy",
                                                     skip_reason=reason, wallet_label="DCA")
                        logger.info("[DCA] 跳过: %s", reason)

            except Exception as e:
                logger.error("[DCA] 循环异常: %s", e)

            await asyncio.sleep(60)

    # ── Buyback 回购监控 ─────────────────────────────────────────

    async def on_buyback(event: BuybackEvent) -> None:
        """检测到回购事件时，卖出对应持仓。"""
        try:
            if not guard.can_trade():
                logger.info("[BUYBACK] 风控已触发，跳过卖出")
                return

            # 查对应持仓
            pos = await get_open_position_by_token(event.token_addr)
            if pos is None:
                logger.info("[BUYBACK] 无持仓: %s，跳过", event.token_addr[:10])
                return

            # 确定卖出数量
            filled_raw = pos.get("filled_amount")
            if filled_raw:
                sell_amount = int(filled_raw)
                cost_basis = pos.get("cost_usd", 0.0)
            else:
                sell_amount = int(pos.get("amount_out", 0))
                cost_basis = pos.get("cost_usd", 0.0)

            if sell_amount <= 0 or cost_basis <= 0:
                logger.info("[BUYBACK] 持仓无效: amount=%d cost=%.2f", sell_amount, cost_basis)
                return

            # 执行卖出
            tx_hash = await trader.sell(event.token_addr, source_tx=event.tx_hash)
            if not tx_hash:
                logger.info("[BUYBACK] 卖出跳过: %s", trader.last_skip_reason)
                return

            # 算盈亏
            exit_quote = await okx.get_quote(event.token_addr, USDC_BASE, sell_amount)
            exit_usd = 0.0
            if exit_quote:
                exit_usd = float(exit_quote.get("toTokenAmount", 0)) / 1e6

            pnl = exit_usd - cost_basis
            roi = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0

            await insert_sell(
                tx_hash, event.token_addr, sell_amount, 0,
                strategy="buyback_sell", cost_usd=cost_basis,
                pnl_usd=pnl, roi=roi,
            )

            await notifier.notify_trade(
                event.tx_hash, event.token_addr[:10], "USDC",
                event.token_addr, USDC_BASE, exit_usd, "USDC",
                tx_hash, cfg.dry_run, side="sell",
                roi_pct=roi, pnl_usd=pnl, wallet_label="Buyback",
                **({"balance_virtual": 0} if cfg.base_token == "VIRTUAL" else {"balance_usdc": 0}),
            )

            logger.info("[BUYBACK] 卖出成功: %s pnl=%.2f roi=%.1f%%",
                        event.token_addr[:10], pnl, roi)

        except Exception as e:
            logger.error("[BUYBACK] 处理失败: %s", e)

    # ── Take Profit 止盈 ─────────────────────────────────────────

    async def on_take_profit(pos: dict, roi_pct: float, pnl_usd: float) -> None:
        symbol = pos.get("token_address", "?")[:10]
        await notifier.notify_take_profit(symbol, pos.get("token_address", ""), roi_pct, pnl_usd)
        logger.info("[TP] 止盈: %s roi=%.1f%% pnl=%.2f", symbol, roi_pct, pnl_usd)

    # ── 定时汇报 ──────────────────────────────────────────────────

    async def hourly_reporter(stop: asyncio.Event):
        """每天 UTC 09/13 汇报两次（CST 17:00/21:00）。"""
        report_hours = [9, 13]
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            now_sec = now.hour * 3600 + now.minute * 60 + now.second
            next_sec = min(
                (h * 3600 - now_sec) % (24 * 3600) or 24 * 3600
                for h in report_hours
            )
            try:
                await asyncio.wait_for(stop.wait(), next_sec)
                return
            except asyncio.TimeoutError:
                pass

            try:
                # 计算各统计
                usdc_balance = await _get_usdc_balance(w3, cfg.wallet_address)
                eth_raw = await w3.eth.get_balance(cfg.wallet_address)
                balance_eth = eth_raw / 1e18

                open_pos = await get_open_positions()
                enriched = []
                for pos in open_pos:
                    token = pos["token_address"]
                    amount = int(pos.get("filled_amount", pos.get("amount_out", 0)))
                    cost = pos.get("cost_usd", 0)
                    current_usd = cost
                    roi = 0.0
                    if amount > 0 and token:
                        q = await okx.get_quote(token, USDC_BASE, amount)
                        if q:
                            current_usd = float(q.get("toTokenAmount", 0)) / 1e6
                            roi = ((current_usd - cost) / cost * 100) if cost > 0 else 0
                    enriched.append({
                        "symbol": token[:10], "token_out": token,
                        "cost_usd": cost, "current_usd": current_usd, "roi_pct": roi,
                    })

                from src.db.database import get_all_stats, get_today_stats
                stats = await get_all_stats()
                today = await get_today_stats()

                await notifier.notify_hourly_report(
                    balance_usdc=usdc_balance, balance_eth=balance_eth,
                    unrealized_pnl=sum(p["current_usd"] - p["cost_usd"] for p in enriched),
                    realized_pnl=stats["realized_pnl"],
                    total_invested=stats["total_invested"],
                    positions=enriched,
                    today_trades=today["total"],
                    today_success=today["success"],
                    today_pnl=today["pnl"],
                )
            except Exception as e:
                logger.warning("Hourly report failed: %s", e)

    # ── 初始化组件 ────────────────────────────────────────────────

    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        trader = Trader(
            w3=w3, okx=okx,
            wallet_addr=cfg.wallet_address,
            private_key=cfg.private_key,
            base_token=cfg.base_token,
            slippage=cfg.slippage,
            gas_limit_gwei=cfg.gas_limit_gwei,
            dry_run=cfg.dry_run,
        )

        buyback_monitor = BuybackMonitor(
            w3=w3,
            watch_pairs=cfg.buyback_watch,
            poll_interval=cfg.poll_interval_sec,
            on_buyback=on_buyback,
        )

        tp_monitor = TakeProfitMonitor(
            okx=okx, trader=trader,
            roi_threshold=cfg.take_profit_roi,
            check_interval=cfg.take_profit_check_sec,
            on_take_profit=on_take_profit,
        )

        # ── Grid 网格交易 ──────────────────────────────────────────

        grid_ok = False
        grid_strategy = None
        if cfg.grid.enabled:
            grid_strategy = GridStrategy(
                okx=okx, trader=trader, config=cfg.grid,
                notifier=notifier, guard=guard,
                state_mgr=state_mgr, dry_run=cfg.dry_run,
            )
            grid_ok = await grid_strategy.initialize()
            if not grid_ok:
                logger.warning("[GRID] 初始化失败，跳过网格策略")
        else:
            logger.info("[GRID] 未启用，跳过")

        async def grid_loop(stop: asyncio.Event):
            if not grid_ok:
                return
            while not stop.is_set():
                try:
                    await grid_strategy.tick()
                except Exception as e:
                    logger.error("[GRID] tick 异常: %s", e)
                await asyncio.sleep(cfg.poll_interval_sec)

        # ── Sniper 狙击策略 ─────────────────────────────────────────

        sniper_ok = False
        sniper_strategy = None
        if cfg.sniper.enabled:
            vclub = VirtualsClubClient(
                base_url=cfg.sniper.virtuals_club_url,
                email=cfg.sniper.email,
                password=cfg.sniper.password,
                leaderboard_path=cfg.sniper.leaderboard_path,
            )
            whale_guard = WhaleExitGuard(
                exit_threshold_pct=1.5,
                warn_threshold_pct=2.0,
                max_concentration_pct=cfg.sniper.max_concentration_pct,
            )
            sniper_strategy = UnicornSniper(
                vclub=vclub, w3=w3, okx=okx, trader=trader,
                config=cfg.sniper, guard=whale_guard,
                notifier=notifier, state_mgr=state_mgr,
                dry_run=cfg.dry_run,
            )
            sniper_ok = await sniper_strategy.initialize()
            if not sniper_ok:
                logger.warning("[SNIPER] 初始化失败，跳过狙击策略")
        else:
            logger.info("[SNIPER] 未启用，跳过")

        async def sniper_loop(stop: asyncio.Event):
            if not sniper_ok:
                return
            while not stop.is_set():
                try:
                    await sniper_strategy.tick()
                except Exception as e:
                    logger.error("[SNIPER] tick 异常: %s", e)
                await asyncio.sleep(cfg.sniper.poll_interval_sec)

        # 启动
        stop_event = asyncio.Event()
        def _shutdown(*_):
            logger.info("收到关闭信号，正在停止...")
            stop_event.set()
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        logger.info("所有组件已初始化，开始运行")

        tasks = [
            asyncio.create_task(buyback_monitor.start()),
            asyncio.create_task(dca_loop(stop_event)),
            asyncio.create_task(tp_monitor.start()),
            asyncio.create_task(hourly_reporter(stop_event)),
            asyncio.create_task(grid_loop(stop_event)),
            asyncio.create_task(sniper_loop(stop_event)),
        ]

        await stop_event.wait()

        await buyback_monitor.stop()
        await tp_monitor.stop()
        for t in tasks:
            t.cancel()

    lock.release()
    logger.info("正常退出")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="强制 dry-run 模式")
    group.add_argument("--live", action="store_true", help="强制 live 模式")
    parser.add_argument("--check-config", action="store_true", help="仅校验配置")
    args = parser.parse_args()

    if args.check_config:
        check_config()
        return

    override = None
    if args.dry_run:
        override = True
    elif args.live:
        override = False

    asyncio.run(run(override))


if __name__ == "__main__":
    main()
