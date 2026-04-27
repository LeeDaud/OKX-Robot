import asyncio, sys
sys.path.insert(0, "/opt/okx-robot")
from web3 import AsyncWeb3
w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(open("/opt/okx-robot/.env").read().split("RPC_HTTP_URL=")[1].split()[0]))
target = "0x0e54ee305cff2b8943bab7f5a4352586ae9ee2be"
padded = "0x" + "0"*24 + target[2:]
tf = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
async def go():
    latest = await w3.eth.block_number
    print("Latest block:", latest)
    logs_out = await w3.eth.get_logs({"fromBlock": 45255996, "toBlock": latest, "topics": [tf, padded]})
    logs_in = await w3.eth.get_logs({"fromBlock": 45255996, "toBlock": latest, "topics": [tf, None, padded]})
    print("From target:", len(logs_out), "events")
    print("To target:  ", len(logs_in), "events")
    for l in logs_out:
        txh = l["transactionHash"].hex() if isinstance(l["transactionHash"], bytes) else l["transactionHash"]
        print("  FROM: tx=" + txh + " token=" + str(l["address"]))
    for l in logs_in:
        txh = l["transactionHash"].hex() if isinstance(l["transactionHash"], bytes) else l["transactionHash"]
        print("  TO:   tx=" + txh + " token=" + str(l["address"]))
    if not logs_out and not logs_in:
        print("No new transactions found. The user may need to make a new trade.")
asyncio.run(go())
