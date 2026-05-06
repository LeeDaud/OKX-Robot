"""
配置加载：.env + config.yaml。支持热更新（reload_yaml）。
"""
import os
import yaml
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class DcaTokenConfig:
    address: str
    amount_usdc: float
    hour: int = 24          # 24 = 次日 00:00
    minute: int = 0
    window_minutes: int = 30


@dataclass
class DeepBuyConfig:
    enabled: bool = False
    threshold_usd: float = 0.004
    amount_usdc: float = 10
    cooldown_days: int = 5


@dataclass
class DcaConfig:
    enabled: bool = False
    tokens: list[DcaTokenConfig] = field(default_factory=list)


@dataclass
class GridConfig:
    enabled: bool = False
    token: str = ""
    levels: int = 6
    spread_pct: float = 2.0
    investment_usdc: float = 60.0
    profit_pct: float = 3.0
    max_slots: int = 12
    volatility_adjust: bool = False
    volatility_window: int = 20


@dataclass
class Config:
    # 必填字段（无默认值，必须在前）
    rpc_http_url: str
    private_key: str
    wallet_address: str
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    # 可选字段（有默认值，必须在后）
    rpc_http_url_fallback: str = ""
    dca: DcaConfig = field(default_factory=DcaConfig)
    deep_buy: DeepBuyConfig = field(default_factory=DeepBuyConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    buyback_watch: dict[str, str] = field(default_factory=dict)
    base_token: str = "VIRTUAL"
    daily_loss_limit_usd: float = 10
    slippage: float = 0.01
    gas_limit_gwei: float = 50
    take_profit_roi: float = 0.30
    take_profit_check_sec: float = 60
    feishu_webhook_url: str = ""
    dry_run: bool = True
    poll_interval_sec: float = 10


def _parse_dca(raw: dict | None) -> DcaConfig:
    if not raw:
        return DcaConfig(enabled=False)
    enabled = bool(raw.get("enabled", True))
    tokens_raw = raw.get("tokens", [])
    tokens = []
    for t in tokens_raw:
        tokens.append(DcaTokenConfig(
            address=t["address"].lower(),
            amount_usdc=float(t.get("amount_usdc", 2)),
            hour=int(t.get("hour", 24)),
            minute=int(t.get("minute", 0)),
            window_minutes=int(t.get("window_minutes", 30)),
        ))
    return DcaConfig(enabled=enabled, tokens=tokens)


def _parse_deep_buy(raw: dict | None) -> DeepBuyConfig:
    if not raw:
        return DeepBuyConfig(enabled=False)
    return DeepBuyConfig(
        enabled=bool(raw.get("enabled", True)),
        threshold_usd=float(raw.get("threshold_usd", 0.004)),
        amount_usdc=float(raw.get("amount_usdc", 10)),
        cooldown_days=int(raw.get("cooldown_days", 5)),
    )


def _parse_grid(raw: dict | None) -> GridConfig:
    if not raw:
        return GridConfig(enabled=False)
    return GridConfig(
        enabled=bool(raw.get("enabled", True)),
        token=str(raw.get("token", "")).lower(),
        levels=int(raw.get("levels", 6)),
        spread_pct=float(raw.get("spread_pct", 2.0)),
        investment_usdc=float(raw.get("investment_usdc", 60)),
        profit_pct=float(raw.get("profit_pct", 3.0)),
        max_slots=int(raw.get("max_slots", 12)),
        volatility_adjust=bool(raw.get("volatility_adjust", False)),
        volatility_window=int(raw.get("volatility_window", 20)),
    )


def _parse_yaml(y: dict) -> dict:
    raw_buyback = y.get("buyback_watch", {}) or {}
    dca_raw = y.get("dca")
    deep_buy_raw = y.get("deep_buy")
    grid_raw = y.get("grid")
    return dict(
        dca=_parse_dca(dca_raw),
        deep_buy=_parse_deep_buy(deep_buy_raw),
        grid=_parse_grid(grid_raw),
        buyback_watch={k.lower(): v.lower() for k, v in raw_buyback.items()},
        base_token=str(y.get("base_token", "VIRTUAL")).upper(),
        daily_loss_limit_usd=float(y.get("daily_loss_limit_usd", 10)),
        slippage=float(y.get("slippage", 0.01)),
        gas_limit_gwei=float(y.get("gas_limit_gwei", 50)),
        take_profit_roi=float(y.get("take_profit_roi", 0)),
        take_profit_check_sec=float(y.get("take_profit_check_sec", 60)),
        feishu_webhook_url=y.get("feishu_webhook_url", ""),
        dry_run=bool(y.get("dry_run", True)),
        poll_interval_sec=float(y.get("poll_interval_sec", 10)),
    )


def load_config(yaml_path: str = "config.yaml", env_path: str = ".env") -> Config:
    load_dotenv(env_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)

    return Config(
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
