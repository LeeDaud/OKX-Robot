import asyncio, sys
sys.path.insert(0, '.')
from web3 import AsyncWeb3
from src.config.loader import load_config
from src.monitor.decoder import TRANSFER_TOPIC

async def check():
    cfg = load_config()
    wallet = cfg.wallet_address.lower()
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_http_url))
    receipt = await w3.eth.get_transaction_receipt('0x595f5f8df52488621f2f62bf5e83f9d88a89bdd0372a255050e03367488ab0a8')

    target_token = '0xc2bceb0ee69455da32abb10a5ba81c0299a925c8'
    wallet_padded = "0x" + "0" * 24 + wallet[2:]
    transfer_topic = TRANSFER_TOPIC.lstrip("0x").lower()

    print(f'wallet: {wallet}')
    print(f'wallet_padded: {wallet_padded}')
    print(f'transfer_topic: {transfer_topic}')

    for i, log in enumerate(receipt['logs']):
        addr = str(log['address'])
        topics = log.get('topics', [])
        if len(topics) < 3:
            continue
        topic0 = (topics[0].hex() if isinstance(topics[0], bytes) else topics[0]).lstrip("0x").lower()
        if topic0 != transfer_topic:
            continue
        if addr.lower() != target_token:
            continue

        to_addr = topics[2]
        to_hex = (to_addr.hex() if isinstance(to_addr, bytes) else to_addr).lower()
        data = log.get('data', b'')
        if isinstance(data, bytes) and len(data) >= 32:
            value = int.from_bytes(data[:32], 'big')

        print(f'\n=== log[{i}] target token Transfer ===')
        print(f'  addr: {addr}')
        print(f'  topic0: {topic0[:40]}')
        print(f'  topic2 (to): {to_hex}')
        print(f'  topic2 == wallet_padded: {to_hex == wallet_padded}')
        print(f'  value: {value}')
        print(f'  value formatted: {value / 10**18}')

asyncio.run(check())
