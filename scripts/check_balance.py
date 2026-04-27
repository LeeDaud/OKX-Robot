import asyncio, sys
sys.path.insert(0, '.')
from web3 import AsyncWeb3
from src.config.loader import load_config

BALANCE_ABI = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]

async def main():
    cfg = load_config()
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
    wallet = AsyncWeb3.to_checksum_address(cfg.wallet_address)

    token = AsyncWeb3.to_checksum_address('0xc2bceb0ee69455da32abb10a5ba81c0299a925c8')
    contract = w3.eth.contract(address=token, abi=BALANCE_ABI)
    bal = await contract.functions.balanceOf(wallet).call()
    print(f'Token: {bal} ({bal/10**18})')

    usdc = AsyncWeb3.to_checksum_address('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913')
    uc = w3.eth.contract(address=usdc, abi=BALANCE_ABI)
    ubal = await uc.functions.balanceOf(wallet).call()
    print(f'USDC: {ubal} ({ubal/10**6})')

    eth = await w3.eth.get_balance(wallet)
    print(f'ETH: {eth} ({eth/10**18})')

asyncio.run(main())
