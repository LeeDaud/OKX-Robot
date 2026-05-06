"""
SQLite 持久化：交易记录的读写。
"""
import aiosqlite
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "trades.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash       TEXT UNIQUE,
    token_address TEXT NOT NULL,
    side          TEXT NOT NULL,
    strategy      TEXT DEFAULT '',
    amount_in     TEXT DEFAULT '0',
    amount_out    TEXT DEFAULT '0',
    filled_amount TEXT DEFAULT '0',
    cost_usd      REAL DEFAULT 0,
    pnl_usd       REAL DEFAULT 0,
    roi           REAL DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    is_open       INTEGER DEFAULT 0,
    our_tx_hash    TEXT DEFAULT '',
    our_tx_sent_at TEXT DEFAULT '',
    our_tx_stage   TEXT DEFAULT ''
)
"""


async def init_db(path: str = DB_PATH) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(CREATE_TABLE)
        await db.commit()
    logger.info("Database initialized: %s", path)


# ── 新增交易记录 ──────────────────────────────────────────────────

async def insert_buy(
    tx_hash: str,
    token_address: str,
    amount_in: int,
    amount_out: int,
    strategy: str = "",
    cost_usd: float = 0.0,
    filled_amount: str = "0",
    path: str = DB_PATH,
) -> None:
    """记录买入交易。"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO trades
               (tx_hash, token_address, side, strategy, amount_in, amount_out,
                filled_amount, cost_usd, status, created_at, is_open)
               VALUES (?, ?, 'buy', ?, ?, ?, ?, ?, 'success', ?, 1)""",
            (tx_hash, token_address.lower(), strategy,
             str(amount_in), str(amount_out),
             filled_amount, cost_usd, now),
        )
        await db.commit()


async def insert_sell(
    tx_hash: str,
    token_address: str,
    amount_sold: int,
    amount_received: int,
    strategy: str = "",
    cost_usd: float = 0.0,
    pnl_usd: float = 0.0,
    roi: float = 0.0,
    filled_amount: str = "0",
    path: str = DB_PATH,
) -> None:
    """记录卖出交易。"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO trades
               (tx_hash, token_address, side, strategy, amount_in, amount_out,
                filled_amount, cost_usd, pnl_usd, roi, status, created_at)
               VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, 'success', ?)""",
            (tx_hash, token_address.lower(), strategy,
             str(amount_sold), str(amount_received),
             filled_amount, cost_usd, pnl_usd, roi, now),
        )
        # 关闭对应的未平仓买入（FIFO: 最早的一笔）
        await db.execute(
            """UPDATE trades SET is_open=0, pnl_usd=?, roi=?
               WHERE id=(
                   SELECT id FROM trades
                   WHERE side='buy' AND token_address=? AND is_open=1 AND status='success'
                   ORDER BY created_at ASC LIMIT 1
               )""",
            (pnl_usd, roi, token_address.lower()),
        )
        await db.commit()


# ── 崩溃恢复 ──────────────────────────────────────────────────────

async def set_tx_pending(
    tx_hash: str,
    our_tx_hash: str,
    stage: str,
    path: str = DB_PATH,
) -> None:
    """发交易后立即写入 tx_hash 和阶段，用于 crash 恢复。"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE trades
               SET our_tx_hash=?, our_tx_sent_at=?, our_tx_stage=?
               WHERE tx_hash=?""",
            (our_tx_hash, now, stage, tx_hash),
        )
        await db.commit()


async def get_pending_trades(path: str = DB_PATH) -> list[dict]:
    """查询所有已发出交易但尚未确认的记录。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM trades
               WHERE our_tx_hash != '' AND status='pending'
               ORDER BY created_at ASC"""
        ) as cur:
            return [dict(row) async for row in cur]


async def confirm_tx(
    tx_hash: str,
    status: str,
    filled_amount_raw: str = "0",
    path: str = DB_PATH,
) -> None:
    """确认 pending 交易：回填成交数据并更新状态。"""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE trades
               SET status=?, filled_amount=?
               WHERE tx_hash=?""",
            (status, filled_amount_raw, tx_hash),
        )
        await db.commit()


# ── 持仓查询 ──────────────────────────────────────────────────────

async def get_open_positions(path: str = DB_PATH) -> list[dict]:
    """返回所有未平仓的买入记录。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM trades
               WHERE side='buy' AND is_open=1 AND status='success'
               ORDER BY created_at ASC"""
        ) as cur:
            return [dict(row) async for row in cur]


async def get_open_position_by_token(token: str, path: str = DB_PATH) -> Optional[dict]:
    """按 token 找最早一笔未平仓买入。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM trades
               WHERE side='buy' AND token_address=? AND is_open=1 AND status='success'
               ORDER BY created_at ASC LIMIT 1""",
            (token.lower(),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── 统计与查询 ────────────────────────────────────────────────────

async def get_all_trades(limit: int = 100, offset: int = 0, path: str = DB_PATH) -> list[dict]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_trades_by_strategy(strategy: str, path: str = DB_PATH) -> list[dict]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trades WHERE strategy=? ORDER BY created_at DESC",
            (strategy,),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_today_pnl(path: str = DB_PATH) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE status='success' AND created_at LIKE ?",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0


async def get_today_stats(path: str = DB_PATH) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                      COALESCE(SUM(pnl_usd), 0)
               FROM trades WHERE created_at LIKE ?""",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return {
                "total": row[0] or 0,
                "success": row[1] or 0,
                "pnl": float(row[2] or 0),
            }


async def get_all_stats(path: str = DB_PATH) -> dict:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT side, cost_usd, pnl_usd FROM trades WHERE status='success'"
        ) as cur:
            rows = [dict(row) async for row in cur]

    total_invested = sum(r["cost_usd"] for r in rows if r["side"] == "buy")
    realized_pnl = sum(r["pnl_usd"] for r in rows)

    return {
        "total_trades": len(rows),
        "total_invested": total_invested,
        "realized_pnl": realized_pnl,
    }
