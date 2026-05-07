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
class AeroTrendConfig:
    enabled: bool = False
    pool_address: str = "0xcddac48af89589052ff14a3cacf58596fe7e2be2"
    aero_address: str = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
    usdc_address: str = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    # 买入条件
    min_return_5m: float = 0.02
    max_return_5m: float = 0.08
    min_return_15m: float = 0.04
    max_return_30m: float = 0.40
    min_volume_ratio: float = 3.0
    min_buy_pressure: float = 0.65
    min_liquidity_usd: float = 200000
    max_slippage_buy: float = 0.01
    # 卖出条件
    stop_loss_pct: float = 0.07
    time_stop_minutes: int = 60
    time_stop_min_profit: float = 0.03
    take_profit_1_pct: float = 0.10
    take_profit_1_ratio: float = 0.30
    take_profit_2_pct: float = 0.20
    take_profit_2_ratio: float = 0.30
    trailing_stop_drawdown: float = 0.08
    # 仓位
    position_size_pct: float = 0.05
    position_size_reduced: float = 0.025
    consecutive_loss_limit: int = 5
    daily_profit_cap: float = 0.08
    daily_loss_limit_pct: float = 0.03
    cooldown_minutes: int = 30
    poll_interval_sec: float = 60
    # 强回踩
    pullback_min: float = 0.05
    pullback_max: float = 0.15
    pullback_volume_ratio: float = 2.0
    pullback_buy_pressure: float = 0.60


@dataclass
class ContractConfig:
    enabled: bool = False
    pairs: list[str] = field(default_factory=lambda: ["BTC/USDC", "ETH/USDC"])
    default_leverage: int = 3
    max_leverage_main: int = 5
    max_leverage_alt: int = 3
    max_margin_per_position: float = 0.3           # 单品种最大保证金比例
    funding_rate_threshold: float = 0.001           # 资金费率告警阈值
    maintenance_margin_multiplier: float = 2.0      # 维持保证金倍数
    poll_interval_sec: float = 30


