"""SQLite persistence (aiosqlite).

The panel is the source of truth. Server-side Xray/MTProto configs are always a
pure function of the rows here, regenerated and pushed on every change.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiosqlite

from .config import settings
from . import keys

_db: Optional[aiosqlite.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id               INTEGER PRIMARY KEY,
    username            TEXT,
    uuid               TEXT NOT NULL,
    short_id           TEXT NOT NULL,
    proto_password     TEXT NOT NULL,
    sub_token          TEXT NOT NULL UNIQUE,
    mtproto_secret     TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'new',   -- new|trial|active|expired|banned
    plan_id            INTEGER,
    expires_at         INTEGER,                       -- unix seconds
    traffic_limit_bytes INTEGER DEFAULT 0,            -- 0 = unlimited
    traffic_used_bytes  INTEGER NOT NULL DEFAULT 0,
    device_limit       INTEGER NOT NULL DEFAULT 3,
    balance            INTEGER NOT NULL DEFAULT 0,     -- internal units (1 unit = 1 Star by default)
    trial_used         INTEGER NOT NULL DEFAULT 0,
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS servers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,
    host               TEXT NOT NULL,
    ssh_user           TEXT NOT NULL DEFAULT 'root',
    ssh_password       TEXT NOT NULL,
    ssh_port           INTEGER NOT NULL DEFAULT 22,
    country            TEXT DEFAULT '',
    xray_port          INTEGER NOT NULL DEFAULT 443,
    mtproto_port       INTEGER NOT NULL DEFAULT 8443,
    reality_dest       TEXT NOT NULL,
    reality_sni        TEXT NOT NULL,
    reality_public_key TEXT DEFAULT '',
    reality_private_key TEXT DEFAULT '',
    mtproto_faketls_domain TEXT NOT NULL DEFAULT 'www.microsoft.com',
    enable_xray        INTEGER NOT NULL DEFAULT 1,
    enable_mtproto     INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'new',    -- new|provisioning|active|disabled|error
    last_sync_at       INTEGER,
    last_error         TEXT DEFAULT '',
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,
    price              INTEGER NOT NULL,               -- internal units
    duration_days      INTEGER NOT NULL,
    traffic_gb         INTEGER NOT NULL DEFAULT 0,     -- 0 = unlimited
    device_limit       INTEGER NOT NULL DEFAULT 3,
    sort               INTEGER NOT NULL DEFAULT 0,
    active             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS txns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL,
    kind               TEXT NOT NULL,                  -- topup|purchase|trial|admin_grant|refund
    amount             INTEGER NOT NULL DEFAULT 0,     -- + for credit, - for debit
    plan_id            INTEGER,
    method             TEXT NOT NULL DEFAULT 'balance',-- stars|crypto|balance|admin
    status             TEXT NOT NULL DEFAULT 'done',   -- pending|done|failed
    payload            TEXT DEFAULT '',
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key                TEXT PRIMARY KEY,
    value              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sponsor_channels (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT NOT NULL,
    username           TEXT NOT NULL DEFAULT '',   -- @channel, display only
    ad_tag             TEXT NOT NULL,              -- 32 hex chars from @MTProxybot
    active             INTEGER NOT NULL DEFAULT 0, -- at most one row = 1
    created_at         INTEGER NOT NULL
);
"""

DEFAULT_PLANS = [
    # name, price(units), days, traffic_gb, devices, sort
    ("1 месяц", 149, 30, 0, 3, 10),
    ("3 месяца", 399, 90, 0, 3, 20),
    ("6 месяцев", 699, 180, 0, 5, 30),
    ("1 год", 1199, 365, 0, 5, 40),
]

TOPUP_PACKS = [100, 250, 500, 1000]  # internal units purchasable via Stars


def now() -> int:
    return int(time.time())


def db() -> aiosqlite.Connection:
    assert _db is not None, "db.init() not called"
    return _db


async def init() -> None:
    global _db
    _db = await aiosqlite.connect(settings.db_path)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.commit()
    cur = await _db.execute("SELECT COUNT(*) c FROM plans")
    if (await cur.fetchone())["c"] == 0:
        await _db.executemany(
            "INSERT INTO plans (name,price,duration_days,traffic_gb,device_limit,sort) "
            "VALUES (?,?,?,?,?,?)",
            DEFAULT_PLANS,
        )
        await _db.commit()


async def close() -> None:
    if _db is not None:
        await _db.close()


# --- users -----------------------------------------------------------------

async def get_user(tg_id: int) -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    return await cur.fetchone()


async def get_user_by_token(token: str) -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE sub_token=?", (token,))
    return await cur.fetchone()


