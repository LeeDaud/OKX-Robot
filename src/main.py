"""
主入口：初始化所有组件，启动监控循环。
用法：
  python src/main.py              # 读取 config.yaml 中的 dry_run 设置
  python src/main.py --dry-run    # 强制 dry-run
  python src/main.py --live       # 强制 live 模式（谨慎）
"""
import asyncio
import argparse
import logging
from pathlib import Path
import signal
import sys
import uuid
from datetime import datetime, timezone

from web3 import AsyncWeb3

from src.config.loader import load_config, reload_yaml, TargetConfig, Config
from src.db.database import (
    init_db, insert_trade, update_trade, update_trade_fill,
    get_today_pnl, get_today_stats, get_all_stats,
    get_open_positions, get_open_position_by_token, close_position,
    _amount_to_usd,
)
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader, ERC20_BALANCE_ABI
from src.monitor.decoder import SwapInfo, USDC_BASE, USDT_BASE, VIRTUALS_BASE
from src.monitor.filter import SwapFilter
from src.monitor.token_resolver import TokenResolver
from src.monitor.watcher import AddressWatcher
from src.notify.feishu import FeishuNotifier
from src.risk.guard import DailyLossGuard
from src.risk.take_profit import TakeProfitMonitor
from src.rpc.router import RPCRouter

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

STABLE_TOKENS = {USDC_BASE.lower(), USDT_BASE.lower(), VIRTUALS_BASE.lower()}


def validate_runtime_config(cfg: Config) -> list[str]:
    issues: list[str] = []

    if not cfg.copy_targets:
        issues.append("copy_targets must contain at least one address")
    if cfg.trade_mode not in {"ratio", "fixed"}:
        issues.append(f"trade_mode must be 'ratio' or 'fixed', got '{cfg.trade_mode}'")
    if cfg.trade_ratio < 0:
        issues.append("trade_ratio must be >= 0")
    if cfg.trade_fixed_usd < 0:
        issues.append("trade_fixed_usd must be >= 0")
    if cfg.trade_max_usd < 0:
        issues.append("trade_max_usd must be >= 0")
    if cfg.min_trade_usd < 0:
        issues.append("min_trade_usd must be >= 0")
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
    if not 0 <= cfg.daily_report_hour_utc <= 23:
        issues.append("daily_report_hour_utc must be between 0 and 23")
    if cfg.poll_interval_sec <= 0:
        issues.append("poll_interval_sec must be > 0")

    required_values = {
        "rpc_ws_url": cfg.rpc_ws_url,
        "rpc_http_url": cfg.rpc_http_url,
        "private_key": cfg.private_key,
        "wallet_address": cfg.wallet_address,
        "okx_api_key": cfg.okx_api_key,
        "okx_secret_key": cfg.okx_secret_key,
        "okx_passphrase": cfg.okx_passphrase,
    }
    for name, value in required_values.items():
        if not str(value).strip():
            issues.append(f"{name} must not be empty")

    seen_addresses: set[str] = set()
    for idx, target in enumerate(cfg.copy_targets, start=1):
        if not target.address:
            issues.append(f"copy_targets[{idx}] address must not be empty")
            continue
        if target.address in seen_addresses:
            issues.append(f"copy_targets contains duplicate address: {target.address}")
        seen_addresses.add(target.address)
        if target.trade_mode is not None and target.trade_mode not in {"ratio", "fixed"}:
            issues.append(
                f"copy_targets[{idx}] trade_mode must be 'ratio' or 'fixed', got '{target.trade_mode}'"
            )

    return issues


def check_config() -> None:
    cfg = load_config()
    issues = validate_runtime_config(cfg)
    if issues:
        for issue in issues:
            logger.error("Config check failed: %s", issue)
        raise SystemExit(1)

    logger.info(
        "Config check passed | dry_run=%s | targets=%d | trade_mode=%s | poll_interval=%.1fs",
        cfg.dry_run,
        len(cfg.copy_targets),
        cfg.trade_mode,
        cfg.poll_interval_sec,
    )
    for target in cfg.copy_targets:
        logger.info(
            "Target loaded | address=%s | mode=%s",
            target.address,
            target.trade_mode or cfg.trade_mode,
        )


