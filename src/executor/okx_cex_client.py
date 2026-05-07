"""
OKX CEX v5 API 封装（永续合约专用）。
文档：https://www.okx.com/docs-v5

与 DEX v6 客户端的核心差异：
- 签名规则不同（v5 GET 请求不包含 query string）
- 请求路径为 /api/v5/*
- 接口域：市场数据、账户、交易
"""
import asyncio
import hashlib
import hmac
import base64
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://www.okx.com"


def _sign_v5(secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
    """OKX v5 签名：
    - GET: timestamp + GET + path（不含 query string）
    - POST: timestamp + POST + path + json_body
    """
    msg = timestamp + method.upper() + path + body
    mac = hmac.new(secret.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _headers(api_key: str, secret: str, passphrase: str, path: str,
             method: str = "GET", body: str = "") -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": _sign_v5(secret, ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


class OKXCexClient:
    """OKX CEX v5 API 客户端（永续合约子集）。"""

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

    # ── 市场数据 ────────────────────────────────────────────────────

    async def get_ticker(self, inst_id: str) -> Optional[dict]:
        """获取最新 ticker 数据。"""
        path = "/api/v5/market/ticker"
        params = {"instId": inst_id}
        return await self._get(path, params)

    async def get_candles(self, inst_id: str, bar: str = "1D",
                          after: str = "", before: str = "",
                          limit: str = "100") -> list[dict]:
        """获取 K 线数据。

        Args:
            inst_id: 交易对，如 "BTC-USDT"
            bar: 周期 "1m"/"3m"/"5m"/"15m"/"30m"/"1H"/"2H"/"4H"/"6H"/"12H"/"1D"/"1W"/"1M"
            after: 筛选此时间戳之前的 K 线（更旧）
            before: 筛选此时间戳之后的 K 线（更新）
            limit: 数量上限，最大 300
        Returns:
            [[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], ...]
        """
        path = "/api/v5/market/candles"
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    async def get_history_candles(self, inst_id: str, bar: str = "1D",
                                   after: str = "", before: str = "",
                                   limit: str = "100") -> list[dict]:
        """获取历史 K 线数据（更早的行情数据）。

        Args: 同 get_candles
        """
        path = "/api/v5/market/history-candles"
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    async def get_instruments(self, inst_type: str = "SWAP") -> list[dict]:
        """获取所有合约交易对信息（含 ctVal 面值、最小交易量等）。"""
        path = "/api/v5/public/instruments"
        params = {"instType": inst_type}
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    # ── 账户与持仓 ─────────────────────────────────────────────────

    async def get_balance(self, ccy: str = "") -> Optional[dict]:
        """获取账户余额。ccy 为空时返回所有币种。"""
        path = "/api/v5/account/balance"
        params = {}
        if ccy:
            params["ccy"] = ccy
        return await self._get(path, params)

    async def get_positions(self, inst_type: str = "SWAP") -> list[dict]:
        """获取持仓列表。"""
        path = "/api/v5/account/positions"
        params = {"instType": inst_type}
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    async def get_position(self, inst_id: str) -> Optional[dict]:
        """获取指定交易对的持仓。"""
        path = "/api/v5/account/positions"
        params = {"instId": inst_id}
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            data = resp.get("data", [])
            return data[0] if data else None
        return None

    async def get_risk_state(self) -> Optional[dict]:
        """获取账户风控状态。"""
        path = "/api/v5/account/risk-state"
        return await self._get(path)

    # ── 交易执行 ────────────────────────────────────────────────────

    async def place_order(self, inst_id: str, td_mode: str, side: str,
                          pos_side: str, ord_type: str, sz: str,
                          px: str = "", tp_trigger_px: str = "",
                          tp_ord_px: str = "", sl_trigger_px: str = "",
                          sl_ord_px: str = "") -> Optional[dict]:
        """
        下单。

        Args:
            inst_id: 交易对，如 "BTC-USDT-SWAP"
            td_mode: 保证金模式 "cross" | "isolated"
            side: "buy" | "sell"
            pos_side: "long" | "short"
            ord_type: "market" | "limit" | "post_only"
            sz: 张数
            px: 价格（限价单必填）
            tp_trigger_px/tp_ord_px: 止盈触发价/委托价
            sl_trigger_px/sl_ord_px: 止损触发价/委托价
        """
        path = "/api/v5/trade/order"
        body = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "posSide": pos_side,
            "ordType": ord_type,
            "sz": sz,
        }
        if px:
            body["px"] = px
        if tp_trigger_px:
            body["tpTriggerPx"] = tp_trigger_px
            body["tpOrdPx"] = tp_ord_px or tp_trigger_px
        if sl_trigger_px:
            body["slTriggerPx"] = sl_trigger_px
            body["slOrdPx"] = sl_ord_px or sl_trigger_px

        return await self._post(path, body)

    async def close_position(self, inst_id: str, mgn_mode: str = "cross",
                             pos_side: str = "") -> Optional[dict]:
        """市价全平。不指定 pos_side 时自动识别方向。"""
        path = "/api/v5/trade/close-position"
        body = {"instId": inst_id, "mgnMode": mgn_mode}
        if pos_side:
            body["posSide"] = pos_side
        return await self._post(path, body)

    async def cancel_order(self, inst_id: str, ord_id: str = "") -> Optional[dict]:
        """撤单。"""
        path = "/api/v5/trade/cancel-order"
        body = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        return await self._post(path, body)

    async def get_pending_orders(self, inst_type: str = "SWAP") -> list[dict]:
        """获取当前挂单。"""
        path = "/api/v5/trade/orders-pending"
        params = {"instType": inst_type}
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    async def get_fills(self, inst_id: str = "") -> list[dict]:
        """获取成交明细。"""
        path = "/api/v5/trade/fills"
        params = {}
        if inst_id:
            params["instId"] = inst_id
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    # ── 止损止盈（订单算法） ──────────────────────────────────────

    async def place_algo_order(self, inst_id: str, td_mode: str, side: str,
                               pos_side: str, sz: str,
                               sl_trigger_px: str = "", sl_ord_px: str = "",
                               tp_trigger_px: str = "", tp_ord_px: str = "",
                               sl_trigger_px_type: str = "last") -> Optional[dict]:
        """
        设置止损/止盈（独立于开仓调用）。
        适用于开仓后单独追加止盈止损。
        """
        path = "/api/v5/trade/order-algo"
        body = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "posSide": pos_side,
            "sz": sz,
            "algType": "conditional",
        }
        if sl_trigger_px:
            body["slTriggerPx"] = sl_trigger_px
            body["slOrdPx"] = sl_ord_px or sl_trigger_px
            body["slTriggerPxType"] = sl_trigger_px_type
        if tp_trigger_px:
            body["tpTriggerPx"] = tp_trigger_px
            body["tpOrdPx"] = tp_ord_px or tp_trigger_px
        return await self._post(path, body)

    async def cancel_algo_order(self, inst_id: str, algo_id: str) -> Optional[dict]:
        """撤销止损止盈单。"""
        path = "/api/v5/trade/cancel-algos"
        body = [
            {"instId": inst_id, "algoId": algo_id}
        ]
        return await self._post(path, body)

    async def get_algo_orders(self, inst_type: str = "SWAP",
                              algo_state: str = "effective") -> list[dict]:
        """获取当前生效的止损止盈单列表。"""
        path = "/api/v5/trade/orders-algo-pending"
        params = {
            "instType": inst_type,
            "algoState": algo_state,
        }
        resp = await self._get_raw(path, params)
        if resp and resp.get("code") == "0":
            return resp.get("data", [])
        return []

    # ── 账户设置 ────────────────────────────────────────────────────

    async def set_leverage(self, inst_id: str, lever: str,
                           mgn_mode: str = "cross",
                           pos_side: str = "long") -> Optional[dict]:
        """
        设置杠杆倍数。
        同一交易对的 long/short 方向需要分别设置。
        """
        path = "/api/v5/account/set-leverage"
        body = {
            "instId": inst_id,
            "lever": lever,
            "mgnMode": mgn_mode,
            "posSide": pos_side,
        }
        return await self._post(path, body)

    async def adjust_margin(self, inst_id: str, pos_side: str,
                            amount: str, direction: str = "add") -> Optional[dict]:
        """
        调整保证金。
        direction: "add" 增加 | "reduce" 减少
        """
        path = "/api/v5/account/position/margin-balance"
        body = {
            "instId": inst_id,
            "posSide": pos_side,
            "amount": amount,
            "type": "add" if direction == "add" else "reduce",
        }
        return await self._post(path, body)

    # ── 内部 HTTP ──────────────────────────────────────────────────

    async def _get(self, path: str, params: dict, retries: int = 2) -> Optional[dict]:
        resp = await self._get_raw(path, params, retries)
        if resp and resp.get("code") == "0":
            data = resp.get("data", [])
            return data[0] if data else None
        return None

    async def _get_raw(self, path: str, params: dict, retries: int = 2) -> Optional[dict]:
        """GET 请求（v5 签名不含 query string）。"""
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{BASE_URL}{path}?{query}"
        headers = _headers(self._api_key, self._secret, self._passphrase,
                           path, method="GET")

        for attempt in range(retries + 1):
            try:
                async with self._session.get(full_url, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("code") != "0":
                        logger.warning("OKX CEX API error [%s]: code=%s msg=%s",
                                       path, data.get("code"), data.get("msg"))
                        return None
                    return data
            except Exception as e:
                if attempt < retries:
                    logger.warning("OKX CEX GET failed (attempt %d/%d): %s",
                                   attempt + 1, retries + 1, e)
                    await asyncio.sleep(1)
                    continue
                logger.error("OKX CEX GET failed after %d attempts: %s",
                             retries + 1, e)
                return None

    async def _post(self, path: str, body: dict, retries: int = 2) -> Optional[dict]:
        """POST 请求（v5 签名含 JSON body）。"""
        json_body = body if isinstance(body, str) else _json_str(body)
        headers = _headers(self._api_key, self._secret, self._passphrase,
                           path, method="POST", body=json_body)

        for attempt in range(retries + 1):
            try:
                async with self._session.post(
                    BASE_URL + path,
                    headers=headers,
                    data=json_body,
                ) as resp:
                    data = await resp.json()
                    if data.get("code") != "0":
                        logger.warning("OKX CEX API error [%s]: code=%s msg=%s",
                                       path, data.get("code"), data.get("msg"))
                        return None
                    return data
            except Exception as e:
                if attempt < retries:
                    logger.warning("OKX CEX POST failed (attempt %d/%d): %s",
                                   attempt + 1, retries + 1, e)
                    await asyncio.sleep(1)
                    continue
                logger.error("OKX CEX POST failed after %d attempts: %s",
                             retries + 1, e)
                return None


def _json_str(d: dict) -> str:
    """轻量 dict → JSON 字符串（不含多余空格，符合 OKX 签名规范）。"""
    import json
    return json.dumps(d, separators=(",", ":"))
