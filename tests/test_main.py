from src.config.loader import Config, TargetConfig
from src.main import validate_runtime_config


def _make_config() -> Config:
    return Config(
        rpc_ws_url="wss://example.com/ws",
        rpc_http_url="https://example.com/http",
        rpc_http_url_fallback="",
        private_key="0xabc",
        wallet_address="0x123",
        okx_api_key="key",
        okx_secret_key="secret",
        okx_passphrase="pass",
        copy_targets=[TargetConfig(address="0xtarget")],
        trade_mode="ratio",
        trade_ratio=0.5,
        trade_fixed_usd=50,
        trade_max_usd=100,
        token_whitelist=[],
        min_trade_usd=0,
        daily_loss_limit_usd=10,
        slippage=0.01,
        gas_limit_gwei=50,
        take_profit_roi=0.3,
        take_profit_check_sec=60,
        feishu_webhook_url="",
        daily_report_hour_utc=11,
        dry_run=True,
        poll_interval_sec=60,
    )


def test_validate_runtime_config_accepts_valid_config():
    cfg = _make_config()
    assert validate_runtime_config(cfg) == []


def test_validate_runtime_config_rejects_duplicate_targets():
    cfg = _make_config()
    cfg.copy_targets.append(TargetConfig(address="0xtarget"))
    issues = validate_runtime_config(cfg)
    assert "copy_targets contains duplicate address: 0xtarget" in issues


def test_validate_runtime_config_rejects_invalid_values():
    cfg = _make_config()
    cfg.trade_mode = "invalid"
    cfg.slippage = 1.5
    cfg.poll_interval_sec = 0
    issues = validate_runtime_config(cfg)
    assert "trade_mode must be 'ratio' or 'fixed', got 'invalid'" in issues
    assert "slippage must be between 0 and 1" in issues
    assert "poll_interval_sec must be > 0" in issues
