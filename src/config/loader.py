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
    trade_mode: str | None = None
    trade_ratio: float | None = None
    trade_fixed_usd: float | None = None
    trade_max_usd: float | None = None


@dataclass
class Config:
    # 链接
    rpc_ws_url: str
    rpc_http_url: str
    # 钱包
    private_key: str
    wallet_address: str
    # OKX API
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    # 跟单目标（含各自独立配置）
    copy_targets: list[TargetConfig]
    # 全局跟单金额
    trade_mode: str
    trade_ratio: float
    trade_fixed_usd: float
    trade_max_usd: float
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


def _parse_targets(raw: list) -> list[TargetConfig]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(TargetConfig(address=item.lower()))
        elif isinstance(item, dict):
            result.append(TargetConfig(
                address=item["address"].lower(),
                trade_mode=item.get("trade_mode"),
                trade_ratio=float(item["trade_ratio"]) if "trade_ratio" in item else None,
                trade_fixed_usd=float(item["trade_fixed_usd"]) if "trade_fixed_usd" in item else None,
                trade_max_usd=float(item["trade_max_usd"]) if "trade_max_usd" in item else None,
            ))
    return result


def _parse_yaml(y: dict) -> dict:
    return dict(
        copy_targets=_parse_targets(y.get("copy_targets", [])),
        trade_mode=y.get("trade_mode", "ratio"),
        trade_ratio=float(y.get("trade_ratio", 0.10)),
        trade_fixed_usd=float(y.get("trade_fixed_usd", 50)),
        trade_max_usd=float(y.get("trade_max_usd", 0)),
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
    )


def load_config(yaml_path: str = "config.yaml", env_path: str = ".env") -> Config:
    load_dotenv(env_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)

    return Config(
        rpc_ws_url=os.environ["RPC_WS_URL"],
        rpc_http_url=os.environ["RPC_HTTP_URL"],
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