def _is_sell(token_out: str) -> bool:
    return token_out.lower() in STABLE_TOKENS


async def run(dry_run_override: bool | None = None) -> None:
    cfg = load_config()
    if dry_run_override is not None:
        cfg.dry_run = dry_run_override

    logger.info("Starting OKX Robot | dry_run=%s | targets=%d",
                cfg.dry_run, len(cfg.copy_targets))

    await init_db()

    guard = DailyLossGuard(limit_usd=cfg.daily_loss_limit_usd)
    today_pnl = await get_today_pnl()
    guard.record_pnl(today_pnl)

    notifier = FeishuNotifier(cfg.feishu_webhook_url)

    w3 = RPCRouter(cfg.rpc_http_url, cfg.rpc_http_url_fallback)
    swap_filter = SwapFilter(cfg.token_whitelist, cfg.min_trade_usd)
    token_resolver = TokenResolver(w3)

    def _make_trader(okx: OKXDexClient, target: TargetConfig) -> Trader:
        return Trader(
            w3=w3, okx=okx,
            wallet_addr=cfg.wallet_address,
            private_key=cfg.private_key,
            trade_mode=target.trade_mode or cfg.trade_mode,
            trade_ratio=target.trade_ratio if target.trade_ratio is not None else cfg.trade_ratio,
            trade_fixed_usd=target.trade_fixed_usd if target.trade_fixed_usd is not None else cfg.trade_fixed_usd,
            trade_max_usd=target.trade_max_usd if target.trade_max_usd is not None else cfg.trade_max_usd,
            slippage=cfg.slippage,
            gas_limit_gwei=cfg.gas_limit_gwei,
            dry_run=cfg.dry_run,
        )

    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        traders = {t.address: _make_trader(okx, t) for t in cfg.copy_targets}

        async def on_swap(swap: SwapInfo) -> None:
            try:
                if not guard.can_trade():
                    await notifier.notify_risk_halt(abs(guard._loss_today), guard._limit)
                    return

                side = "sell" if _is_sell(swap.token_out) else "buy"
                position_id = None
                entry_price = 0.0
                amount_out = 0.0
                exit_usd = 0.0
                roi_pct = None
                pnl_usd = None
                skip_reason = ""

                # ── 回购检测：回购地址买入指定代币 → 立即卖出对应持仓 ──
                buyback_target = cfg.buyback_watch.get(swap.from_addr.lower())
                if buyback_target and side == "buy" and swap.token_out.lower() == buyback_target:
                    open_pos = await get_open_position_by_token(buyback_target)
                    sell_tx = None
                    if open_pos:
                        filled_raw_pos = open_pos.get("filled_amount")
                        if filled_raw_pos:
                            sell_amount = int(filled_raw_pos)
                            cost_basis = open_pos.get("filled_cost_usd", 0.0)
                        else:
                            sell_amount = int(open_pos.get("amount_out", 0))
                            cost_basis = _amount_to_usd(
                                open_pos.get("amount_in", 0),
                                open_pos.get("token_in", USDC_BASE),
                            )

                        if sell_amount > 0 and cost_basis > 0:
                            token_to_sell = open_pos["token_out"]
                            sell_trader = traders.get(open_pos["source_addr"])
                            sell_tx = await sell_trader.sell(
                                token_to_sell, USDC_BASE, sell_amount
                            ) if sell_trader else None

                            if sell_tx:
                                pnl = round(-cost_basis, 2)
                                roi = -100.0
                                exit_quote = await okx.get_quote(
                                    token_to_sell, USDC_BASE, sell_amount
                                )
                                if exit_quote:
                                    exit_usd = float(exit_quote.get("toTokenAmount", 0)) / 1e6
                                if exit_usd > 0:
                                    pnl = round(exit_usd - cost_basis, 2)
                                    roi = round((pnl / cost_basis * 100) if cost_basis > 0 else 0, 2)
                                    await close_position(open_pos["position_id"], exit_usd, roi, pnl)
                                else:
                                    await close_position(open_pos["position_id"], 0, 0, -cost_basis)

                                await update_trade(swap.tx_hash, sell_tx, "success")

                    # 录入原始交易
                    await insert_trade(
                        swap.tx_hash, swap.from_addr,
                        swap.token_in, swap.token_out, swap.amount_in,
                        side="buy", position_id=open_pos["position_id"] if open_pos else None,
                    )

                    # 通知：回购地址买入检测 + 卖出结果
                    sym_in, sym_out = await asyncio.gather(
                        token_resolver.symbol(swap.token_in),
                        token_resolver.symbol(swap.token_out),
                    )
                    if sell_tx:
                        pnl_display = pnl if exit_usd > 0 else -cost_basis
                        roi_display = roi if exit_usd > 0 else -100.0
                        await notifier.notify_trade(
                            swap.tx_hash, sym_in, f"🔄回购{sym_out}",
                            swap.token_in, swap.token_out,
                            exit_usd if exit_usd > 0 else 0, "USDC",
                            sell_tx, cfg.dry_run,
                            side="sell", roi_pct=roi_display, pnl_usd=pnl_display,
                            balance_usdc=0, balance_eth=0,
                            skip_reason="",
                        )
                        logger.info("Buyback sell sent: %s | token=%s", sell_tx, buyback_target[:10])
                    else:
                        reason = "模拟运行模式" if cfg.dry_run else (
                            sell_trader.last_skip_reason if open_pos and sell_trader else "无持仓或持仓为空"
                        )
                        await notifier.notify_trade(
                            swap.tx_hash, sym_in, sym_out,
                            swap.token_in, swap.token_out,
                            0, "USDC", None, cfg.dry_run,
                            side="sell", balance_usdc=0, balance_eth=0,
                            skip_reason=reason,
                        )
                    return  # skip normal flow

                if side == "buy":
                    position_id = str(uuid.uuid4())
                    # 查入场价：amount_in USDC 能买多少 token_out
                    quote = await okx.get_quote(swap.token_in, swap.token_out, swap.amount_in)
                    if quote:
                        amount_out = float(quote.get("toTokenAmount", 0))
                        cost = _amount_to_usd(swap.amount_in, swap.token_in)
                        entry_price = cost / amount_out if amount_out > 0 else 0
                else:
                    # 卖出：匹配持仓，用机器人实际数量跟卖
                    open_pos = await get_open_position_by_token(swap.token_in)
                    if open_pos:
                        position_id = open_pos["position_id"]

                        # 用机器人实际持仓数量卖出
                        filled_raw_pos = open_pos.get("filled_amount")
                        if filled_raw_pos:
                            sell_amount = int(filled_raw_pos)
                            cost_basis = open_pos.get("filled_cost_usd", 0.0)
                        else:
                            sell_amount = int(open_pos.get("amount_out", 0))
                            cost_basis = _amount_to_usd(
                                open_pos.get("amount_in", 0),
                                open_pos.get("token_in", USDC_BASE),
                            )

                        if sell_amount > 0 and cost_basis > 0:
                            token_to_sell = open_pos["token_out"]
                            sell_trader = traders.get(swap.from_addr)
                            sell_tx = await sell_trader.sell(
                                token_to_sell, USDC_BASE, sell_amount
                            ) if sell_trader else None

                            if sell_tx:
                                our_tx = sell_tx
                                # 查真实出场价值
                                exit_quote = await okx.get_quote(
                                    token_to_sell, USDC_BASE, sell_amount
                                )
                                if exit_quote:
                                    exit_usd = float(exit_quote.get("toTokenAmount", 0)) / 1e6
                                if exit_usd > 0:
                                    pnl_usd = exit_usd - cost_basis
                                    roi_pct = (pnl_usd / cost_basis * 100) if cost_basis > 0 else 0
                                    await close_position(position_id, exit_usd, roi_pct, pnl_usd)
                                    our_amount_usd = exit_usd
                                else:
                                    # 查价失败也平仓，防止止盈监控二次卖出
                                    await close_position(position_id, 0, 0, -cost_basis)

                                await update_trade(swap.tx_hash, sell_tx, "success")
                            elif sell_trader:
                                skip_reason = sell_trader.last_skip_reason or "卖出交易广播失败"
                        else:
                            skip_reason = "持仓数量为空或成本基础为 0"
                    else:
                        skip_reason = "未匹配到对应持仓"

                await insert_trade(
                    swap.tx_hash, swap.from_addr,
                    swap.token_in, swap.token_out, swap.amount_in,
                    side=side, position_id=position_id,
                    entry_price=entry_price, amount_out=amount_out,
                )

                # 计算显示金额：根据 token_in 类型确定小数位数
                if swap.token_in.lower() == USDC_BASE.lower() or swap.token_in.lower() == USDT_BASE.lower():
                    amount_display = swap.amount_in / 1e6
                    amount_unit = "USDC"
                elif swap.token_in.lower() == VIRTUALS_BASE.lower():
                    amount_display = swap.amount_in / 1e18
                    amount_unit = "VIRTUAL"
                else:
                    # 其他代币默认 18 位小数
                    amount_display = swap.amount_in / 1e18
                    amount_unit = "TOKEN"

                # 卖出时如果有出场价值，优先显示
                if side == "sell" and exit_usd > 0:
                    amount_display = exit_usd
                    amount_unit = "USDC"

                symbol_in, symbol_out = await asyncio.gather(
                    token_resolver.symbol(swap.token_in),
                    token_resolver.symbol(swap.token_out),
                )

                trader = traders.get(swap.from_addr)

                # 买入：走 execute（USDC 支付）；卖出：已在上面通过 trader.sell() 处理
                if side == "buy":
                    execute_result = await trader.execute(swap) if trader else None
                    our_tx_buy = execute_result[0] if execute_result else None
                    our_amount_usd_buy = execute_result[1] if execute_result else 0.0
                    filled_raw_buy = execute_result[2] if execute_result else 0

                    if our_tx_buy and filled_raw_buy > 0:
                        await update_trade_fill(
                            swap.tx_hash, our_tx_buy, "success",
                            str(filled_raw_buy), our_amount_usd_buy,
                        )
                        our_tx = our_tx_buy
                        our_amount_usd = our_amount_usd_buy
                    elif our_tx_buy:
                        await update_trade(swap.tx_hash, our_tx_buy, "success")
                        our_tx = our_tx_buy
                    else:
                        if trader:
                            skip_reason = trader.last_skip_reason or "买入交易执行失败"
                        await update_trade(swap.tx_hash, None,
                                           "dry_run" if cfg.dry_run else "failed")
                else:
                    # 卖出如果未产生 our_tx（无持仓/量太小/skip），更新 DB 状态
                    if not our_tx:
                        await update_trade(swap.tx_hash, None,
                                           "dry_run" if cfg.dry_run else "failed")

                # 先发 swap alert（无论是否自动跟单都通知）
                await notifier.notify_swap_alert(
                    swap.tx_hash, symbol_in, symbol_out,
                    swap.token_in, swap.token_out,
                    amount_display, amount_unit, side,
                    auto_followed=bool(our_tx) or cfg.dry_run,
                )

                # 如果有自动跟单结果，再发跟单详情
                if trader is not None:
                    usdc_raw, eth_raw = await asyncio.gather(
                        w3.eth.call({"to": AsyncWeb3.to_checksum_address(USDC_BASE),
                                     "data": "0x70a08231" + "000000000000000000000000" + cfg.wallet_address[2:].lower()}),
                        w3.eth.get_balance(AsyncWeb3.to_checksum_address(cfg.wallet_address)),
                    )
                    balance_usdc = int(usdc_raw.hex(), 16) / 1e6
                    balance_eth = eth_raw / 1e18
                    # 自动跟单时显示实际跟单金额，否则显示跟单钱包金额
                    notify_amount = our_amount_usd if our_amount_usd > 0 else amount_display
                    notify_unit = "USDC" if our_amount_usd > 0 else amount_unit
                    await notifier.notify_trade(
                        swap.tx_hash, symbol_in, symbol_out,
                        swap.token_in, swap.token_out,
                        notify_amount, notify_unit, our_tx, cfg.dry_run,
                        side=side, roi_pct=roi_pct, pnl_usd=pnl_usd,
                        balance_usdc=balance_usdc, balance_eth=balance_eth,
                        skip_reason=skip_reason,
                    )

                if our_tx:
                    logger.info("Trade sent: %s", our_tx)
            except Exception as e:
                logger.error("on_swap failed: %s | tx=%s token=%s",
                             e, swap.tx_hash[:10], swap.token_in[:10])
                try:
                    sym_in = await token_resolver.symbol(swap.token_in)
                    sym_out = await token_resolver.symbol(swap.token_out)
                except Exception:
                    sym_in, sym_out = swap.token_in[:10], swap.token_out[:10]
                await notifier.notify_trade(
                    swap.tx_hash, sym_in, sym_out,
                    swap.token_in, swap.token_out,
                    swap.amount_in / 1e18, "TOKEN", None, cfg.dry_run,
                    side="?", balance_usdc=0, balance_eth=0,
                )

        async def on_take_profit(pos: dict, roi: float, pnl: float) -> None:
            symbol = await token_resolver.symbol(pos["token_out"])
            await notifier.notify_take_profit(symbol, pos["token_out"], roi * 100, pnl)

        async def config_reloader() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    reload_yaml(cfg)
                    swap_filter.update(cfg.token_whitelist, cfg.min_trade_usd)
                    for addr, trader in traders.items():
                        target = next((t for t in cfg.copy_targets if t.address == addr), None)
                        trader._mode = (target.trade_mode if target and target.trade_mode else cfg.trade_mode)
                        trader._ratio = (target.trade_ratio if target and target.trade_ratio is not None else cfg.trade_ratio)
                        trader._fixed_usd = (target.trade_fixed_usd if target and target.trade_fixed_usd is not None else cfg.trade_fixed_usd)
                        trader._max_usd = (target.trade_max_usd if target and target.trade_max_usd is not None else cfg.trade_max_usd)
                        trader._slippage = cfg.slippage
                        trader._dry_run = cfg.dry_run
                    guard._limit = cfg.daily_loss_limit_usd
                    tp_monitor._roi_threshold = cfg.take_profit_roi
                    tp_monitor._interval = cfg.take_profit_check_sec
                    logger.info("Config reloaded: mode=%s ratio=%.2f tp_roi=%.0f%%",
                                cfg.trade_mode, cfg.trade_ratio, cfg.take_profit_roi * 100)
                except Exception as e:
                    logger.warning("Config reload failed: %s", e)

        async def hourly_reporter() -> None:
            # 每天 09:00/12:00/15:00/18:00/21:00 CST（即 UTC 01/04/07/10/13）各汇报一次
            # 21:00（UTC 13）那次附带今日交易统计
            report_hours_utc = [1, 4, 7, 10, 13]
            while True:
                now = datetime.now(timezone.utc)
                now_total_sec = now.hour * 3600 + now.minute * 60 + now.second
                seconds_until = min(
                    (h * 3600 - now_total_sec) % (24 * 3600) or 24 * 3600
                    for h in report_hours_utc
                )
                logger.info("Hourly reporter: next report in %d seconds (%.1f min)", seconds_until, seconds_until / 60)
                await asyncio.sleep(seconds_until)
                logger.info("Hourly reporter: woke up, generating report...")
                try:
                    fire_hour = datetime.now(timezone.utc).hour
                    contract = w3.eth.contract(
                        address=AsyncWeb3.to_checksum_address(USDC_BASE),
                        abi=ERC20_BALANCE_ABI,
                    )
                    raw_balance, eth_raw = await asyncio.gather(
                        contract.functions.balanceOf(AsyncWeb3.to_checksum_address(cfg.wallet_address)).call(),
                        w3.eth.get_balance(AsyncWeb3.to_checksum_address(cfg.wallet_address)),
                    )
                    balance_usdc = raw_balance / 1e6
                    balance_eth = eth_raw / 1e18

                    open_pos = await get_open_positions()
                    unrealized_pnl = 0.0
                    enriched = []
                    for pos in open_pos:
                        token = pos["token_out"]
                        amount_out = pos.get("amount_out", 0)
                        cost_usd = _amount_to_usd(pos.get("amount_in", 0), pos.get("token_in", USDC_BASE))
                        current_usd = cost_usd
                        roi = 0.0
                        if amount_out > 0:
                            q = await okx.get_quote(token, USDC_BASE, int(amount_out))
                            if q:
                                current_usd = float(q.get("toTokenAmount", 0)) / 1e6
                                roi = ((current_usd - cost_usd) / cost_usd * 100) if cost_usd > 0 else 0
                        unrealized_pnl += current_usd - cost_usd
                        sym = await token_resolver.symbol(token)
                        enriched.append({
                            "symbol": sym, "token_out": token,
                            "cost_usd": cost_usd, "current_usd": current_usd, "roi_pct": roi,
                        })

                    stats = await get_all_stats()

                    # 21:00 CST（UTC 13）附带今日统计
                    today_trades = today_success = today_pnl = None
                    if fire_hour == 13:
                        today = await get_today_stats()
                        today_trades = today["total"]
                        today_success = today["success"]
                        today_pnl = today["pnl"]

                    await notifier.notify_hourly_report(
                        balance_usdc=balance_usdc,
                        balance_eth=balance_eth,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=stats["realized_pnl"],
                        total_invested=stats["total_invested"],
                        positions=enriched,
                        today_trades=today_trades,
                        today_success=today_success,
                        today_pnl=today_pnl,
                    )
                except Exception as e:
                    logger.warning("Hourly report failed: %s", e)

        tp_monitor = TakeProfitMonitor(
            okx=okx,
            traders=traders,
            take_profit_roi=cfg.take_profit_roi,
            check_interval=cfg.take_profit_check_sec,
            on_take_profit=on_take_profit,
        )

        watcher = AddressWatcher(
            w3=w3,
            targets=list(set([t.address for t in cfg.copy_targets] + list(cfg.buyback_watch.keys()))),
            on_swap=on_swap,
            swap_filter=swap_filter,
            poll_interval=cfg.poll_interval_sec,
        )

        stop_event = asyncio.Event()

        def _shutdown(*_):
            logger.info("Shutting down...")
            stop_event.set()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        tasks = [
            asyncio.create_task(watcher.start()),
            asyncio.create_task(config_reloader()),
            asyncio.create_task(hourly_reporter()),
            asyncio.create_task(tp_monitor.start()),
        ]
        await stop_event.wait()
        await watcher.stop()
        await tp_monitor.stop()
        for t in tasks:
            t.cancel()


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="强制 dry-run 模式")
    group.add_argument("--live", action="store_true", help="强制 live 模式")
    parser.add_argument("--check-config", action="store_true", help="validate .env and config.yaml only")
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
