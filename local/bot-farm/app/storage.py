"""SQLite-хранилище управляемых ботов и их аудитории."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import aiosqlite

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bots.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    telegram_id INTEGER,
    username TEXT,
    reply_text TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audience (
    bot_id INTEGER NOT NULL REFERENCES bots(id),
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (bot_id, user_id)
);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


async def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def add_bot(token: str, telegram_id: int, username: str, reply_text: str) -> int:
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO bots (token, telegram_id, username, reply_text, active, added_at) "
            "VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(token) DO UPDATE SET telegram_id=excluded.telegram_id, "
            "username=excluded.username, active=1",
            (token, telegram_id, username, reply_text, _now()),
        )
        await db.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = await (await db.execute("SELECT id FROM bots WHERE token = ?", (token,))).fetchone()
        return row[0]


async def list_bots(active_only: bool = False) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM bots" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        rows = await db.execute_fetchall(q)
        return [dict(r) for r in rows]


async def get_bot_by_token(token: str) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM bots WHERE token = ?", (token,))).fetchone()
        return dict(row) if row else None


async def find_bot(ref: str) -> dict | None:
    """Найти бота по @username, username без @ или числовому id записи."""
    ref = ref.strip().lstrip("@")
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if ref.isdigit():
            row = await (await db.execute("SELECT * FROM bots WHERE id = ?", (int(ref),))).fetchone()
            if row:
                return dict(row)
        row = await (await db.execute("SELECT * FROM bots WHERE username = ?", (ref,))).fetchone()
        return dict(row) if row else None


async def set_reply_text(bot_id: int, text: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("UPDATE bots SET reply_text = ? WHERE id = ?", (text, bot_id))
        await db.commit()


async def get_reply_text(bot_id: int, fallback: str) -> str:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute("SELECT reply_text FROM bots WHERE id = ?", (bot_id,))).fetchone()
        text = (row[0] if row else "") or ""
        return text.strip() or fallback


async def set_active(bot_id: int, active: bool) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("UPDATE bots SET active = ? WHERE id = ?", (1 if active else 0, bot_id))
        await db.commit()


async def remove_bot(bot_id: int) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM audience WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        await db.commit()


async def record_audience(bot_id: int, user_id: int, username: str | None, full_name: str) -> None:
    now = _now()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT INTO audience (bot_id, user_id, username, full_name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(bot_id, user_id) DO UPDATE SET "
            "username=excluded.username, full_name=excluded.full_name, last_seen=excluded.last_seen",
            (bot_id, user_id, username, full_name, now, now),
        )
        await db.commit()


async def audience_count(bot_id: int) -> int:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM audience WHERE bot_id = ?", (bot_id,))).fetchone()
        return row[0] if row else 0


async def list_audience_ids(bot_id: int) -> list[int]:
    async with aiosqlite.connect(_DB_PATH) as db:
        rows = await db.execute_fetchall("SELECT user_id FROM audience WHERE bot_id = ?", (bot_id,))
        return [r[0] for r in rows]
