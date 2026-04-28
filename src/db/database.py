"""
SQLite 持久化：跟单记录的读写。
"""
import uuid
import aiosqlite
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "copytrade.db"

# token 地址 → decimals 映射（用于 raw amount → USD 换算）
STABLE_DECIMALS_MAP = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": 6,   # USDT
    "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b": 18,  # VIRTUAL
}


def _amount_to_usd(raw: str | int, token_addr: str) -> float:
    """根据 token_in 地址将 raw amount 换算为 USD 价值。"""
    decimals = STABLE_DECIMALS_MAP.get(token_addr.lower(), 6)
    return int(raw) / 10 ** decimals

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS copy_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_tx    TEXT UNIQUE NOT NULL,
    source_addr  TEXT NOT NULL,
    token_in     TEXT,
    token_out    TEXT,
    amount_in    TEXT,
    amount_out   REAL DEFAULT 0,
    our_tx       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    side         TEXT DEFAULT 'buy',
    position_id  TEXT,
    entry_price  REAL DEFAULT 0,
    exit_price   REAL DEFAULT 0,
    roi_pct      REAL DEFAULT 0,
    pnl          REAL DEFAULT 0,
    created_at   TEXT NOT NULL
)
"""

MIGRATE_COLUMNS = [
    ("amount_out",      "REAL DEFAULT 0"),
    ("side",            "TEXT DEFAULT 'buy'"),
    ("position_id",     "TEXT"),
    ("entry_price",     "REAL DEFAULT 0"),
    ("exit_price",      "REAL DEFAULT 0"),
    ("roi_pct",         "REAL DEFAULT 0"),
    ("filled_amount",   "TEXT"),
    ("filled_cost_usd", "REAL DEFAULT 0"),
    ("our_tx_hash",     "TEXT DEFAULT ''"),
    ("our_tx_sent_at",  "TEXT DEFAULT ''"),
    ("our_tx_stage",    "TEXT DEFAULT ''"),
]


async def init_db(path: str = DB_PATH) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(CREATE_TABLE)
        # 兼容旧库：按需补列
        async with db.execute("PRAGMA table_info(copy_trades)") as cur:
            existing = {row[1] async for row in cur}
        for col, col_def in MIGRATE_COLUMNS:
            if col not in existing:
                await db.execute(f"ALTER TABLE copy_trades ADD COLUMN {col} {col_def}")
        await db.commit()


async def insert_trade(
    source_tx: str,
    source_addr: str,
    token_in: str,
    token_out: str,
    amount_in: int,
    side: str = "buy",
    position_id: str | None = None,
    entry_price: float = 0.0,
    amount_out: float = 0.0,
    path: str = DB_PATH,
) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO copy_trades
               (source_tx, source_addr, token_in, token_out, amount_in,
                amount_out, side, position_id, entry_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_tx, source_addr, token_in, token_out, str(amount_in),
             amount_out, side, position_id,
             entry_price, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def update_trade(
    source_tx: str,
    our_tx: Optional[str],
    status: str,
    pnl: float = 0.0,
    path: str = DB_PATH,
) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE copy_trades SET our_tx=?, status=?, pnl=? WHERE source_tx=?",
            (our_tx, status, pnl, source_tx),
        )
        await db.commit()


async def update_trade_fill(
    source_tx: str,
    our_tx: str,
    status: str,
    filled_amount_raw: str,
    filled_cost_usd: float,
    path: str = DB_PATH,
) -> None:
    """回填机器人实际成交数据（覆盖目标报价估算）。"""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE copy_trades
               SET our_tx=?, status=?, filled_amount=?, filled_cost_usd=?
               WHERE source_tx=?""",
            (our_tx, status, filled_amount_raw, filled_cost_usd, source_tx),
        )
        await db.commit()


async def set_tx_pending(
    source_tx: str,
    our_tx_hash: str,
    stage: str,
    path: str = DB_PATH,
) -> None:
    """发交易后立即写入 tx_hash 和阶段，用于 crash 恢复。"""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE copy_trades
               SET our_tx_hash=?, our_tx_sent_at=?, our_tx_stage=?
               WHERE source_tx=?""",
            (our_tx_hash, datetime.now(timezone.utc).isoformat(), stage, source_tx),
        )
        await db.commit()


async def get_pending_trades(path: str = DB_PATH) -> list[dict]:
    """查询所有已发出跟单交易但尚未确认的记录。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copy_trades
               WHERE our_tx_hash != '' AND status='pending'
               ORDER BY created_at ASC"""
        ) as cur:
            return [dict(row) async for row in cur]


async def confirm_tx(
    source_tx: str,
    status: str,
    filled_amount_raw: str = "0",
    filled_cost_usd: float = 0.0,
    path: str = DB_PATH,
) -> None:
    """确认 pending 交易：回填成交数据并更新状态。"""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE copy_trades
               SET status=?, filled_amount=?, filled_cost_usd=?
               WHERE source_tx=?""",
            (status, filled_amount_raw, filled_cost_usd, source_tx),
        )
        await db.commit()


async def close_position(
    position_id: str,
    exit_price: float,
    roi_pct: float,
    pnl: float,
    path: str = DB_PATH,
) -> None:
    """平仓：更新买入记录的出场价、ROI、PnL。"""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE copy_trades
               SET exit_price=?, roi_pct=?, pnl=?
               WHERE position_id=? AND side='buy'""",
            (exit_price, roi_pct, pnl, position_id),
        )
        await db.commit()


async def get_open_positions(path: str = DB_PATH) -> list[dict]:
    """返回所有未平仓的买入记录（exit_price=0 且 status=success/dry_run）。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copy_trades
               WHERE side='buy' AND exit_price=0
               AND status IN ('success', 'dry_run')
               ORDER BY created_at ASC"""
        ) as cur:
            return [dict(row) async for row in cur]


async def get_open_position_by_token(token: str, path: str = DB_PATH) -> Optional[dict]:
    """按 token_out 找最近一笔未平仓买入。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copy_trades
               WHERE side='buy' AND token_out=? AND exit_price=0
               AND status IN ('success', 'dry_run')
               ORDER BY created_at DESC LIMIT 1""",
            (token.lower(),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_trades(limit: int = 100, offset: int = 0, path: str = DB_PATH) -> list[dict]:
    """查询交易记录列表（按时间倒序）。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copy_trades
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_all_stats(path: str = DB_PATH) -> dict:
    """总实际盈亏、总投入金额（逐行按 token_in 换算，兼容多 decimal 代币）。"""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT side, token_in, amount_in, pnl
               FROM copy_trades WHERE status IN ('success', 'dry_run')"""
        ) as cur:
            rows = [dict(row) async for row in cur]

    total_invested = 0.0
    realized_pnl = 0.0
    for row in rows:
        if row["side"] == "buy":
            total_invested += _amount_to_usd(row["amount_in"], row["token_in"])
        realized_pnl += float(row["pnl"] or 0)

    return {
        "total_trades": len(rows),
        "total_invested": total_invested,
        "realized_pnl": realized_pnl,
    }


async def get_today_stats(path: str = DB_PATH) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            """SELECT COUNT(*), SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                      COALESCE(SUM(pnl), 0)
               FROM copy_trades WHERE created_at LIKE ?""",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return {
                "total": row[0] or 0,
                "success": row[1] or 0,
                "pnl": float(row[2] or 0),
            }


async def get_today_pnl(path: str = DB_PATH) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM copy_trades WHERE status='success' AND created_at LIKE ?",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0
