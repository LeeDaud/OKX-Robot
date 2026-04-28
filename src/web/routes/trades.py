"""
交易数据 API。
"""
import logging

from fastapi import APIRouter, Query
from src.db.database import (
    get_all_trades, get_open_positions, get_today_stats,
    get_all_stats, get_today_pnl,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trades"])


@router.get("/trades")
async def list_trades(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """返回交易记录列表。"""
    trades = await get_all_trades(limit=limit, offset=offset)
    return {"trades": trades}


@router.get("/trades/stats")
async def trade_stats():
    """返回交易统计。"""
    today_stats = await get_today_stats()
    all_stats = await get_all_stats()
    today_pnl = await get_today_pnl()
    return {
        "today": today_stats,
        "all": all_stats,
        "today_pnl": today_pnl,
    }


@router.get("/positions")
async def positions():
    """返回当前持仓。"""
    open_positions = await get_open_positions()
    return {"positions": open_positions}