@dataclass
class SniperConfig:
    enabled: bool = False
    virtuals_club_url: str = "https://virtuals.club"
    email: str = ""
    password: str = ""
    leaderboard_path: str = "/api/public/leaderboard"
    buy_amount_usdc: float = 20
    min_window_sec: int = 60
    max_window_sec: int = 5880
    max_concentration_pct: float = 40
    poll_interval_sec: int = 30
    max_active_targets: int = 3
    leaderboard_top_n: int = 50


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
    aero_trend: AeroTrendConfig = field(default_factory=AeroTrendConfig)
    sniper: SniperConfig = field(default_factory=SniperConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
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


def _parse_contract(raw: dict | None) -> ContractConfig:
    if not raw:
        return ContractConfig(enabled=False)
    return ContractConfig(
        enabled=bool(raw.get("enabled", True)),
        pairs=list(raw.get("pairs", ["BTC/USDC", "ETH/USDC"])),
        default_leverage=int(raw.get("default_leverage", 3)),
        max_leverage_main=int(raw.get("max_leverage_main", 5)),
        max_leverage_alt=int(raw.get("max_leverage_alt", 3)),
        max_margin_per_position=float(raw.get("max_margin_per_position", 0.3)),
        funding_rate_threshold=float(raw.get("funding_rate_threshold", 0.001)),
        maintenance_margin_multiplier=float(raw.get("maintenance_margin_multiplier", 2.0)),
        poll_interval_sec=float(raw.get("poll_interval_sec", 30)),
    )


def _parse_sniper(raw: dict | None) -> SniperConfig:
    if not raw:
        return SniperConfig(enabled=False)
    return SniperConfig(
        enabled=bool(raw.get("enabled", True)),
        virtuals_club_url=str(raw.get("virtuals_club_url", "https://virtuals.club")).rstrip("/"),
        email=str(raw.get("email", "")),
        password=str(raw.get("password", "")),
        leaderboard_path=str(raw.get("leaderboard_path", "/api/public/leaderboard")),
        buy_amount_usdc=float(raw.get("buy_amount_usdc", 20)),
        min_window_sec=int(raw.get("min_window_sec", 60)),
        max_window_sec=int(raw.get("max_window_sec", 5880)),
        max_concentration_pct=float(raw.get("max_concentration_pct", 40)),
        poll_interval_sec=int(raw.get("poll_interval_sec", 30)),
        max_active_targets=int(raw.get("max_active_targets", 3)),
        leaderboard_top_n=int(raw.get("leaderboard_top_n", 50)),
    )


def _parse_aero(raw: dict | None) -> AeroTrendConfig:
    if not raw:
        return AeroTrendConfig(enabled=False)
    return AeroTrendConfig(
        enabled=bool(raw.get("enabled", True)),
        pool_address=str(raw.get("pool_address", "")).lower(),
        aero_address=str(raw.get("aero_address", "")).lower(),
        usdc_address=str(raw.get("usdc_address", "")).lower(),
        min_return_5m=float(raw.get("min_return_5m", 0.02)),
        max_return_5m=float(raw.get("max_return_5m", 0.08)),
        min_return_15m=float(raw.get("min_return_15m", 0.04)),
        max_return_30m=float(raw.get("max_return_30m", 0.40)),
        min_volume_ratio=float(raw.get("min_volume_ratio", 3.0)),
        min_buy_pressure=float(raw.get("min_buy_pressure", 0.65)),
        min_liquidity_usd=float(raw.get("min_liquidity_usd", 200000)),
        max_slippage_buy=float(raw.get("max_slippage_buy", 0.01)),
        stop_loss_pct=float(raw.get("stop_loss_pct", 0.07)),
        time_stop_minutes=int(raw.get("time_stop_minutes", 60)),
        time_stop_min_profit=float(raw.get("time_stop_min_profit", 0.03)),
        take_profit_1_pct=float(raw.get("take_profit_1_pct", 0.10)),
        take_profit_1_ratio=float(raw.get("take_profit_1_ratio", 0.30)),
        take_profit_2_pct=float(raw.get("take_profit_2_pct", 0.20)),
        take_profit_2_ratio=float(raw.get("take_profit_2_ratio", 0.30)),
        trailing_stop_drawdown=float(raw.get("trailing_stop_drawdown", 0.08)),
        position_size_pct=float(raw.get("position_size_pct", 0.05)),
        position_size_reduced=float(raw.get("position_size_reduced", 0.025)),
        consecutive_loss_limit=int(raw.get("consecutive_loss_limit", 5)),
        daily_profit_cap=float(raw.get("daily_profit_cap", 0.08)),
        daily_loss_limit_pct=float(raw.get("daily_loss_limit_pct", 0.03)),
        cooldown_minutes=int(raw.get("cooldown_minutes", 30)),
        poll_interval_sec=float(raw.get("poll_interval_sec", 60)),
        pullback_min=float(raw.get("pullback_min", 0.05)),
        pullback_max=float(raw.get("pullback_max", 0.15)),
        pullback_volume_ratio=float(raw.get("pullback_volume_ratio", 2.0)),
        pullback_buy_pressure=float(raw.get("pullback_buy_pressure", 0.60)),
    )


def _parse_yaml(y: dict) -> dict:
    raw_buyback = y.get("buyback_watch", {}) or {}
    dca_raw = y.get("dca")
    deep_buy_raw = y.get("deep_buy")
    grid_raw = y.get("grid")
    aero_raw = y.get("aero_trend")
    contract_raw = y.get("contract")
    sniper_raw = y.get("sniper")
    return dict(
        dca=_parse_dca(dca_raw),
        deep_buy=_parse_deep_buy(deep_buy_raw),
        grid=_parse_grid(grid_raw),
        aero_trend=_parse_aero(aero_raw),
        contract=_parse_contract(contract_raw),
        sniper=_parse_sniper(sniper_raw),
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
