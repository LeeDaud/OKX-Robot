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

    grid_cfg = _load_grid_cfg()
    grid_enabled = grid_cfg.get("enabled", False)
    grid_vol_adjust = grid_cfg.get("volatility_adjust", False)

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
        "enabled": grid_enabled,
        "token": grid_cfg.get("token", ""),
        "token_symbol": "AERO",
        "current_price": round(current_price, 8) if current_price else None,
        "total_investment": grid_cfg.get("investment_usdc", 0),
        "total_slots": len(slots),
        "active_slots": active,
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "total_pnl": round(realized_pnl + unrealized_pnl, 4),
        "volatility_adjust": grid_vol_adjust,
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
        "contract": {
            "enabled": bool((raw_cfg.get("contract", {}) or {}).get("enabled", False)),
        },
    })


async def handle_stats(_request):
    rows = await _query_db(
        "SELECT side, cost_usd, pnl_usd, created_at FROM trades WHERE status='success'"
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

    # 切换 mean_reversion.enabled
    if "mean_reversion_enabled" in data:
        if "mean_reversion" not in cfg:
            cfg["mean_reversion"] = {}
        cfg["mean_reversion"]["enabled"] = bool(data["mean_reversion_enabled"])
        updated.append("mean_reversion_enabled")

    # 切换 contract.enabled
    if "contract_enabled" in data:
        if "contract" not in cfg:
            cfg["contract"] = {}
        cfg["contract"]["enabled"] = bool(data["contract_enabled"])
        updated.append("contract_enabled")

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

    # 按 token 地址聚合多笔持仓
    agg = {}
    for row in open_rows:
        addr = row["token_address"]
        amount = int(row.get("amount_out") or 0)
        cost = float(row.get("cost_usd") or 0)
        if amount <= 0:
            continue
        if addr not in agg:
            agg[addr] = {"amount": 0, "cost": 0.0}
        agg[addr]["amount"] += amount
        agg[addr]["cost"] += cost

    tokens = {}
    positions = {}
    async with OKXDexClient(
        okx_cfg["api_key"], okx_cfg["secret"], okx_cfg["passphrase"]
    ) as okx:
        for addr, data in agg.items():
            amount = data["amount"]
            cost = data["cost"]

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
    aero_raw = raw.get("aero_trend", {}) or {}

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
        "copy_trading_enabled": bool(copy_raw.get("enabled", False)),
        "grid_enabled": bool(grid_raw.get("enabled", False)),
        "contract_enabled": bool((raw.get("contract", {}) or {}).get("enabled", False)),
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


async def handle_aero_state(_request):
    """GET /api/aero/state - AERO 趋势策略状态"""
    from src.config.loader import load_config

    state = _read_state()
    raw_pos = state.get("aero_position", {})
    consecutive = int(state.get("aero_consecutive_losses", 0))
    snap = state.get("aero_snapshot", {}) or {}

    # 读取 enabled + config 阈值
    enabled = False
    cfg = {}
    try:
        ac = load_config().aero_trend
        enabled = ac.enabled
        cfg = {
            "min_return_5m": ac.min_return_5m,
            "max_return_5m": ac.max_return_5m,
            "min_return_15m": ac.min_return_15m,
            "max_return_30m": ac.max_return_30m,
            "min_volume_ratio": ac.min_volume_ratio,
            "min_buy_pressure": ac.min_buy_pressure,
            "min_liquidity_usd": ac.min_liquidity_usd,
            "max_slippage_buy": ac.max_slippage_buy,
            "stop_loss_pct": ac.stop_loss_pct,
            "time_stop_minutes": ac.time_stop_minutes,
            "time_stop_min_profit": ac.time_stop_min_profit,
            "take_profit_1_pct": ac.take_profit_1_pct,
            "take_profit_2_pct": ac.take_profit_2_pct,
            "trailing_stop_drawdown": ac.trailing_stop_drawdown,
            "pullback_min": ac.pullback_min,
            "pullback_max": ac.pullback_max,
            "pullback_volume_ratio": ac.pullback_volume_ratio,
            "pullback_buy_pressure": ac.pullback_buy_pressure,
            "cooldown_minutes": ac.cooldown_minutes,
            "position_size_pct": ac.position_size_pct,
        }
    except Exception:
        pass

    # 计算条件达标状态
    conditions = _compute_aero_conditions(snap, cfg)

    return json_ok({
        "enabled": enabled,
        "has_position": raw_pos.get("has_position", False),
        "entry_price": raw_pos.get("entry_price", 0),
        "current_price": raw_pos.get("current_price", 0),
        "position_amount": raw_pos.get("position_amount", 0),
        "cost_basis_usdc": raw_pos.get("cost_basis_usdc", 0),
        "pnl_pct": raw_pos.get("pnl_pct", 0),
        "highest_price": raw_pos.get("highest_price_since_entry", 0),
        "holding_time_minutes": raw_pos.get("holding_time_minutes", 0),
        "take_profit_1_done": raw_pos.get("take_profit_1_done", False),
        "take_profit_2_done": raw_pos.get("take_profit_2_done", False),
        "trailing_stop_active": raw_pos.get("trailing_stop_active", False),
        "consecutive_losses": consecutive,
        "entry_time": raw_pos.get("entry_time", ""),
        "buy_tx_hash": raw_pos.get("buy_tx_hash", ""),
        # 新增：指标、阈值、条件
        "indicators": snap,
        "config": cfg,
        "conditions": conditions,
    })


async def handle_mean_reversion_state(_request):
    """GET /api/mean-reversion/state - 均值回归策略状态"""
    state = _read_state()
    mr_state = state.get("mean_reversion_state", {}) or {}
    return json_ok({
        "enabled": mr_state.get("enabled", False),
        "symbols": mr_state.get("symbols", []),
        "consecutive_losses": mr_state.get("consecutive_losses", 0),
        "paused_until": mr_state.get("paused_until"),
        "daily_open_count": mr_state.get("daily_open_count", 0),
        "daily_open_date": mr_state.get("daily_open_date", ""),
        "config": mr_state.get("config", {}),
    })


def _compute_aero_conditions(snap: dict, cfg: dict) -> dict:
    """计算条件达标状态，供前端展示。"""
    p = snap.get("price", 0)
    r5 = snap.get("return_5m", 0)
    r15 = snap.get("return_15m", 0)
    r30 = snap.get("return_30m", 0)
    r1h = snap.get("return_1h", 0)
    vwap = snap.get("vwap", 0)
    ma20 = snap.get("ma_20m", 0)
    vol_ratio = snap.get("volume_ratio", 0)
    buy_p = snap.get("buy_pressure", 0)
    liq = snap.get("pool_liquidity_usd", 0)
    slip = snap.get("simulated_buy_slippage", 1)
    pullback = snap.get("pullback_from_high", 0)
    buy_vol = snap.get("buy_volume_5m", 0)
    sell_vol = snap.get("sell_volume_5m", 0)
    breakout = snap.get("price_breakout_1h", False)
    above_vwap = p > vwap if vwap > 0 else False
    above_ma = p > ma20 if ma20 > 0 else False
    above_open = snap.get("price_above_open_1h", False)
    near_vwap = (abs(p - vwap) / vwap < 0.02) if vwap > 0 else False
    near_ma = (abs(p - ma20) / ma20 < 0.02) if ma20 > 0 else False
    sell_declining = sell_vol < buy_vol * 0.8 if buy_vol > 0 else False

    cr = cfg  # shorthand

    def meets(label, ok, current, threshold, hint=""):
        return {"label": label, "ok": ok, "current": current, "threshold": threshold, "hint": hint}

    trend = [
        meets("价格突破 1h 高", breakout, breakout, True, ""),
        meets("价格 > VWAP", above_vwap, p, vwap, f"VWAP=${vwap:.6f}" if vwap else "N/A"),
        meets("价格 > MA20", above_ma, p, ma20, f"MA20=${ma20:.6f}" if ma20 else "N/A"),
        meets(f"5m 涨幅 ≥{cr.get('min_return_5m',0)*100:.0f}%", r5 >= cr.get("min_return_5m", 0), r5, cr.get("min_return_5m", 0), ""),
        meets(f"5m 涨幅 ≤{cr.get('max_return_5m',0)*100:.0f}%", r5 <= cr.get("max_return_5m", 0), r5, cr.get("max_return_5m", 0), ""),
        meets(f"15m 涨幅 ≥{cr.get('min_return_15m',0)*100:.0f}%", r15 >= cr.get("min_return_15m", 0), r15, cr.get("min_return_15m", 0), ""),
        meets(f"30m 涨幅 <{cr.get('max_return_30m',0)*100:.0f}%", r30 < cr.get("max_return_30m", 0), r30, cr.get("max_return_30m", 0), ""),
        meets(f"成交量 ≥{cr.get('min_volume_ratio',0):.1f}x", vol_ratio >= cr.get("min_volume_ratio", 0), vol_ratio, cr.get("min_volume_ratio", 0), ""),
        meets(f"买入压力 ≥{cr.get('min_buy_pressure',0)*100:.0f}%", buy_p >= cr.get("min_buy_pressure", 0), buy_p, cr.get("min_buy_pressure", 0), ""),
        meets(f"流动性 ≥${cr.get('min_liquidity_usd',0)//1000}K", liq >= cr.get("min_liquidity_usd", 0), liq, cr.get("min_liquidity_usd", 0), ""),
        meets(f"滑点 <{cr.get('max_slippage_buy',0)*100:.0f}%", slip < cr.get("max_slippage_buy", 0), slip, cr.get("max_slippage_buy", 0), ""),
    ]
    all_trend_ok = all(c["ok"] for c in trend)

    pullback_cond = [
        meets("1h 趋势向上", r1h > 0, r1h, 0, ""),
        meets("价格 > 1h 开盘价", above_open, above_open, True, ""),
        meets("接近 VWAP/MA20", near_vwap or near_ma, near_vwap or near_ma, True, f"偏离 VWAP={abs(p-vwap)/vwap*100:.1f}%" if vwap > 0 else "N/A"),
        meets(f"回撤 {cr.get('pullback_min',0)*100:.0f}-{cr.get('pullback_max',0)*100:.0f}%", cr.get("pullback_min", 0) <= pullback <= cr.get("pullback_max", 0), pullback, f"{cr.get('pullback_min',0)}-{cr.get('pullback_max',0)}", ""),
        meets("卖压下降", sell_declining, f"{sell_vol:.0f}<{buy_vol*0.8:.0f}", f"{buy_vol*0.8:.0f}", ""),
        meets(f"成交量 ≥{cr.get('pullback_volume_ratio',0):.1f}x", vol_ratio >= cr.get("pullback_volume_ratio", 0), vol_ratio, cr.get("pullback_volume_ratio", 0), ""),
        meets(f"买入压力 ≥{cr.get('pullback_buy_pressure',0)*100:.0f}%", buy_p >= cr.get("pullback_buy_pressure", 0), buy_p, cr.get("pullback_buy_pressure", 0), ""),
        meets(f"滑点 <{cr.get('max_slippage_buy',0)*100:.0f}%", slip < cr.get("max_slippage_buy", 0), slip, cr.get("max_slippage_buy", 0), ""),
    ]
    all_pullback_ok = all(c["ok"] for c in pullback_cond)

    # 卖出条件
    exit_cond = []
    hp = snap.get("price", 1)
    exit_cond.append(meets(f"硬止损 ≤{cr.get('stop_loss_pct',0)*100:.0f}%", False, 0, cr.get("stop_loss_pct", 0) * 100, ""))
    exit_cond.append(meets("买入压力 <35%", buy_p < 0.35 if buy_p else False, buy_p, 0.35, ""))
    exit_cond.append(meets("价格 < VWAP", p < vwap if vwap > 0 else False, p, vwap, ""))
    exit_cond.append(meets("价格 < MA20", p < ma20 if ma20 > 0 else False, p, ma20, ""))
    exit_cond.append(meets("放量下跌", vol_ratio >= 2 and r5 < 0, f"vol={vol_ratio:.1f} r5={r5*100:.1f}%", "vol≥2 & r5<0", ""))

    return {
        "trend_startup": {"conditions": trend, "all_ok": all_trend_ok, "label": "趋势启动型"},
        "strong_pullback": {"conditions": pullback_cond, "all_ok": all_pullback_ok, "label": "强势回踩型"},
        "exit": {"conditions": exit_cond, "label": "卖出信号"},
    }


# ── 合约交易 ──────────────────────────────────────────────


def _get_contract_trader(app: web.Application):
    """懒加载 ContractTrader 实例，存入 app 复用。"""
    trader = app.get("contract_trader")
    if trader is not None:
        return trader
    from src.executor.contract_trader import ContractTrader
    from src.config.loader import load_config
    from web3 import AsyncWeb3
    try:
        cfg = load_config()
        if not cfg.contract.enabled:
            return None
        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
        trader = ContractTrader(
            w3=w3,
            private_key=os.environ["PRIVATE_KEY"],
            wallet_address=cfg.wallet_address,
            default_leverage=cfg.contract.default_leverage,
            max_leverage_main=cfg.contract.max_leverage_main,
            max_leverage_alt=cfg.contract.max_leverage_alt,
            dry_run=cfg.dry_run,
        )
        app["contract_trader"] = trader
        return trader
    except Exception as e:
        logger.warning("[CONTRACT] Failed to init ContractTrader: %s", e)
        return None


async def handle_contract_state(request: web.Request) -> web.Response:
    """GET /api/contract/state — 合约持仓、余额、配置。"""
    from src.config.loader import load_config

    cfg = load_config()
    cc = cfg.contract

    positions = []
    balances = {"vault_usdc": 0, "wallet_usdc": 0, "total_usdc": 0}

    trader = _get_contract_trader(request.app)
    if trader:
        try:
            positions = await trader.get_positions() or []
            bal = await trader.get_balance()
            if bal:
                balances = bal
        except Exception as e:
            logger.warning("[CONTRACT] state query error: %s", e)

    return json_ok({
        "enabled": cc.enabled,
        "dry_run": cfg.dry_run,
        "pairs": cc.pairs,
        "default_leverage": cc.default_leverage,
        "max_leverage_main": cc.max_leverage_main,
        "max_leverage_alt": cc.max_leverage_alt,
        "max_margin_per_position": cc.max_margin_per_position,
        "funding_rate_threshold": cc.funding_rate_threshold,
        "positions": positions,
        "balances": balances,
    })


async def handle_contract_open(request: web.Request) -> web.Response:
    """POST /api/contract/open — 开仓。"""
    from src.config.loader import load_config

    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    pair = (data.get("pair") or "").strip().upper()
    if not pair:
        return json_error("pair is required", 400)
    side = (data.get("side") or "").strip().lower()
    if side not in ("long", "short"):
        return json_error("side must be 'long' or 'short'", 400)
    margin_usd = float(data.get("margin_usd", 0))
    if margin_usd <= 0:
        return json_error("margin_usd must be > 0", 400)
    leverage = int(data.get("leverage", 0))

    cfg = load_config()
    if not cfg.contract.enabled:
        return json_error("Contract trading is not enabled", 400)

    trader = _get_contract_trader(request.app)
    if not trader:
        return json_error("ContractTrader initialization failed", 500)

    try:
        if side == "long":
            tx_hash = await trader.open_long(pair, margin_usd, leverage)
        else:
            tx_hash = await trader.open_short(pair, margin_usd, leverage)
    except Exception as e:
        logger.warning("[CONTRACT] open error: %s", e)
        return json_error(f"Open position failed: {e}", 500)

    return json_ok({
        "ok": True,
        "pair": pair,
        "side": side,
        "margin_usd": margin_usd,
        "leverage": leverage,
        "tx_hash": tx_hash,
        "dry_run": cfg.dry_run,
    })


async def handle_contract_close(request: web.Request) -> web.Response:
    """POST /api/contract/close — 平仓。"""
    try:
        data = await request.json()
    except Exception:
        return json_error("Invalid JSON", 400)

    pair = (data.get("pair") or "").strip().upper()
    if not pair:
        return json_error("pair is required", 400)

    trader = _get_contract_trader(request.app)
    if not trader:
        return json_error("ContractTrader not initialized", 500)

    try:
        tx_hash = await trader.close_position(pair)
    except Exception as e:
        logger.warning("[CONTRACT] close error: %s", e)
        return json_error(f"Close position failed: {e}", 500)

    return json_ok({
        "ok": True,
        "pair": pair,
        "tx_hash": tx_hash,
    })


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

    # AERO 趋势策略
    app.router.add_get("/api/aero/state", handle_aero_state)

    # 合约交易
    app.router.add_get("/api/contract/state", handle_contract_state)
    app.router.add_post("/api/contract/open", handle_contract_open)
    app.router.add_post("/api/contract/close", handle_contract_close)

    # 均值回归策略
    app.router.add_get("/api/mean-reversion/state", handle_mean_reversion_state)

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
