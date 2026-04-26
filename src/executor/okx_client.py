"""
OKX DEX Aggregator API 封装。
文档：https://www.okx.com/web3/build/docs/waas/dex-swap
"""
import asyncio
import hashlib
import hmac
import base64
import time
import logging
from datetime import datetime, timezone
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://www.okx.com"
CHAIN_ID = "8453"  # Base mainnet


def _sign(secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
    msg = timestamp + method.upper() + path + body
    mac = hmac.new(secret.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _headers(api_key: str, secret: str, passphrase: str, path: str, method: str = "GET") -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": _sign(secret, ts, method, path),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


class OKXDexClient:
    def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    async def get_quote(
        self,
        token_in: str,
        token_out: str,
        amount: int,
        slippage: float = 0.01,
    ) -> Optional[dict]:
        """获取报价，返回 OKX API 原始响应。"""
        path = "/api/v5/dex/aggregator/quote"
        params = {
            "chainId": CHAIN_ID,
            "fromTokenAddress": token_in,
            "toTokenAddress": token_out,
            "amount": str(amount),
            "slippage": str(slippage),
        }
        return await self._get(path, params)

    async def build_swap_tx(
        self,
        token_in: str,
        token_out: str,
        amount: int,
        user_addr: str,
        slippage: float = 0.01,
    ) -> Optional[dict]:
        """获取可直接广播的 swap calldata。"""
        path = "/api/v5/dex/aggregator/swap"
        params = {
            "chainId": CHAIN_ID,
            "fromTokenAddress": token_in,
            "toTokenAddress": token_out,
            "amount": str(amount),
            "userWalletAddress": user_addr,
            "slippage": str(slippage),
        }
        return await self._get(path, params)

    async def _get(self, path: str, params: dict, retries: int = 2) -> Optional[dict]:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{query}"
        headers = _headers(self._api_key, self._secret, self._passphrase, full_path)
        url = BASE_URL + full_path

        for attempt in range(retries + 1):
            try:
                async with self._session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("code") != "0":
                        logger.warning("OKX API error: %s", data.get("msg"))
                        return None
                    items = data.get("data") or []
                    if not items:
                        logger.warning("OKX API returned empty data for %s", path)
                        return None
                    return items[0]
            except Exception as e:
                if attempt < retries:
                    logger.warning("OKX API request failed (attempt %d/%d): %s", attempt + 1, retries + 1, e)
                    await asyncio.sleep(1)
                    continue
                logger.error("OKX API request failed after %d attempts: %s", retries + 1, e)
                return None
