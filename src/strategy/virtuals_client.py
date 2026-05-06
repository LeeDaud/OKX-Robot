"""
virtuals.club API 客户端：登录、获取新项目、获取大户持仓排名。
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class WhalePosition:
    wallet: str
    sum_spent_v_est: str = "0"
    sum_token_bought: str = "0"
    avg_cost_v: str = "0"
    breakeven_fdv_v: str = "0"
    breakeven_fdv_usd: str | None = None


@dataclass
class UpcomingProject:
    project_id: str
    name: str
    symbol: str
    token_address: str
    contract_address: str
    pool_address: str
    status: str
    launch_time: str | None
    risk_level: str
    lifecycle_stage: str
    url: str


class VirtualsClubClient:
    """virtuals.club API 客户端。

    需要账号登录获取 session，支持后续切换到公共接口模式。
    """

    def __init__(
        self,
        base_url: str,
        email: str = "",
        password: str = "",
        leaderboard_path: str = "/api/admin/leaderboard",
        signalhub_path: str = "/api/app/signalhub",
        login_path: str = "/api/auth/login",
        timeout_sec: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._leaderboard_path = leaderboard_path
        self._signalhub_path = signalhub_path
        self._login_path = login_path
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session: aiohttp.ClientSession | None = None
        self._cookie_jar: dict[str, str] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                cookies=self._cookie_jar,
            )
        return self._session

    async def login(self) -> bool:
        """用 email/password 登录，保存 session cookie。"""
        if not self._email or not self._password:
            logger.warning("[VCLUB] email/password 未配置，跳过登录")
            return False

        try:
            session = await self._ensure_session()
            resp = await session.post(
                f"{self.base_url}{self._login_path}",
                json={"email": self._email, "password": self._password},
            )
            if resp.status != 200:
                logger.error("[VCLUB] 登录失败: status=%d", resp.status)
                return False

            # 保存 cookies
            for k, v in session.cookie_jar.filter_cookies(self.base_url).items():
                self._cookie_jar[k] = v.value

            logger.info("[VCLUB] 登录成功")
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("[VCLUB] 登录异常: %s", e)
            return False

    async def fetch_upcoming_projects(
        self, limit: int = 50, within_hours: int = 72
    ) -> list[UpcomingProject]:
        """获取 upcoming 项目列表（SignalHub 数据）。"""
        session = await self._ensure_session()
        try:
            resp = await session.get(
                f"{self.base_url}{self._signalhub_path}",
                params={"limit": limit, "within_hours": within_hours},
            )
            if resp.status != 200:
                logger.warning("[VCLUB] upcoming 请求失败: status=%d", resp.status)
                return []

            data = await resp.json()
            items = data.get("items") or []
            result = []
            for item in items:
                result.append(UpcomingProject(
                    project_id=str(item.get("projectId") or item.get("project_id", "")),
                    name=str(item.get("name", "")),
                    symbol=str(item.get("symbol", "")),
                    token_address=str(item.get("tokenAddress") or item.get("token_address", "")),
                    contract_address=str(item.get("contractAddress") or item.get("contract_address", "")),
                    pool_address=str(item.get("poolAddress") or item.get("pool_address", "") or
                                     item.get("internalMarketAddress") or item.get("internal_market_address", "")),
                    status=str(item.get("status", "")),
                    launch_time=item.get("launchTime") or item.get("launch_time"),
                    risk_level=str(item.get("riskLevel") or item.get("risk_level", "high")),
                    lifecycle_stage=str(item.get("lifecycleStage") or item.get("lifecycle_stage", "")),
                    url=str(item.get("url", "")),
                ))
            return result
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("[VCLUB] upcoming 请求异常: %s", e)
            return []

    async def fetch_leaderboard(
        self, project_token: str, top_n: int = 50
    ) -> list[WhalePosition]:
        """获取指定代币的大户持仓排名。

        需要 /api/public/leaderboard 接口（朋友需添加此路由）。
        """
        if not project_token:
            return []

        session = await self._ensure_session()
        try:
            resp = await session.get(
                f"{self.base_url}{self._leaderboard_path}",
                params={"project": project_token, "top": top_n},
            )
            if resp.status != 200:
                logger.warning("[VCLUB] leaderboard 请求失败: status=%d token=%s",
                               resp.status, project_token[:10])
                return []

            data = await resp.json()
            items = data.get("items") or []
            return [
                WhalePosition(
                    wallet=str(item.get("wallet", "")),
                    sum_spent_v_est=str(item.get("sum_spent_v_est", "0")),
                    sum_token_bought=str(item.get("sum_token_bought", "0")),
                    avg_cost_v=str(item.get("avg_cost_v", "0")),
                    breakeven_fdv_v=str(item.get("breakeven_fdv_v", "0")),
                    breakeven_fdv_usd=str(item.get("breakeven_fdv_usd")) if item.get("breakeven_fdv_usd") else None,
                )
                for item in items
            ]
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("[VCLUB] leaderboard 请求异常: %s", e)
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "VirtualsClubClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
