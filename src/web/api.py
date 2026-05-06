"""
轻量 HTTP API：提供网格策略数据 + 钱包余额。
使用 aiohttp，端口 8911。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from aiohttp import web

from src.executor.trader import USDC_BASE

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
STATE_FILE = DATA_DIR / "state.json"
DB_PATH = "trades.db"

# ── CORS ──────────────────────────────────────────────────

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


async def _cors_preflight(_request):
    return web.Response(status=204, headers=CORS_HEADERS)


def json_ok(data: dict) -> web.Response:
    return web.json_response(data, headers=CORS_HEADERS)


def json_error(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status, headers=CORS_HEADERS)


# ── 底层 IO ────────────────────────────────────────────────


def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


async def _query_db(sql: str, params=()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(row) async for row in cur]


async def _get_okx_price(okx_cfg: dict | None) -> float | None:
    """如果配置了 OKX 凭证，通过 OKX 获取 AERO 当前价格。"""
    if not okx_cfg:
        return None
    try:
        from src.executor.okx_client import OKXDexClient

        token = okx_cfg.get("grid_token", "")
        if not token:
            return None
        async with OKXDexClient(
            okx_cfg["api_key"], okx_cfg["secret"], okx_cfg["passphrase"]
        ) as okx:
            quote = await okx.get_quote(USDC_BASE, token, int(0.1 * 1e6))
            if quote is None:
                return None
            to_amount = float(quote.get("toTokenAmount", "0"))
            if to_amount <= 0:
                return None
            to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
            token_amount = to_amount / (10 ** to_decimals)
            return 0.1 / token_amount if token_amount > 0 else None
    except Exception as e:
        logger.warning("OKX price fetch failed: %s", e)
        return None


# ── 处理器 ──────────────────────────────────────────────────


async def _get_realized_pnl() -> float:
    rows = await _query_db(
        "SELECT COALESCE(SUM(pnl_usd), 0) as total FROM trades "
        "WHERE strategy LIKE 'grid_sell%' AND status='success'"
    )
    return float(rows[0]["total"]) if rows else 0.0


async def handle_grid_state(request: web.Request) -> web.Response:
    state = _read_state()
    raw_slots = state.get("grid_slots", [])
    stored_price = state.get("grid_current_price")
    okx = request.app.get("okx_cfg")

    current_price = await _get_okx_price(okx)
    if current_price is None:
        current_price = stored_price or 0.0

    grid_config = request.app.get("grid_cfg", {})

    slots = []
    active = 0
    unrealized_pnl = 0.0
    for s in raw_slots:
        status = s.get("status", "idle")
        filled = int(s.get("filled_amount", 0))
        cost = float(s.get("amount_usdc", 0))
        slot_current_value = None
        slot_pnl = None
        slot_roi = None

        if status == "bought" and filled > 0 and current_price:
            slot_current_value = filled * current_price / 1e18
            slot_pnl = slot_current_value - cost
            slot_roi = (slot_pnl / cost * 100) if cost > 0 else 0.0
            unrealized_pnl += slot_pnl
            active += 1

        slots.append({
            "slot_id": s.get("slot_id", 0),
            "buy_price": float(s.get("buy_price", 0)),
            "sell_price": float(s.get("sell_price", 0)),
            "amount_usdc": cost,
            "status": status,
            "filled_amount": filled,
            "current_value_usd": round(slot_current_value, 4) if slot_current_value is not None else None,
            "unrealized_pnl": round(slot_pnl, 4) if slot_pnl is not None else None,
            "roi_pct": round(slot_roi, 2) if slot_roi is not None else None,
        })

    realized_pnl = await _get_realized_pnl()

    return json_ok({
        "enabled": grid_config.get("enabled", False),
        "token": grid_config.get("token", ""),
        "token_symbol": "AERO",
        "current_price": round(current_price, 8) if current_price else None,
        "total_investment": grid_config.get("investment_usdc", 0),
        "total_slots": len(slots),
        "active_slots": active,
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "total_pnl": round(realized_pnl + unrealized_pnl, 4),
        "slots": slots,
        "updated_at": state.get("_updated_at", ""),
    })


async def handle_grid_history(request: web.Request) -> web.Response:
    """返回网格策略的交易记录。"""
    rows = await _query_db(
        "SELECT tx_hash, side, token_address, amount_in, amount_out, "
        "filled_amount, cost_usd, pnl_usd, roi, created_at "
        "FROM trades WHERE strategy LIKE 'grid%' AND status='success' "
        "ORDER BY created_at DESC LIMIT 100"
    )

    trades = []
    for r in rows:
        cost = float(r.get("cost_usd") or 0)
        pnl = float(r.get("pnl_usd") or 0)
        roi_val = float(r.get("roi") or 0) * 100  # DB stores as decimal
        amount_out = int(r.get("amount_out") or 0)
        amount_in = int(r.get("amount_in") or 0)

        trades.append({
            "tx_hash": r["tx_hash"],
            "side": r["side"],
            "token_address": r["token_address"],
            "amount_out_raw": amount_out,
            "amount_in_raw": amount_in,
            "cost_usd": cost,
            "pnl_usd": round(pnl, 4),
            "roi_pct": round(roi_val, 2),
            "created_at": r["created_at"],
        })

    return json_ok({"trades": trades})


# ── 旧页面兼容端点（返回空数据） ─────────────────────────


async def handle_config(_request):
    state = _read_state()
    return json_ok({
        "base_token": "USDC",
        "trade_mode": "grid",
        "dry_run": True,
        "daily_loss_limit_usd": 10,
        "slippage": 0.01,
        "gas_limit_gwei": 50,
        "wallet_address": os.environ.get("WALLET_ADDRESS", ""),
        "copy_targets": [],
        "buyback_watch": {},
    })


async def handle_stats(_request):
    rows = await _query_db(
        "SELECT side, cost_usd, pnl_usd FROM trades WHERE status='success'"
    )
    today_prefix = datetime.now(timezone.utc).date().isoformat()
    today_trades = [r for r in rows if r.get("created_at", "").startswith(today_prefix)]

    total_invested = sum(float(r.get("cost_usd", 0)) for r in rows if r["side"] == "buy")
    realized_pnl = sum(float(r.get("pnl_usd", 0)) for r in rows)
    today_pnl = sum(float(r.get("pnl_usd", 0)) for r in today_trades)

    return json_ok({
        "today": {"total": len(today_trades), "success": len(today_trades), "pnl": round(today_pnl, 2)},
        "all": {"total_trades": len(rows), "total_invested": round(total_invested, 2), "realized_pnl": round(realized_pnl, 2)},
        "today_pnl": round(today_pnl, 2),
    })


async def handle_balances(_request):
    return json_ok({
        "balances": {"USDC": None, "ETH": None, "AERO": None},
        "base_token": "USDC",
        "wallet_address": os.environ.get("WALLET_ADDRESS", ""),
    })


# ── 启动 ────────────────────────────────────────────────────


def _load_grid_cfg() -> dict:
    """从 config.yaml 读取网格配置。"""
    try:
        import yaml
        with open("config.yaml", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        g = raw.get("grid", {}) or {}
        return {
            "enabled": bool(g.get("enabled", False)),
            "token": str(g.get("token", "")),
            "investment_usdc": float(g.get("investment_usdc", 0)),
        }
    except Exception:
        return {}


def _load_okx_cfg() -> dict | None:
    """从 .env 读取 OKX 凭证。"""
    from dotenv import load_dotenv
    load_dotenv()
    token = _load_grid_cfg().get("token", "")
    if not token:
        return None
    api_key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    if not api_key or not secret or not passphrase:
        return None
    return {"api_key": api_key, "secret": secret, "passphrase": passphrase, "grid_token": token}


def create_app() -> web.Application:
    app = web.Application()

    # 启动时加载配置
    app["grid_cfg"] = _load_grid_cfg()
    app["okx_cfg"] = _load_okx_cfg()

    # 网格端点
    app.router.add_get("/api/grid/state", handle_grid_state)
    app.router.add_get("/api/grid/history", handle_grid_history)

    # 旧页面兼容端点
    app.router.add_get("/api/config", handle_config)
    app.router.add_get("/api/trades/stats", handle_stats)
    app.router.add_get("/api/config/balances", handle_balances)

    # CORS preflight
    app.router.add_route("OPTIONS", "/api/{tail:.*}", _cors_preflight)

    return app


def run(host: str = "127.0.0.1", port: int = 8911) -> None:
    app = create_app()
    logger.info("API server starting on http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=lambda _: None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run()
