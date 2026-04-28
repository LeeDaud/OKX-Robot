"""
配置管理 API。
"""
import os
import yaml
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config.loader import (
    load_config, write_config, config_to_safe_dict,
    TargetConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

YAML_PATH = "config.yaml"
ENV_PATH = ".env"

_cfg_cache: dict | None = None


def _read_yaml() -> dict:
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(data: dict) -> None:
    with open(YAML_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _read_env_lines() -> list[str]:
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        return f.readlines()


def _write_env_lines(lines: list[str]) -> None:
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _update_env_var(key: str, value: str) -> None:
    """更新 .env 文件中某个变量的值（保留注释和顺序）。"""
    lines = _read_env_lines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    _write_env_lines(lines)
    os.environ[key] = value


@router.get("/config")
async def get_config():
    """返回完整配置（不含密钥）。"""
    cfg = load_config(YAML_PATH, ENV_PATH)
    safe = config_to_safe_dict(cfg)
    # 补充 YAML 中但 config_to_safe_dict 不包含的字段
    ydata = _read_yaml()
    safe["buyback_watch"] = ydata.get("buyback_watch", {})
    return safe


class TargetUpdate(BaseModel):
    address: str
    remark: str | None = None
    trade_mode: str | None = None
    trade_ratio: float | None = None
    trade_fixed_usd: float | None = None
    trade_max_usd: float | None = None
    trade_fixed_virtuals: float | None = None


class TargetPatch(BaseModel):
    """不含 address，仅用于 PUT 更新（address 来自路径参数）。"""
    remark: str | None = None
    trade_mode: str | None = None
    trade_ratio: float | None = None
    trade_fixed_usd: float | None = None
    trade_max_usd: float | None = None
    trade_fixed_virtuals: float | None = None


@router.post("/config/targets")
async def add_target(target: TargetUpdate):
    """添加跟单目标。"""
    ydata = _read_yaml()
    targets = ydata.get("copy_targets", []) or []
    addr = target.address.lower()
    if any(t.get("address", "").lower() == addr for t in targets if isinstance(t, dict)):
        raise HTTPException(400, "目标地址已存在")
    entry = {"address": addr}
    if target.remark:
        entry["remark"] = target.remark
    if target.trade_mode:
        entry["trade_mode"] = target.trade_mode
    if target.trade_ratio is not None:
        entry["trade_ratio"] = target.trade_ratio
    if target.trade_fixed_usd is not None:
        entry["trade_fixed_usd"] = target.trade_fixed_usd
    if target.trade_max_usd is not None:
        entry["trade_max_usd"] = target.trade_max_usd
    if target.trade_fixed_virtuals is not None:
        entry["trade_fixed_virtuals"] = target.trade_fixed_virtuals
    targets.append(entry)
    ydata["copy_targets"] = targets
    _write_yaml(ydata)
    return {"ok": True}


@router.put("/config/targets/{address}")
async def update_target(address: str, target: TargetPatch):
    """更新跟单目标配置。"""
    ydata = _read_yaml()
    targets = ydata.get("copy_targets", []) or []
    addr_lower = address.lower()
    found = False
    for t in targets:
        if isinstance(t, dict) and t.get("address", "").lower() == addr_lower:
            if target.remark is not None:
                t["remark"] = target.remark
            if target.trade_mode is not None:
                t["trade_mode"] = target.trade_mode
            if target.trade_ratio is not None:
                t["trade_ratio"] = target.trade_ratio
            if target.trade_fixed_usd is not None:
                t["trade_fixed_usd"] = target.trade_fixed_usd
            if target.trade_max_usd is not None:
                t["trade_max_usd"] = target.trade_max_usd
            if target.trade_fixed_virtuals is not None:
                t["trade_fixed_virtuals"] = target.trade_fixed_virtuals
            found = True
            break
    if not found:
        raise HTTPException(404, "目标地址不存在")
    _write_yaml(ydata)
    return {"ok": True}


@router.delete("/config/targets/{address}")
async def delete_target(address: str):
    """删除跟单目标。"""
    ydata = _read_yaml()
    targets = ydata.get("copy_targets", []) or []
    addr_lower = address.lower()
    new_targets = [t for t in targets if isinstance(t, dict) and t.get("address", "").lower() != addr_lower]
    if len(new_targets) == len(targets):
        raise HTTPException(404, "目标地址不存在")
    ydata["copy_targets"] = new_targets
    _write_yaml(ydata)
    return {"ok": True}


class WalletUpdate(BaseModel):
    wallet_address: str | None = None
    rpc_http_url: str | None = None
    rpc_ws_url: str | None = None
    private_key: str | None = None
    okx_api_key: str | None = None
    okx_secret_key: str | None = None
    okx_passphrase: str | None = None


@router.get("/config/wallet")
async def get_wallet():
    """返回执行钱包信息（不含私钥）。"""
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
    return {
        "wallet_address": os.environ.get("WALLET_ADDRESS", ""),
        "rpc_http_url": os.environ.get("RPC_HTTP_URL", ""),
        "rpc_ws_url": os.environ.get("RPC_WS_URL", ""),
        "has_private_key": bool(os.environ.get("PRIVATE_KEY")),
        "has_okx_api_key": bool(os.environ.get("OKX_API_KEY")),
    }


@router.put("/config/wallet")
async def update_wallet(wallet: WalletUpdate):
    """更新执行钱包配置（写入 .env，重启后生效）。"""
    changes = {}
    if wallet.wallet_address is not None:
        _update_env_var("WALLET_ADDRESS", wallet.wallet_address)
        changes["wallet_address"] = wallet.wallet_address
    if wallet.rpc_http_url is not None:
        _update_env_var("RPC_HTTP_URL", wallet.rpc_http_url)
        changes["rpc_http_url"] = wallet.rpc_http_url
    if wallet.rpc_ws_url is not None:
        _update_env_var("RPC_WS_URL", wallet.rpc_ws_url)
        changes["rpc_ws_url"] = wallet.rpc_ws_url
    if wallet.private_key is not None:
        _update_env_var("PRIVATE_KEY", wallet.private_key)
        changes["private_key"] = "***updated***"
    if wallet.okx_api_key is not None:
        _update_env_var("OKX_API_KEY", wallet.okx_api_key)
        changes["okx_api_key"] = "***updated***"
    if wallet.okx_secret_key is not None:
        _update_env_var("OKX_SECRET_KEY", wallet.okx_secret_key)
        changes["okx_secret_key"] = "***updated***"
    if wallet.okx_passphrase is not None:
        _update_env_var("OKX_PASSPHRASE", wallet.okx_passphrase)
        changes["okx_passphrase"] = "***updated***"
    if not changes:
        raise HTTPException(400, "未提供任何可修改的字段")
    logger.info("Wallet config updated: %s", changes)
    return {"ok": True, "updated": list(changes.keys())}


@router.put("/config/params")
async def update_params(params: dict):
    """更新全局参数。"""
    allowed = {
        "base_token", "trade_mode", "trade_ratio", "trade_fixed_usd",
        "trade_max_usd", "trade_fixed_virtuals", "token_whitelist",
        "min_trade_usd", "daily_loss_limit_usd", "slippage",
        "gas_limit_gwei", "take_profit_roi", "take_profit_check_sec",
        "dry_run", "poll_interval_sec",
    }
    ydata = _read_yaml()
    for k, v in params.items():
        if k not in allowed:
            raise HTTPException(400, f"不允许修改字段: {k}")
        ydata[k] = v
    _write_yaml(ydata)
    return {"ok": True}
