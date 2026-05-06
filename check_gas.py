"""Check current Base chain gas price and estimate swap cost."""
import asyncio
import sys
sys.path.insert(0, '.')
from src.rpc.router import RPCRouter
from src.config.loader import load_config
cfg = load_config()
w3 = RPCRouter(cfg.rpc_http_url, cfg.rpc_http_url_fallback)

async def main():
    gp = await w3.eth.gas_price
    print(f"Gas price: {gp} wei = {gp/1e9:.6f} gwei")
    gas_units = 839528
    eth_cost = gas_units * gp / 1e18
    print(f"839k gas x {gp/1e9:.4f} gwei = {eth_cost:.8f} ETH")
    print(f"At ETH $1800: ${eth_cost*1800:.4f}")
    print(f"At ETH $3000: ${eth_cost*3000:.4f}")

asyncio.run(main())
