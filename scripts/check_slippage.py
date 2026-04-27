import asyncio, sys
sys.path.insert(0, '.')
from src.config.loader import load_config
from src.executor.okx_client import OKXDexClient
from src.monitor.decoder import USDC_BASE

async def check():
    cfg = load_config()
    print('Current slippage:', cfg.slippage)
    async with OKXDexClient(cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase) as okx:
        for slip in [0.01, 0.05, 0.10, 0.20, 0.30]:
            tx = await okx.build_swap_tx(USDC_BASE, '0xc2bceb0ee69455da32abb10a5ba81c0299a925c8', 1000000, cfg.wallet_address, slip)
            if tx:
                tx_data = tx.get("tx", {})
                min_recv = tx_data.get("minReceiveAmount", "?")
                gas = tx_data.get("gas", "?")
                to = str(tx_data.get("to", ""))[:20]
                print(f'slippage={slip*100}%: OK | minReceiveAmount={min_recv} | gas={gas} | to={to}')
            else:
                print(f'slippage={slip*100}%: FAIL')

asyncio.run(check())
