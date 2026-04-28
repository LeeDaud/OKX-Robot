"""
配置加载：.env + config.yaml。支持热更新（reload_yaml）。
"""
import os
import yaml
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class TargetConfig:
    address: str
    remark: str | None = None              # 备注，如 "EFO"
    trade_mode: str | None = None          # 不填则继承全局
    trade_ratio: float | None = None
    trade_fixed_usd: float | None = None
    trade_max_usd: float | None = None
    trade_fixed_virtuals: float | None = None


@dataclass
class Config:
    # 链接
    rpc_ws_url: str
    rpc_http_url: str
    rpc_http_url_fallback: str  # 可选，空字符串 = 不启用
    # 钱包
    private_key: str
    wallet_address: str
    # OKX API
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    # 跟单目标（含各自独立配置）
    copy_targets: list[TargetConfig]
    # 回购地址簿：回购地址 → 目标代币地址
    buyback_watch: dict[str, str]
    # 基础代币（VIRTUAL | USDC），决定交易支付/收款代币
    base_token: str
    # 跟单金额（USDC 模式）
    trade_mode: str               # ratio | fixed
    trade_ratio: float            # ratio 模式：空闲 USDC 余额的百分比
    trade_fixed_usd: float        # fixed 模式：每笔固定金额（USDC）
    trade_max_usd: float          # 单笔上限（两种模式都生效，0 = 不限）
    # 跟单金额（VIRTUAL 模式）
    trade_fixed_virtuals: float   # 每笔固定 VIRTUAL 数量
    # 过滤
    token_whitelist: list[str]
    min_trade_usd: float
    # 风控
    daily_loss_limit_usd: float
    slippage: float
    gas_limit_gwei: float
    take_profit_roi: float      # 0 = 不启用
    take_profit_check_sec: float
    # 通知
    feishu_webhook_url: str
    daily_report_hour_utc: int
    # 运行
    dry_run: bool
    poll_interval_sec: float
    trade_retry: int = 0             # 跟单失败重试次数（0 = 不重试）


def _parse_targets(raw: list) -> list[TargetConfig]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(TargetConfig(address=item.lower()))
        elif isinstance(item, dict):
            result.append(TargetConfig(
                address=item["address"].lower(),
                remark=item.get("remark"),
                trade_mode=item.get("trade_mode"),
                trade_ratio=_maybe_float(item.get("trade_ratio")),
                trade_fixed_usd=_maybe_float(item.get("trade_fixed_usd")),
                trade_max_usd=_maybe_float(item.get("trade_max_usd")),
                trade_fixed_virtuals=_maybe_float(item.get("trade_fixed_virtuals")),
            ))
    return result


def _maybe_float(v) -> float | None:
    if v is None:
        return None
    return float(v)


def target_to_dict(t: TargetConfig) -> dict:
    d = {"address": t.address}
    if t.remark is not None:
        d["remark"] = t.remark
    if t.trade_mode is not None:
        d["trade_mode"] = t.trade_mode
    if t.trade_ratio is not None:
        d["trade_ratio"] = t.trade_ratio
    if t.trade_fixed_usd is not None:
        d["trade_fixed_usd"] = t.trade_fixed_usd
    if t.trade_max_usd is not None:
        d["trade_max_usd"] = t.trade_max_usd
    if t.trade_fixed_virtuals is not None:
        d["trade_fixed_virtuals"] = t.trade_fixed_virtuals
    return d


def config_to_safe_dict(cfg: Config) -> dict:
    """序列化配置为 dict（排除私密字段），用于 API 响应。"""
    return {
        "base_token": cfg.base_token,
        "trade_mode": cfg.trade_mode,
        "trade_ratio": cfg.trade_ratio,
        "trade_fixed_usd": cfg.trade_fixed_usd,
        "trade_max_usd": cfg.trade_max_usd,
        "trade_fixed_virtuals": cfg.trade_fixed_virtuals,
        "token_whitelist": cfg.token_whitelist,
        "min_trade_usd": cfg.min_trade_usd,
        "daily_loss_limit_usd": cfg.daily_loss_limit_usd,
        "slippage": cfg.slippage,
        "gas_limit_gwei": cfg.gas_limit_gwei,
        "take_profit_roi": cfg.take_profit_roi,
        "take_profit_check_sec": cfg.take_profit_check_sec,
        "dry_run": cfg.dry_run,
        "poll_interval_sec": cfg.poll_interval_sec,
        "wallet_address": cfg.wallet_address,
        "copy_targets": [target_to_dict(t) for t in cfg.copy_targets],
    }


