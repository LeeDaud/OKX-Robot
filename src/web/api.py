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
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
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
        "volatility_adjust": _load_grid_cfg().get("volatility_adjust", False),
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
    cfg = _load_full_config()
    grid_raw = {"enabled": cfg["grid_enabled"], "token": cfg["grid_token"],
                 "levels": cfg["grid_levels"], "spread_pct": cfg["grid_spread_pct"],
                 "investment_usdc": cfg["grid_investment_usdc"], "profit_pct": cfg["grid_profit_pct"],
                 "max_slots": cfg.get("grid_max_slots", 12)}
    # read raw config for buyback_watch
    try:
        import yaml
        with open("config.yaml", encoding="utf-8") as f:
            raw_cfg = yaml.safe_load(f) or {}
    except Exception:
        raw_cfg = {}
    return json_ok({
        # 原有 flat 字段（向后兼容）
        "base_token": cfg["base_token"],
        "trade_mode": cfg["trade_mode"],
        "trade_ratio": cfg["trade_ratio"],
        "trade_fixed_usd": cfg["trade_fixed_usd"],
        "trade_max_usd": cfg["trade_max_usd"],
        "trade_fixed_virtuals": cfg["trade_fixed_virtuals"],
        "token_whitelist": cfg["token_whitelist"],
        "min_trade_usd": cfg["min_trade_usd"],
        "daily_loss_limit_usd": cfg["daily_loss_limit_usd"],
        "slippage": cfg["slippage"],
        "gas_limit_gwei": cfg["gas_limit_gwei"],
        "take_profit_roi": cfg["take_profit_roi"],
        "take_profit_check_sec": cfg["take_profit_check_sec"],
        "dry_run": cfg["dry_run"],
        "poll_interval_sec": cfg["poll_interval_sec"],
        "wallet_address": cfg["wallet_address"],
        "copy_targets": cfg["copy_targets"],
        "buyback_watch": raw_cfg.get("buyback_watch", {}),
        # 结构化分组
        "wallet": {
            "wallet_address": cfg["wallet_address"],
            "rpc_http_url": cfg["rpc_http_url"],
            "rpc_ws_url": cfg["rpc_ws_url"],
            "has_private_key": cfg["has_private_key"],
            "has_okx_api_key": cfg["has_okx_api_key"],
            "dry_run": cfg["dry_run"],
            "base_token": cfg["base_token"],
        },
        "copy_trading": {
            "enabled": bool(cfg.get("copy_trading_enabled", False)),
            "trade_mode": cfg["trade_mode"],
            "trade_ratio": cfg["trade_ratio"],
            "trade_fixed_usd": cfg["trade_fixed_usd"],
            "trade_max_usd": cfg["trade_max_usd"],
            "trade_fixed_virtuals": cfg["trade_fixed_virtuals"],
            "token_whitelist": cfg["token_whitelist"],
            "min_trade_usd": cfg["min_trade_usd"],
            "daily_loss_limit_usd": cfg["daily_loss_limit_usd"],
            "slippage": cfg["slippage"],
            "gas_limit_gwei": cfg["gas_limit_gwei"],
            "take_profit_roi": cfg["take_profit_roi"],
            "take_profit_check_sec": cfg["take_profit_check_sec"],
            "poll_interval_sec": cfg["poll_interval_sec"],
            "targets": cfg["copy_targets"],
        },
        "grid": grid_raw,
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


async def handle_trades(request: web.Request) -> web.Response:
    strategy = request.query.get("strategy", "").strip()
    limit = int(request.query.get("limit", 50))
    offset = int(request.query.get("offset", 0))
    limit = min(max(limit, 1), 500)

    if strategy in ("", "all"):
        sql = ("SELECT * FROM trades ORDER BY created_at DESC "
               "LIMIT ? OFFSET ?")
        params = (limit, offset)
    elif strategy == "copy":
        sql = ("SELECT * FROM trades WHERE strategy='' ORDER BY created_at DESC "
               "LIMIT ? OFFSET ?")
        params = (limit, offset)
    elif strategy == "grid":
        sql = ("SELECT * FROM trades WHERE strategy LIKE 'grid%' ORDER BY created_at DESC "
               "LIMIT ? OFFSET ?")
        params = (limit, offset)
    elif strategy == "dca":
        sql = ("SELECT * FROM trades WHERE strategy IN ('dca','deep_buy') ORDER BY created_at DESC "
               "LIMIT ? OFFSET ?")
        params = (limit, offset)
    elif strategy == "buyback":
        sql = ("SELECT * FROM trades WHERE strategy='buyback_sell' ORDER BY created_at DESC "
               "LIMIT ? OFFSET ?")
        params = (limit, offset)
    else:
        return json_error(f"Unknown strategy filter: {strategy}", 400)

    rows = await _query_db(sql, params)
    trades = [_row_to_trade(r) for r in rows]
    return json_ok({"trades": trades})


async def handle_balances(request: web.Request):
    """查询钱包真实余额（ETH + 已知代币）。"""
    from web3 import AsyncWeb3
    from src.executor.trader import ERC20_BALANCE_ABI

    base_token = _load_full_config().get("base_token", "USDC")
    wallet = os.environ.get("WALLET_ADDRESS", "")
    rpc_url = os.environ.get("RPC_HTTP_URL", "") or os.environ.get("RPC_HTTP_URL_FALLBACK", "")
    if not wallet or not rpc_url:
        return json_ok({"balances": {}, "base_token": base_token, "wallet_address": wallet,
                        "error": "WALLET_ADDRESS or RPC_HTTP_URL not configured"})

    known_tokens = {
        "ETH":   {"address": None, "decimals": 18},
        "USDC":  {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
        "VIRTUAL": {"address": "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b", "decimals": 18},
        "AERO":  {"address": "0x940181a94a35a4569e4529a3cdfb74e38fd98631", "decimals": 18},
    }

    try:
        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        checksum = AsyncWeb3.to_checksum_address(wallet)
    except Exception as e:
        logger.warning("Web3 init failed: %s", e)
        return json_ok({"balances": {}, "base_token": base_token, "wallet_address": wallet,
                        "error": f"RPC init failed: {e}"})

    result = {}
    for name, info in known_tokens.items():
        try:
            if info["address"] is None:
                # ETH — native balance
                raw = await w3.eth.get_balance(checksum)
                result[name] = round(raw / (10 ** info["decimals"]), 6)
            else:
                contract = w3.eth.contract(
                    address=AsyncWeb3.to_checksum_address(info["address"]),
                    abi=ERC20_BALANCE_ABI,
                )
                raw = await contract.functions.balanceOf(checksum).call()
                result[name] = round(raw / (10 ** info["decimals"]), 6)
        except Exception as e:
            logger.warning("Fetch %s balance failed: %s", name, e)
            result[name] = None

    return json_ok({
        "balances": result,
        "base_token": base_token,
        "wallet_address": wallet,
    })


async def handle_get_wallet(_request):
    """GET /api/config/wallet - 返回钱包信息"""
    cfg = _load_full_config()
    return json_ok({
        "wallet_address": cfg["wallet_address"],
        "rpc_http_url": cfg["rpc_http_url"],
        "rpc_ws_url": cfg["rpc_ws_url"],
        "has_private_key": cfg["has_private_key"],
        "has_okx_api_key": cfg["has_okx_api_key"],
    })


async def handle_update_wallet(request: web.Request):
    """PUT /api/config/wallet - 更新钱包配置"""
    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    from dotenv import load_dotenv, set_key
    import yaml

    load_dotenv()
    env_file = Path(".env")
    updated = []

    # 更新 .env 文件中的字段
    if "wallet_address" in data and data["wallet_address"]:
        set_key(env_file, "WALLET_ADDRESS", data["wallet_address"])
        updated.append("wallet_address")

    if "rpc_http_url" in data and data["rpc_http_url"]:
        set_key(env_file, "RPC_HTTP_URL", data["rpc_http_url"])
        updated.append("rpc_http_url")

    if "rpc_ws_url" in data and data["rpc_ws_url"]:
        set_key(env_file, "RPC_WS_URL", data["rpc_ws_url"])
        updated.append("rpc_ws_url")

    if "private_key" in data and data["private_key"]:
        set_key(env_file, "PRIVATE_KEY", data["private_key"])
        updated.append("private_key")

    if "okx_api_key" in data and data["okx_api_key"]:
        set_key(env_file, "OKX_API_KEY", data["okx_api_key"])
        updated.append("okx_api_key")

    if "okx_secret_key" in data and data["okx_secret_key"]:
        set_key(env_file, "OKX_SECRET_KEY", data["okx_secret_key"])
        updated.append("okx_secret_key")

    if "okx_passphrase" in data and data["okx_passphrase"]:
        set_key(env_file, "OKX_PASSPHRASE", data["okx_passphrase"])
        updated.append("okx_passphrase")

    return json_ok({"ok": True, "updated": updated})


async def handle_toggle_execution(request: web.Request):
    """POST /api/config/toggle - 切换执行开关（copy_trading.enabled, grid.enabled, dry_run）"""
    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    import yaml
    config_file = Path("config.yaml")

    try:
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        return json_error(f"Failed to read config: {e}", 500)

    updated = []

    # 切换 copy_trading.enabled
    if "copy_trading_enabled" in data:
        if "copy_trading" not in cfg:
            cfg["copy_trading"] = {}
        cfg["copy_trading"]["enabled"] = bool(data["copy_trading_enabled"])
        updated.append("copy_trading_enabled")

    # 切换 grid.enabled
    if "grid_enabled" in data:
        if "grid" not in cfg:
            cfg["grid"] = {}
        cfg["grid"]["enabled"] = bool(data["grid_enabled"])
        updated.append("grid_enabled")

    # 切换 grid.volatility_adjust
    if "grid_volatility_adjust" in data:
        if "grid" not in cfg:
            cfg["grid"] = {}
        cfg["grid"]["volatility_adjust"] = bool(data["grid_volatility_adjust"])
        updated.append("grid_volatility_adjust")

    # 切换 dry_run
    if "dry_run" in data:
        cfg["dry_run"] = bool(data["dry_run"])
        updated.append("dry_run")

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return json_error(f"Failed to write config: {e}", 500)

    return json_ok({"ok": True, "updated": updated})


# ── 跟单目标 CRUD ─────────────────────────────────────────


async def handle_add_target(request: web.Request):
    """POST /api/config/targets - 添加跟单目标"""
    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    address = (data.get("address") or "").strip().lower()
    if not address:
        return json_error("address is required", 400)

    import yaml
    config_file = Path("config.yaml")
    try:
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        return json_error(f"Failed to read config: {e}", 500)

    ct = cfg.setdefault("copy_trading", {})
    targets = ct.setdefault("targets", [])

    if any(t.get("address", "").lower() == address for t in targets):
        return json_error("Target already exists", 409)

    new_target = {"address": address}
    if data.get("remark"): new_target["remark"] = data["remark"]
    if data.get("trade_mode"): new_target["trade_mode"] = data["trade_mode"]
    if data.get("trade_ratio") is not None: new_target["trade_ratio"] = float(data["trade_ratio"])
    if data.get("trade_fixed_usd") is not None: new_target["trade_fixed_usd"] = float(data["trade_fixed_usd"])
    if data.get("trade_max_usd") is not None: new_target["trade_max_usd"] = float(data["trade_max_usd"])
    if data.get("trade_fixed_virtuals") is not None: new_target["trade_fixed_virtuals"] = float(data["trade_fixed_virtuals"])

    targets.append(new_target)

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return json_error(f"Failed to write config: {e}", 500)

    return json_ok({"ok": True})


async def handle_update_target(request: web.Request):
    """PUT /api/config/targets/{address} - 更新跟单目标"""
    address = request.match_info.get("address", "").strip().lower()
    if not address:
        return json_error("address is required", 400)

    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    import yaml
    config_file = Path("config.yaml")
    try:
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        return json_error(f"Failed to read config: {e}", 500)

    targets = cfg.get("copy_trading", {}).get("targets", [])
    found = None
    for t in targets:
        if t.get("address", "").lower() == address:
            found = t
            break

    if found is None:
        return json_error("Target not found", 404)

    for key in ("remark", "trade_mode"):
        if key in data:
            found[key] = data[key]
    for key in ("trade_ratio", "trade_fixed_usd", "trade_max_usd", "trade_fixed_virtuals"):
        if key in data and data[key] is not None:
            found[key] = float(data[key])
        elif key in data and data[key] is None:
            found.pop(key, None)

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return json_error(f"Failed to write config: {e}", 500)

    return json_ok({"ok": True})


async def handle_delete_target(request: web.Request):
    """DELETE /api/config/targets/{address} - 删除跟单目标"""
    address = request.match_info.get("address", "").strip().lower()
    if not address:
        return json_error("address is required", 400)

    import yaml
    config_file = Path("config.yaml")
    try:
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        return json_error(f"Failed to read config: {e}", 500)

    targets = cfg.get("copy_trading", {}).get("targets", [])
    new_targets = [t for t in targets if t.get("address", "").lower() != address]

    if len(new_targets) == len(targets):
        return json_error("Target not found", 404)

    cfg["copy_trading"]["targets"] = new_targets

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return json_error(f"Failed to write config: {e}", 500)

    return json_ok({"ok": True})


# ── 参数更新 ──────────────────────────────────────────────


async def handle_update_params(request: web.Request):
    """PUT /api/config/params - 更新交易参数"""
    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    import yaml
    config_file = Path("config.yaml")
    try:
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        return json_error(f"Failed to read config: {e}", 500)

    ct = cfg.setdefault("copy_trading", {})
    ct_keys = ["trade_mode", "trade_ratio", "trade_fixed_usd", "trade_max_usd",
               "trade_fixed_virtuals", "slippage", "min_trade_usd",
               "daily_loss_limit_usd", "take_profit_roi", "take_profit_check_sec",
               "poll_interval_sec", "gas_limit_gwei"]
    for key in ct_keys:
        if key in data:
            ct[key] = data[key]

    if "base_token" in data:
        cfg["base_token"] = str(data["base_token"]).upper()

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return json_error(f"Failed to write config: {e}", 500)

    return json_ok({"ok": True})


# ── 持仓查询 ──────────────────────────────────────────────


async def handle_positions_all(_request):
    """GET /api/positions/all - 返回所有持仓（开仓 + 平仓）"""
    open_rows = await _query_db(
        "SELECT * FROM trades WHERE side='buy' AND is_open=1 "
        "ORDER BY created_at DESC LIMIT 50"
    )
    closed_rows = await _query_db(
        "SELECT * FROM trades WHERE side='sell' AND status='success' "
        "ORDER BY created_at DESC LIMIT 50"
    )

    return json_ok({
        "open": [_row_to_trade(r) for r in open_rows],
        "closed": [_row_to_trade(r) for r in closed_rows],
        "summary": {
            "open_count": len(open_rows),
            "closed_count": len(closed_rows),
            "total_invested_open": sum(float(r.get("cost_usd", 0)) for r in open_rows),
            "realized_pnl": sum(float(r.get("pnl_usd", 0)) for r in closed_rows),
        },
    })


async def handle_refresh_prices(request: web.Request):
    """POST /api/positions/refresh-prices - 刷新持仓代币价格"""
    okx_cfg = request.app.get("okx_cfg")
    if not okx_cfg:
        return json_ok({
            "tokens": {},
            "positions": {},
            "error": "OKX 凭证未配置",
        })

    from src.executor.okx_client import OKXDexClient
    from src.executor.trader import USDC_BASE

    open_rows = await _query_db(
        "SELECT token_address, amount_out, cost_usd FROM trades "
        "WHERE side='buy' AND is_open=1"
    )

    tokens = {}
    positions = {}
    async with OKXDexClient(
        okx_cfg["api_key"], okx_cfg["secret"], okx_cfg["passphrase"]
    ) as okx:
        for row in open_rows:
            addr = row["token_address"]
            if addr in positions:
                continue
            amount = int(row.get("amount_out") or 0)
            cost = float(row.get("cost_usd") or 0)
            if amount <= 0:
                continue

            quote = await okx.get_quote(addr, USDC_BASE, amount)
            current_price = None
            current_value = None
            if quote:
                from_amount = int(quote.get("fromTokenAmount", "0"))
                if from_amount > 0:
                    to_amount = float(quote.get("toTokenAmount", "0"))
                    to_decimals = int((quote.get("toToken") or {}).get("decimals", 18))
                    current_value = to_amount / (10 ** to_decimals)
                    from_decimals = int((quote.get("fromToken") or {}).get("decimals", 18))
                    current_price = current_value / (amount / (10 ** from_decimals)) if amount > 0 else None

            tokens[addr] = {
                "symbol": None,
                "decimals": 18,
                "current_price": round(current_price, 12) if current_price else None,
            }
            positions[addr] = {
                "amount": amount,
                "cost_basis_usd": round(cost, 4),
                "current_price": round(current_price, 12) if current_price else None,
                "current_value_usd": round(current_value, 4) if current_value else None,
                "unrealized_pnl": round(current_value - cost, 4) if current_value else None,
                "roi_pct": round((current_value - cost) / cost * 100, 2) if current_value and cost > 0 else None,
            }

    return json_ok({"tokens": tokens, "positions": positions})


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
            "volatility_adjust": bool(g.get("volatility_adjust", False)),
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


def _load_full_config() -> dict:
    """Read complete config from config.yaml + .env, return as flat dict."""
    from dotenv import load_dotenv
    import yaml
    load_dotenv()
    try:
        with open("config.yaml", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        raw = {}
    copy_raw = raw.get("copy_trading", {}) or {}
    targets_raw = copy_raw.get("targets", []) or []
    grid_raw = raw.get("grid", {}) or {}

    return {
        "base_token": str(raw.get("base_token", "VIRTUAL")).upper(),
        "trade_mode": str(copy_raw.get("trade_mode", "monitor")),
        "trade_ratio": float(copy_raw.get("trade_ratio", 0.5)),
        "trade_fixed_usd": float(copy_raw.get("trade_fixed_usd", 50)),
        "trade_max_usd": float(copy_raw.get("trade_max_usd", 100)),
        "trade_fixed_virtuals": float(copy_raw.get("trade_fixed_virtuals", 30)),
        "token_whitelist": list(copy_raw.get("token_whitelist", [])),
        "min_trade_usd": float(copy_raw.get("min_trade_usd", 5)),
        "daily_loss_limit_usd": float(raw.get("daily_loss_limit_usd", 10)),
        "slippage": float(raw.get("slippage", 0.01)),
        "gas_limit_gwei": float(raw.get("gas_limit_gwei", 50)),
        "take_profit_roi": float(raw.get("take_profit_roi", 0)),
        "take_profit_check_sec": float(raw.get("take_profit_check_sec", 60)),
        "dry_run": bool(raw.get("dry_run", True)),
        "poll_interval_sec": float(raw.get("poll_interval_sec", 10)),
        "wallet_address": os.environ.get("WALLET_ADDRESS", ""),
        "rpc_http_url": os.environ.get("RPC_HTTP_URL", ""),
        "rpc_ws_url": os.environ.get("RPC_WS_URL", ""),
        "has_private_key": bool(os.environ.get("PRIVATE_KEY", "")),
        "has_okx_api_key": bool(os.environ.get("OKX_API_KEY", "")),
        "feishu_webhook_url": str(raw.get("feishu_webhook_url", "")),
        "copy_targets": targets_raw,
        "grid_enabled": bool(grid_raw.get("enabled", False)),
        "grid_token": str(grid_raw.get("token", "")),
        "grid_levels": int(grid_raw.get("levels", 6)),
        "grid_spread_pct": float(grid_raw.get("spread_pct", 2.0)),
        "grid_investment_usdc": float(grid_raw.get("investment_usdc", 60)),
        "grid_profit_pct": float(grid_raw.get("profit_pct", 3.0)),
        "grid_max_slots": int(grid_raw.get("max_slots", 12)),
    }


def _row_to_trade(row: dict) -> dict:
    """Map a DB row to the frontend TradeRecord shape."""
    amount_in = int(row.get("amount_in") or 0)
    amount_out = int(row.get("amount_out") or 0)
    cost = float(row.get("cost_usd") or 0)
    pnl = float(row.get("pnl_usd") or 0)
    roi_raw = float(row.get("roi") or 0)
    token_addr = row.get("token_address", "")
    side = row.get("side", "buy")
    filled_num = int(row.get("filled_amount") or 0)

    # entry_price: cost / tokens_received (assume 18 decimals for tokens)
    entry_price = 0.0
    if amount_out > 0 and cost > 0:
        entry_price = cost / (amount_out / 1e18)

    exit_price = 0.0
    if amount_in > 0 and side == "sell":
        exit_price = (cost + max(pnl, 0)) / (amount_in / 1e18) if amount_in > 0 else 0.0

    return {
        "id": row["id"],
        "source_tx": row.get("source_tx") or "",
        "source_addr": row.get("source_addr") or row.get("tx_hash") or "",
        "token_in": token_addr if side == "buy" else "",
        "token_out": token_addr if side == "sell" else "",
        "amount_in": row.get("amount_in", "0"),
        "amount_out": amount_out,
        "our_tx": row.get("our_tx_hash") or None,
        "status": row.get("status", ""),
        "side": side,
        "position_id": None,
        "entry_price": round(entry_price, 12),
        "exit_price": round(exit_price, 12) if exit_price else 0.0,
        "roi_pct": round(roi_raw, 4),
        "pnl": round(pnl, 4),
        "created_at": row.get("created_at", ""),
        "filled_amount": row.get("filled_amount", "0"),
        "filled_cost_usd": round(cost, 4),
        "strategy": row.get("strategy", ""),
    }


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
    app.router.add_get("/api/trades", handle_trades)
    app.router.add_get("/api/config/balances", handle_balances)
    app.router.add_get("/api/config/wallet", handle_get_wallet)
    app.router.add_put("/api/config/wallet", handle_update_wallet)
    app.router.add_post("/api/config/toggle", handle_toggle_execution)
    app.router.add_post("/api/config/targets", handle_add_target)
    app.router.add_put("/api/config/targets/{address}", handle_update_target)
    app.router.add_delete("/api/config/targets/{address}", handle_delete_target)
    app.router.add_put("/api/config/params", handle_update_params)
    app.router.add_get("/api/positions/all", handle_positions_all)
    app.router.add_post("/api/positions/refresh-prices", handle_refresh_prices)

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
