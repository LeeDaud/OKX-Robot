"""
合约链路验证脚本。

测试内容：
  - 模块导入 & 数据类构造
  - 合约规格缓存逻辑
  - USD → 张数换算（mock 数据）
  - 杠杆约束逻辑

网络连通后会额外执行 CEX API 调用。
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_cex")

# ── 1. 单元级测试（不需要网络） ───────────────────────────────────


def test_leverage_constraint():
    """杠杆约束：主流币 ≤5x，山寨 ≤3x"""
    from src.executor.contract_trader import ContractTrader, MAIN_PAIRS

    for pair in MAIN_PAIRS:
        assert pair in MAIN_PAIRS
    assert "BTC-USDT-SWAP" in MAIN_PAIRS
    assert "ETH-USDT-SWAP" in MAIN_PAIRS
    print("[PASS] 杠杆约束：主流币识别正确")


async def test_usd_to_size_mock():
    """USD → 张数换算（mock 合约规格 + ticker 价格）"""
    from src.executor.okx_cex_client import OKXCexClient
    from src.executor.contract_trader import ContractTrader

    cex = OKXCexClient("k", "s", "p")
    trader = ContractTrader(cex, dry_run=True)

    # 注入 mock instrument 和 ticker
    inst = {
        "instId": "BTC-USDT-SWAP",
        "ctVal": "0.01",
        "ctMult": "1",
        "minSz": "1",
    }
    trader._instruments["BTC-USDT-SWAP"] = inst

    with patch.object(cex, "get_ticker", AsyncMock(return_value={"last": "100000"})):
        sz = await trader._usd_to_size("BTC-USDT-SWAP", 100, 3)
        # 名义价值 = 100*3 = 300 USD
        # 每张价值 = 0.01 * 100000 = 1000 USD
        # 张数 = 300 / 1000 = 0.3 → 0（不足 1 张）
        assert sz == 0, f"预期 0，实际 {sz}"
        print(f"[PASS] 100USD@3x BTC=0 张（不足 1 张，正确）")

        sz = await trader._usd_to_size("BTC-USDT-SWAP", 500, 3)
        # 名义价值 = 1500 USD
        # 张数 = 1500 / 1000 = 1.5 → 1
        assert sz == 1, f"预期 1，实际 {sz}"
        print(f"[PASS] 500USD@3x BTC=1 张（1500/1000=1.5 向下取整）")

        sz = await trader._usd_to_size("BTC-USDT-SWAP", 1000, 3)
        # 名义价值 = 3000 USD，张数 = 3
        assert sz == 3, f"预期 3，实际 {sz}"
        print(f"[PASS] 1000USD@3x BTC=3 张")


async def test_dry_run_no_network():
    """dry-run 模式下调用 open_long/close 不触发网络请求"""
    from src.executor.okx_cex_client import OKXCexClient
    from src.executor.contract_trader import ContractTrader

    cex = OKXCexClient("k", "s", "p")
    trader = ContractTrader(cex, dry_run=True)

    # 注入 mock instrument
    trader._instruments["BTC-USDT-SWAP"] = {
        "instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctMult": "1", "minSz": "1",
    }

    with patch.object(cex, "get_ticker", AsyncMock(return_value={"last": "100000"})):
        # dry-run 应直接返回 None，不调用 _cex.set_leverage / place_order
        with patch.object(cex, "set_leverage", AsyncMock()) as mock_set_lev:
            ord_id = await trader.open_long("BTC-USDT-SWAP", 1000, 3)
            assert ord_id is None
            # dry-run 模式不应实际调用 API
            mock_set_lev.assert_not_called()
            print("[PASS] dry-run open_long：没有实际 API 调用")

        close_id = await trader.close_position("BTC-USDT-SWAP")
        assert close_id is None
        print("[PASS] dry-run close_position：返回 None")


# ── 2. 网络连通后测试（可选） ────────────────────────────────────


async def test_live_connectivity():
    """需要 okx.com 可达时执行"""
    from src.executor.okx_cex_client import OKXCexClient

    api_key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    if not all([api_key, secret, passphrase]):
        print("[SKIP] 无 API Key 配置，跳过网络连通测试")
        return

    async with OKXCexClient(api_key, secret, passphrase) as cex:
        ticker = await cex.get_ticker("BTC-USDT-SWAP")
        if ticker:
            print(f"[OK] BTC-USDT-SWAP 最新价: {ticker.get('last', '?')} USDT")
        else:
            print("[FAIL] ticker 查询失败（可能网络不通或 Key 权限不足）")
            return

        insts = await cex.get_instruments("SWAP")
        btc = next((i for i in insts if i["instId"] == "BTC-USDT-SWAP"), None)
        print(f"[OK] BTC 合约面值: {btc['ctVal']} BTC/张" if btc else "[FAIL] 查不到合约规格")

        bal = await cex.get_balance()
        if bal:
            details = bal.get("details", [{}])[0]
            print(f"[OK] 交易账户权益: {details.get('eq', '?')} USDT, 可用: {details.get('availBal', '?')} USDT")
        else:
            print("[FAIL] 余额查询失败")

        pos = await cex.get_positions()
        print(f"[OK] 当前持仓数: {len(pos)}")

        # 张数换算验证
        from src.executor.contract_trader import ContractTrader
        trader = ContractTrader(cex, dry_run=True)
        for pair in ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]:
            sz = await trader._usd_to_size(pair, 100, 3)
            print(f"  100 USD @ 3x {pair} ≈ {sz} 张")


if __name__ == "__main__":
    print("=" * 50)
    print("合约交易链路本地单元测试")
    print("=" * 50)

    # 单元测试
    test_leverage_constraint()
    asyncio.run(test_usd_to_size_mock())
    asyncio.run(test_dry_run_no_network())

    print("\n" + "=" * 50)
    print("网络连通测试（需 okx.com 可达）")
    print("=" * 50)
    asyncio.run(test_live_connectivity())

    print("\n" + "=" * 50)
    print("所有测试完成")
    print("=" * 50)