def write_config(cfg: Config, yaml_path: str = "config.yaml") -> None:
    """将 Config 对象写回 config.yaml。"""
    data = {
        "copy_targets": [target_to_dict(t) for t in cfg.copy_targets],
        "base_token": cfg.base_token,
        "trade_mode": cfg.trade_mode,
        "trade_ratio": cfg.trade_ratio,
        "trade_fixed_usd": cfg.trade_fixed_usd,
        "trade_max_usd": cfg.trade_max_usd,
        "trade_fixed_virtuals": cfg.trade_fixed_virtuals,
        "token_whitelist": cfg.token_whitelist,
        "min_trade_usd": cfg.min_trade_usd,
        "daily_loss_limit_usd": cfg.daily_loss_limit_usd,
        "slippage": cfg.slippage,
        "gas_limit_gwei": cfg.gas_limit_gwei,
        "take_profit_roi": cfg.take_profit_roi,
        "take_profit_check_sec": cfg.take_profit_check_sec,
        "dry_run": cfg.dry_run,
        "poll_interval_sec": cfg.poll_interval_sec,
    }
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _parse_yaml(y: dict) -> dict:
    raw_buyback = y.get("buyback_watch", {}) or {}
    return dict(
        copy_targets=_parse_targets(y.get("copy_targets", [])),
        buyback_watch={k.lower(): v.lower() for k, v in raw_buyback.items()},
        base_token=str(y.get("base_token", "USDC")).upper(),
        trade_mode=str(y.get("trade_mode", "ratio")),
        trade_ratio=float(y.get("trade_ratio", 0.5)),
        trade_fixed_usd=float(y.get("trade_fixed_usd", 50)),
        trade_max_usd=float(y.get("trade_max_usd", 100)),
        trade_fixed_virtuals=float(y.get("trade_fixed_virtuals", 30)),
        token_whitelist=[t.lower() for t in y.get("token_whitelist", [])],
        min_trade_usd=float(y.get("min_trade_usd", 0)),
        daily_loss_limit_usd=float(y.get("daily_loss_limit_usd", 50)),
        slippage=float(y.get("slippage", 0.01)),
        gas_limit_gwei=float(y.get("gas_limit_gwei", 50)),
        take_profit_roi=float(y.get("take_profit_roi", 0)),
        take_profit_check_sec=float(y.get("take_profit_check_sec", 60)),
        feishu_webhook_url=y.get("feishu_webhook_url", ""),
        daily_report_hour_utc=int(y.get("daily_report_hour_utc", 16)),
        dry_run=bool(y.get("dry_run", True)),
        poll_interval_sec=float(y.get("poll_interval_sec", 2)),
        trade_retry=int(y.get("trade_retry", 0)),
    )


def load_config(yaml_path: str = "config.yaml", env_path: str = ".env") -> Config:
    load_dotenv(env_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)

    return Config(
        rpc_ws_url=os.environ["RPC_WS_URL"],
        rpc_http_url=os.environ["RPC_HTTP_URL"],
        rpc_http_url_fallback=os.environ.get("RPC_HTTP_URL_FALLBACK", ""),
        private_key=os.environ["PRIVATE_KEY"],
        wallet_address=os.environ["WALLET_ADDRESS"],
        okx_api_key=os.environ["OKX_API_KEY"],
        okx_secret_key=os.environ["OKX_SECRET_KEY"],
        okx_passphrase=os.environ["OKX_PASSPHRASE"],
        **_parse_yaml(y),
    )


def reload_yaml(cfg: Config, yaml_path: str = "config.yaml") -> Config:
    with open(yaml_path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)
    for k, v in _parse_yaml(y).items():
        setattr(cfg, k, v)
    return cfg