async def ensure_user(tg_id: int, username: str | None) -> aiosqlite.Row:
    row = await get_user(tg_id)
    if row:
        if username and username != row["username"]:
            await db().execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
            await db().commit()
        return row
    await db().execute(
        """INSERT INTO users (tg_id, username, uuid, short_id, proto_password,
             sub_token, mtproto_secret, status, device_limit, created_at)
           VALUES (?,?,?,?,?,?,?, 'new', ?, ?)""",
        (
            tg_id, username, keys.new_uuid(), keys.new_short_id(),
            keys.new_proto_password(), keys.new_sub_token(), keys.new_mtproto_secret(),
            settings.device_limit, now(),
        ),
    )
    await db().commit()
    return await get_user(tg_id)  # type: ignore[return-value]


async def rotate_sub_token(tg_id: int) -> str:
    tok = keys.new_sub_token()
    await db().execute("UPDATE users SET sub_token=? WHERE tg_id=?", (tok, tg_id))
    await db().commit()
    return tok


async def set_user(tg_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await db().execute(f"UPDATE users SET {cols} WHERE tg_id=?", (*fields.values(), tg_id))
    await db().commit()


async def active_users() -> list[aiosqlite.Row]:
    """Users whose keys must be present in server configs right now."""
    cur = await db().execute(
        "SELECT * FROM users WHERE status IN ('trial','active') "
        "AND (expires_at IS NULL OR expires_at > ?) ORDER BY tg_id",
        (now(),),
    )
    return list(await cur.fetchall())


async def all_users(limit: int = 100, offset: int = 0) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return list(await cur.fetchall())


async def expire_overdue() -> list[int]:
    cur = await db().execute(
        "SELECT tg_id FROM users WHERE status IN ('trial','active') "
        "AND expires_at IS NOT NULL AND expires_at <= ?",
        (now(),),
    )
    ids = [r["tg_id"] for r in await cur.fetchall()]
    if ids:
        qs = ",".join("?" * len(ids))
        await db().execute(f"UPDATE users SET status='expired' WHERE tg_id IN ({qs})", ids)
        await db().commit()
    return ids


async def grant_time(tg_id: int, days: int, *, plan_id: int | None = None,
                     traffic_gb: int = 0, device_limit: int | None = None,
                     status: str = "active") -> None:
    row = await get_user(tg_id)
    base = max(row["expires_at"] or 0, now()) if row else now()
    new_exp = base + days * 86400
    fields: dict[str, Any] = {
        "status": status,
        "expires_at": new_exp,
        "traffic_limit_bytes": traffic_gb * 1024**3,
        "traffic_used_bytes": 0,
    }
    if plan_id is not None:
        fields["plan_id"] = plan_id
    if device_limit is not None:
        fields["device_limit"] = device_limit
    await set_user(tg_id, **fields)


async def start_trial(tg_id: int) -> None:
    await grant_time(
        tg_id, settings.trial_days,
        traffic_gb=settings.trial_traffic_gb,
        device_limit=settings.device_limit,
        status="trial",
    )
    await set_user(tg_id, trial_used=1)
    await add_txn(tg_id, "trial", 0, method="admin")


# --- plans ---------------------------------------------------------------

async def plans(active_only: bool = True) -> list[aiosqlite.Row]:
    q = "SELECT * FROM plans"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY sort, price"
    cur = await db().execute(q)
    return list(await cur.fetchall())


async def get_plan(plan_id: int) -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM plans WHERE id=?", (plan_id,))
    return await cur.fetchone()


async def upsert_plan(plan_id: int | None, **f: Any) -> None:
    if plan_id:
        cols = ", ".join(f"{k}=?" for k in f)
        await db().execute(f"UPDATE plans SET {cols} WHERE id=?", (*f.values(), plan_id))
    else:
        cols = ", ".join(f.keys())
        qs = ", ".join("?" * len(f))
        await db().execute(f"INSERT INTO plans ({cols}) VALUES ({qs})", tuple(f.values()))
    await db().commit()


# --- servers ------------------------------------------------------------

async def add_server(name: str, host: str, ssh_user: str, ssh_password: str,
                     ssh_port: int = 22, **extra: Any) -> int:
    fields = {
        "name": name, "host": host, "ssh_user": ssh_user,
        "ssh_password": ssh_password, "ssh_port": ssh_port,
        "reality_dest": extra.get("reality_dest", settings.reality_dest),
        "reality_sni": extra.get("reality_sni", settings.reality_sni),
        "xray_port": extra.get("xray_port", settings.xray_port),
        "mtproto_port": extra.get("mtproto_port", settings.mtproto_port),
        "mtproto_faketls_domain": extra.get("mtproto_faketls_domain", settings.reality_sni),
        "country": extra.get("country", ""),
        "created_at": now(),
    }
    cols = ", ".join(fields)
    qs = ", ".join("?" * len(fields))
    cur = await db().execute(f"INSERT INTO servers ({cols}) VALUES ({qs})", tuple(fields.values()))
    await db().commit()
    return cur.lastrowid  # type: ignore[return-value]


async def get_server(sid: int) -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM servers WHERE id=?", (sid,))
    return await cur.fetchone()


async def set_server(sid: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await db().execute(f"UPDATE servers SET {cols} WHERE id=?", (*fields.values(), sid))
    await db().commit()


async def servers(status: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM servers"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY id"
    cur = await db().execute(q, args)
    return list(await cur.fetchall())


async def active_servers() -> list[aiosqlite.Row]:
    return await servers("active")


async def delete_server(sid: int) -> None:
    await db().execute("DELETE FROM servers WHERE id=?", (sid,))
    await db().commit()


# --- txns / balance ---------------------------------------------------

async def add_txn(user_id: int, kind: str, amount: int, *, plan_id: int | None = None,
                  method: str = "balance", status: str = "done", payload: str = "") -> int:
    cur = await db().execute(
        "INSERT INTO txns (user_id,kind,amount,plan_id,method,status,payload,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_id, kind, amount, plan_id, method, status, payload, now()),
    )
    await db().commit()
    return cur.lastrowid  # type: ignore[return-value]


async def add_balance(tg_id: int, amount: int, *, method: str = "stars", payload: str = "") -> None:
    await db().execute("UPDATE users SET balance = balance + ? WHERE tg_id=?", (amount, tg_id))
    await db().commit()
    await add_txn(tg_id, "topup" if amount > 0 else "refund", amount, method=method, payload=payload)


async def spend_balance(tg_id: int, amount: int, plan_id: int | None = None) -> bool:
    row = await get_user(tg_id)
    if not row or row["balance"] < amount:
        return False
    await db().execute("UPDATE users SET balance = balance - ? WHERE tg_id=?", (amount, tg_id))
    await db().commit()
    await add_txn(tg_id, "purchase", -amount, plan_id=plan_id, method="balance")
    return True


# --- settings kv ----------------------------------------------------

async def get_setting(key: str, default: str = "") -> str:
    cur = await db().execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await db().execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db().commit()


# --- sponsor channels (MTProto promoted channel) ----------------------

async def sponsor_channels() -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM sponsor_channels ORDER BY id")
    return list(await cur.fetchall())


async def get_sponsor_channel(cid: int) -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM sponsor_channels WHERE id=?", (cid,))
    return await cur.fetchone()


async def add_sponsor_channel(title: str, username: str, ad_tag: str) -> int:
    cur = await db().execute(
        "INSERT INTO sponsor_channels (title, username, ad_tag, active, created_at) "
        "VALUES (?,?,?,0,?)",
        (title, username, ad_tag, now()),
    )
    await db().commit()
    return cur.lastrowid  # type: ignore[return-value]


async def delete_sponsor_channel(cid: int) -> None:
    await db().execute("DELETE FROM sponsor_channels WHERE id=?", (cid,))
    await db().commit()


async def set_active_sponsor_channel(cid: int | None) -> None:
    """Make ``cid`` the only active sponsor channel, or disable sponsorship (None)."""
    await db().execute("UPDATE sponsor_channels SET active=0")
    if cid is not None:
        await db().execute("UPDATE sponsor_channels SET active=1 WHERE id=?", (cid,))
    await db().commit()


async def active_sponsor_channel() -> Optional[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM sponsor_channels WHERE active=1 LIMIT 1")
    return await cur.fetchone()


async def active_ad_tag() -> str:
    row = await active_sponsor_channel()
    return row["ad_tag"] if row else ""


async def mtproto_house_secret() -> str:
    """Stable public MTProto secret for the sponsored proxy.

    Generated once and kept in ``settings`` so it survives restarts and is the
    same across every node — this is the secret registered with @MTProxybot and
    handed out in the public ``tg://proxy`` link.
    """
    s = await get_setting("mtproto_house_secret")
    if not s:
        s = keys.new_mtproto_secret()
        await set_setting("mtproto_house_secret", s)
    return s


async def stats() -> dict[str, Any]:
    d = db()
    out: dict[str, Any] = {}
    for label, q in {
        "users_total": "SELECT COUNT(*) c FROM users",
        "users_trial": "SELECT COUNT(*) c FROM users WHERE status='trial'",
        "users_active": "SELECT COUNT(*) c FROM users WHERE status='active'",
        "users_expired": "SELECT COUNT(*) c FROM users WHERE status='expired'",
        "servers_active": "SELECT COUNT(*) c FROM servers WHERE status='active'",
        "revenue_units": "SELECT COALESCE(SUM(amount),0) c FROM txns WHERE kind='topup'",
    }.items():
        cur = await d.execute(q)
        out[label] = (await cur.fetchone())["c"]
    return out
