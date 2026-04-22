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
import signal
import sys
import uuid
from datetime import datetime, timezone

from web3 import AsyncWeb3

from src.config.loader import load_config, reload_yaml, TargetConfig
from src.db.database import (
    init_db, insert_trade, update_trade,
    get_today_pnl, get_today_stats, get_all_stats,
    get_open_positions, get_open_position_by_token, close_position,
)
from src.executor.okx_client import OKXDexClient
from src.executor.trader import Trader
from src.monitor.decoder import SwapInfo, USDC_BASE, USDT_BASE
from src.monitor.filter import SwapFilter
from src.monitor.token_resolver import TokenResolver
from src.monitor.watcher import AddressWatcher
from src.notify.feishu import FeishuNotifier
from src.risk.guard import DailyLossGuard
from src.risk.take_profit import TakeProfitMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log"),
    ],
)
logger = logging.getLogger("main")

STABLE_TOKENS = {USDC_BASE.lower(), USDT_BASE.lower()}


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

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
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
            if not guard.can_trade():
                await notifier.notify_risk_halt(abs(guard._loss_today), guard._limit)
                return

            side = "sell" if _is_sell(swap.token_out) else "buy"
            position_id = None
            entry_price = 0.0
            amount_out = 0.0
            roi_pct = None
            pnl_usd = None

            if side == "buy":
                position_id = str(uuid.uuid4())
                # 查入场价：amount_in USDC 能买多少 token_out
                quote = await okx.get_quote(swap.token_in, swap.token_out, swap.amount_in)
                if quote:
                    amount_out = float(quote.get("toTokenAmount", 0))
                    entry_price = (swap.amount_in / 1e6) / amount_out if amount_out > 0 else 0
            else:
                # 卖出：匹配最近未平仓买入，计算 ROI
                open_pos = await get_open_position_by_token(swap.token_in)
                if open_pos:
                    position_id = open_pos["position_id"]
                    cost_usd = open_pos["amount_in"] / 1e6
                    exit_usd = swap.amount_in / 1e6  # 卖出时 token_in 是代币，amount_in 是代币数量
                    # 查出场价值
                    quote = await okx.get_quote(swap.token_in, USDC_BASE, swap.amount_in)
                    if quote:
                        exit_usd = float(quote.get("toTokenAmount", 0)) / 1e6
                    pnl_usd = exit_usd - cost_usd
                    roi_pct = (pnl_usd / cost_usd * 100) if cost_usd > 0 else 0
                    exit_price = exit_usd / swap.amount_in if swap.amount_in > 0 else 0
                    await close_position(position_id, exit_price, roi_pct, pnl_usd)

            await insert_trade(
                swap.tx_hash, swap.from_addr,
                swap.token_in, swap.token_out, swap.amount_in,
                side=side, position_id=position_id,
                entry_price=entry_price, amount_out=amount_out,
            )

            trader = traders.get(swap.from_addr)
            if trader is None:
                return

            our_tx = await trader.execute(swap)
            status = "success" if our_tx else ("dry_run" if cfg.dry_run else "failed")
            await update_trade(swap.tx_hash, our_tx, status)

            amount_usd = swap.amount_in / 1e6
            symbol_in, symbol_out, usdc_raw, eth_raw = await asyncio.gather(
                token_resolver.symbol(swap.token_in),
                token_resolver.symbol(swap.token_out),
                w3.eth.call({"to": AsyncWeb3.to_checksum_address(USDC_BASE),
                             "data": "0x70a08231" + "000000000000000000000000" + cfg.wallet_address[2:].lower()}),
                w3.eth.get_balance(AsyncWeb3.to_checksum_address(cfg.wallet_address)),
            )
            balance_usdc = int(usdc_raw.hex(), 16) / 1e6
            balance_eth = eth_raw / 1e18
            await notifier.notify_trade(
                swap.tx_hash, symbol_in, symbol_out,
                swap.token_in, swap.token_out,
                amount_usd, our_tx, cfg.dry_run,
                side=side, roi_pct=roi_pct, pnl_usd=pnl_usd,
                balance_usdc=balance_usdc, balance_eth=balance_eth,
            )

            if our_tx:
                logger.info("Trade sent: %s", our_tx)

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
            # 每天 09:00 和 21:00 UTC+8（即 01:00 和 13:00 UTC）各汇报一次
            report_hours_utc = [1, 13]
            while True:
                now = datetime.now(timezone.utc)
                current_minutes = now.hour * 60 + now.minute
                next_minutes = min(
                    (h * 60 - current_minutes) % (24 * 60) or 24 * 60
                    for h in report_hours_utc
                )
                seconds_until = next_minutes * 60 - now.second
                await asyncio.sleep(seconds_until)
                try:
                    from src.executor.trader import ERC20_BALANCE_ABI
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
                        cost_usd = pos.get("amount_in", 0) / 1e6
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
                    await notifier.notify_hourly_report(
                        balance_usdc=balance_usdc,
                        balance_eth=balance_eth,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=stats["realized_pnl"],
                        total_invested=stats["total_invested"],
                        positions=enriched,
                    )
                except Exception as e:
                    logger.warning("Hourly report failed: %s", e)

        async def daily_reporter() -> None:
            while True:
                now = datetime.now(timezone.utc)
                target_hour = cfg.daily_report_hour_utc
                seconds_until = ((target_hour - now.hour - 1) % 24) * 3600 + (60 - now.minute) * 60 + (60 - now.second)
                await asyncio.sleep(seconds_until)
                try:
                    stats = await get_today_stats()
                    await notifier.notify_daily_report(stats["total"], stats["success"], stats["pnl"])
                except Exception as e:
                    logger.warning("Daily report failed: %s", e)

        tp_monitor = TakeProfitMonitor(
            okx=okx,
            traders=traders,
            take_profit_roi=cfg.take_profit_roi,
            check_interval=cfg.take_profit_check_sec,
            on_take_profit=on_take_profit,
        )

        watcher = AddressWatcher(
            w3=w3,
            targets=[t.address for t in cfg.copy_targets],
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
            asyncio.create_task(daily_reporter()),
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
    args = parser.parse_args()

    override = None
    if args.dry_run:
        override = True
    elif args.live:
        override = False

    asyncio.run(run(override))


if __name__ == "__main__":
    main()
