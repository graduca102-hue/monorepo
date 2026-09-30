from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import aiosqlite

from .security import SecretCipher


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InsufficientFunds(RuntimeError):
    pass


def serialized_write(method):
    @wraps(method)
    async def wrapped(self: "Database", *args, **kwargs):
        async with self._write_lock:
            return await method(self, *args, **kwargs)

    return wrapped


class Database:
    DEFAULT_SETTINGS = {
        "usdt_rub_rate": "1.00",
        "market_markup_percent": "25",
        "proxy_markup_percent": "25",
        "referral_percent": "5",
        "market_min_price_usd": "0.80",
        "support_username": "",
        "public_base_url": "",
    }

    def __init__(self, path: Path, cipher: SecretCipher) -> None:
        self.path = path
        self.cipher = cipher
        self.conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                full_name TEXT NOT NULL DEFAULT '',
                balance_kopecks INTEGER NOT NULL DEFAULT 0 CHECK(balance_kopecks >= 0),
                spent_kopecks INTEGER NOT NULL DEFAULT 0 CHECK(spent_kopecks >= 0),
                purchased_count INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                order_number TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit_price_kopecks INTEGER NOT NULL,
                total_kopecks INTEGER NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                delivery TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id DESC);

            CREATE TABLE IF NOT EXISTS payments (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount_kopecks INTEGER NOT NULL,
                status TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'topup',
                purchase_payload TEXT NOT NULL DEFAULT '{}',
                provider_uuid TEXT NOT NULL DEFAULT '',
                payment_url TEXT NOT NULL DEFAULT '',
                credited_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_kopecks INTEGER NOT NULL,
                reason TEXT NOT NULL,
                reference TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                amount_kopecks INTEGER NOT NULL CHECK(amount_kopecks > 0),
                uses_left INTEGER NOT NULL CHECK(uses_left >= 0),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_uses (
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY(code, user_id),
                FOREIGN KEY(code) REFERENCES promocodes(code),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS product_overrides (
                product_id INTEGER PRIMARY KEY,
                custom_title TEXT NOT NULL DEFAULT '',
                hidden INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_overrides (
                service_code TEXT PRIMARY KEY,
                custom_title TEXT NOT NULL DEFAULT '',
                hidden INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = utcnow()
        for key, value in self.DEFAULT_SETTINGS.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings(key,value,is_secret,updated_at) VALUES(?,?,0,?)",
                (key, value, now),
            )
        await self._migrate()
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _migrate(self) -> None:
        cur = await self._db().execute("PRAGMA table_info(payments)")
        columns = {str(row["name"]) for row in await cur.fetchall()}
        if "purpose" not in columns:
            await self._db().execute("ALTER TABLE payments ADD COLUMN purpose TEXT NOT NULL DEFAULT 'topup'")
        if "purchase_payload" not in columns:
            await self._db().execute("ALTER TABLE payments ADD COLUMN purchase_payload TEXT NOT NULL DEFAULT '{}'")

        cur = await self._db().execute("PRAGMA table_info(orders)")
        order_columns = {str(row["name"]) for row in await cur.fetchall()}
        if "order_number" not in order_columns:
            await self._db().execute("ALTER TABLE orders ADD COLUMN order_number TEXT NOT NULL DEFAULT ''")
            await self._backfill_order_numbers()

        await self._db().execute(
            """CREATE TABLE IF NOT EXISTS proxy_settings (
                user_id INTEGER PRIMARY KEY,
                settings_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )"""
        )

        cur = await self._db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='residential_entitlements'"
        )
        had_entitlements = await cur.fetchone() is not None
        await self._db().execute(
            """CREATE TABLE IF NOT EXISTS residential_entitlements (
                user_id INTEGER PRIMARY KEY,
                gb REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        if not had_entitlements:
            await self._backfill_residential_entitlements()

        await self._db().execute(
            """CREATE TABLE IF NOT EXISTS residential_deliveries (
                user_id INTEGER PRIMARY KEY,
                lines_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )"""
        )
        cur = await self._db().execute("PRAGMA table_info(residential_deliveries)")
        rd_cols = {str(row["name"]) for row in await cur.fetchall()}
        if "last_check_at" not in rd_cols:
            await self._db().execute(
                "ALTER TABLE residential_deliveries ADD COLUMN last_check_at TEXT"
            )
        if "last_notice_at" not in rd_cols:
            await self._db().execute(
                "ALTER TABLE residential_deliveries ADD COLUMN last_notice_at TEXT"
            )

    async def _backfill_residential_entitlements(self) -> None:
        """Seed per-customer residential GB from historical proxy-service orders.

        The old flow credited every residential purchase into one shared API
        pool and never allocated it to a customer; this records who paid for
        how much so the new management menu can hand each buyer their own GB."""
        now = utcnow()
        await self._db().execute(
            """INSERT INTO residential_entitlements(user_id, gb, updated_at)
               SELECT user_id, ROUND(SUM(quantity), 2), ?
               FROM orders
               WHERE kind = 'proxy_service'
                 AND status IN ('completed', 'delivery_pending')
                 AND lower(title) LIKE 'residential%'
               GROUP BY user_id
               ON CONFLICT(user_id) DO NOTHING""",
            (now,),
        )

    async def _backfill_order_numbers(self) -> None:
        """Populate ``order_number`` for orders that predate the column by
        re-reading the stored SOUS API response."""
        from .clients import external_order_number

        cur = await self._db().execute(
            "SELECT id, external_id, response_json FROM orders WHERE order_number = ''"
        )
        rows = await cur.fetchall()
        for row in rows:
            try:
                data = json.loads(row["response_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                data = {}
            number = external_order_number(data) if isinstance(data, dict) else ""
            if not number:
                number = str(row["external_id"] or "")
            if number:
                await self._db().execute(
                    "UPDATE orders SET order_number=? WHERE id=?", (number, row["id"])
                )

    def _db(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database is not initialized")
        return self.conn

    async def seed_config(
        self,
        *,
        sous_api_key: str = "",
        heleket_merchant_id: str = "",
        heleket_api_key: str = "",
        public_base_url: str = "",
        support_username: str = "",
    ) -> None:
        seeds = {
            "sous_api_key": (sous_api_key, True),
            "heleket_merchant_id": (heleket_merchant_id, True),
            "heleket_api_key": (heleket_api_key, True),
            "public_base_url": (public_base_url, False),
            "support_username": (support_username, False),
        }
        for key, (value, secret) in seeds.items():
            if value and not await self.get_setting(key, secret=secret):
                await self.set_setting(key, value, secret=secret)

    async def get_setting(self, key: str, *, secret: bool = False) -> str:
        cur = await self._db().execute("SELECT value,is_secret FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        if not row:
            return ""
        value = str(row["value"])
        if row["is_secret"]:
            return self.cipher.decrypt(value)
        return value

    @serialized_write
    async def set_setting(self, key: str, value: str, *, secret: bool = False) -> None:
        stored = self.cipher.encrypt(value) if secret else value
        await self._db().execute(
            """INSERT INTO settings(key,value,is_secret,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                 is_secret=excluded.is_secret, updated_at=excluded.updated_at""",
            (key, stored, int(secret), utcnow()),
        )
        await self._db().commit()

    async def pricing(self, kind: str) -> tuple[float, float]:
        rate = float(await self.get_setting("usdt_rub_rate") or "1")
        markup_key = "market_markup_percent" if kind == "market" else "proxy_markup_percent"
        markup = float(await self.get_setting(markup_key) or "0")
        return rate, markup

    async def market_min_price_kopecks(self) -> int:
        """Minimum final price (in kopecks) a market product must reach to stay
        visible in the catalog. Configurable via the ``market_min_price_usd``
        setting from the admin panel; ``0`` disables the filter. Proxy sections
        are never affected."""
        raw = await self.get_setting("market_min_price_usd") or "0"
        try:
            return max(0, int(round(float(raw.replace(",", ".")) * 100)))
        except (TypeError, ValueError):
            return 0

    @serialized_write
    async def upsert_user(
        self,
        user_id: int,
        username: str,
        full_name: str,
        referrer_id: int | None = None,
    ) -> bool:
        db = self._db()
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (user_id,))
        exists = await cur.fetchone()
        now = utcnow()
        valid_referrer = referrer_id if referrer_id and referrer_id != user_id else None
        await db.execute(
            """INSERT INTO users(id,username,full_name,referrer_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username,
                 full_name=excluded.full_name, updated_at=excluded.updated_at""",
            (user_id, username or "", full_name or "", valid_referrer, now, now),
        )
        await db.commit()
        return exists is None

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        cur = await self._db().execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def referrals(self, user_id: int) -> tuple[int, int]:
        cur = await self._db().execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(balance_kopecks),0) AS balances FROM users WHERE referrer_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0, 0
        return int(row["count"]), int(row["balances"])

    @serialized_write
    async def adjust_balance(self, user_id: int, amount_kopecks: int, reason: str, reference: str = "") -> int:
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT balance_kopecks FROM users WHERE id=?", (user_id,))
            row = await cur.fetchone()
            if not row:
                raise ValueError("Пользователь не найден")
            new_balance = int(row["balance_kopecks"]) + amount_kopecks
            if new_balance < 0:
                raise InsufficientFunds("Недостаточно средств")
            now = utcnow()
            await db.execute(
                "UPDATE users SET balance_kopecks=?,updated_at=? WHERE id=?",
                (new_balance, now, user_id),
            )
            await db.execute(
                "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, amount_kopecks, reason, reference, now),
            )
            await db.commit()
            return new_balance
        except Exception:
            await db.rollback()
            raise

    @serialized_write
    async def reserve_order(
        self,
        *,
        request_id: str,
        user_id: int,
        kind: str,
        title: str,
        quantity: float,
        unit_price_kopecks: int,
        total_kopecks: int,
        request: dict[str, Any],
    ) -> int:
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT balance_kopecks FROM users WHERE id=?", (user_id,))
            row = await cur.fetchone()
            if not row or int(row["balance_kopecks"]) < total_kopecks:
                raise InsufficientFunds("Недостаточно средств")
            now = utcnow()
            cur = await db.execute(
                """INSERT INTO orders(request_id,user_id,kind,title,quantity,
                   unit_price_kopecks,total_kopecks,status,request_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    user_id,
                    kind,
                    title,
                    quantity,
                    unit_price_kopecks,
                    total_kopecks,
                    "processing",
                    json.dumps(request, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await db.execute(
                "UPDATE users SET balance_kopecks=balance_kopecks-?,updated_at=? WHERE id=?",
                (total_kopecks, now, user_id),
            )
            await db.execute(
                "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, -total_kopecks, "purchase_reserve", request_id, now),
            )
            await db.commit()
            if cur.lastrowid is None:
                raise RuntimeError("Database did not return an order ID")
            return int(cur.lastrowid)
        except Exception:
            await db.rollback()
            raise

    @serialized_write
    async def complete_order(
        self,
        order_id: int,
        *,
        external_id: str,
        response: dict[str, Any],
        delivery: str,
        status: str = "completed",
        order_number: str = "",
    ) -> None:
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cur.fetchone()
            if not order or order["status"] not in {"processing", "delivery_pending", "review"}:
                await db.rollback()
                return
            now = utcnow()
            await db.execute(
                """UPDATE orders SET status=?,external_id=?,
                   order_number=COALESCE(NULLIF(?, ''), order_number),
                   response_json=?,delivery=?,updated_at=?
                   WHERE id=?""",
                (
                    status,
                    external_id,
                    order_number,
                    json.dumps(response, ensure_ascii=False),
                    delivery,
                    now,
                    order_id,
                ),
            )
            if order["status"] == "processing":
                await db.execute(
                    """UPDATE users SET spent_kopecks=spent_kopecks+?,
                       purchased_count=purchased_count+?,updated_at=? WHERE id=?""",
                    (order["total_kopecks"], int(float(order["quantity"])), now, order["user_id"]),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    @serialized_write
    async def fail_order(self, order_id: int, response: dict[str, Any], *, uncertain: bool = False) -> None:
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cur.fetchone()
            if not order or order["status"] != "processing":
                await db.rollback()
                return
            now = utcnow()
            status = "review" if uncertain else "failed"
            await db.execute(
                "UPDATE orders SET status=?,response_json=?,updated_at=? WHERE id=?",
                (status, json.dumps(response, ensure_ascii=False), now, order_id),
            )
            if not uncertain:
                await db.execute(
                    "UPDATE users SET balance_kopecks=balance_kopecks+?,updated_at=? WHERE id=?",
                    (order["total_kopecks"], now, order["user_id"]),
                )
                await db.execute(
                    "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                    (order["user_id"], order["total_kopecks"], "purchase_refund", order["request_id"], now),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        cur = await self._db().execute("SELECT * FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def user_orders(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        cur = await self._db().execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )
        return [dict(row) for row in await cur.fetchall()]

    @serialized_write
    async def create_payment(
        self,
        order_id: str,
        user_id: int,
        amount_kopecks: int,
        *,
        purpose: str = "topup",
        purchase_payload: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        await self._db().execute(
            """INSERT INTO payments(order_id,user_id,amount_kopecks,status,purpose,purchase_payload,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                order_id,
                user_id,
                amount_kopecks,
                "creating",
                purpose,
                json.dumps(purchase_payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self._db().commit()

    @serialized_write
    async def update_payment_invoice(
        self, order_id: str, status: str, provider_uuid: str = "", payment_url: str = ""
    ) -> None:
        await self._db().execute(
            "UPDATE payments SET status=?,provider_uuid=?,payment_url=?,updated_at=? WHERE order_id=?",
            (status, provider_uuid, payment_url, utcnow(), order_id),
        )
        await self._db().commit()

    async def get_payment(self, order_id: str) -> dict[str, Any] | None:
        cur = await self._db().execute("SELECT * FROM payments WHERE order_id=?", (order_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def pending_payments(
        self, *, max_age_days: int = 14, exclude_statuses: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in exclude_statuses)
        query = "SELECT * FROM payments WHERE credited_at IS NULL AND created_at>=?"
        params: list[Any] = [cutoff]
        if exclude_statuses:
            query += f" AND status NOT IN ({placeholders})"
            params.extend(exclude_statuses)
        query += " ORDER BY created_at ASC"
        cur = await self._db().execute(query, params)
        return [dict(row) for row in await cur.fetchall()]

    @serialized_write
    async def credit_payment(self, order_id: str, provider_status: str) -> dict[str, Any] | None:
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT * FROM payments WHERE order_id=?", (order_id,))
            payment = await cur.fetchone()
            if not payment:
                await db.rollback()
                return None
            if payment["credited_at"]:
                await db.rollback()
                return {"already_credited": True, **dict(payment)}
            if provider_status not in {"paid", "paid_over"}:
                await db.execute(
                    "UPDATE payments SET status=?,updated_at=? WHERE order_id=?",
                    (provider_status, utcnow(), order_id),
                )
                await db.commit()
                return None
            now = utcnow()
            amount = int(payment["amount_kopecks"])
            user_id = int(payment["user_id"])
            await db.execute(
                "UPDATE users SET balance_kopecks=balance_kopecks+?,updated_at=? WHERE id=?",
                (amount, now, user_id),
            )
            await db.execute(
                "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, amount, "heleket_topup", order_id, now),
            )
            await db.execute(
                "UPDATE payments SET status=?,credited_at=?,updated_at=? WHERE order_id=?",
                (provider_status, now, now, order_id),
            )
            referral_bonus = 0
            referrer_id = 0
            if payment["purpose"] == "topup":
                cur = await db.execute("SELECT referrer_id FROM users WHERE id=?", (user_id,))
                user_row = await cur.fetchone()
                referrer_id = int(user_row["referrer_id"]) if user_row and user_row["referrer_id"] else 0
                percent = float(await self._setting_in_transaction("referral_percent") or "0")
                if referrer_id:
                    referral_bonus = max(0, round(amount * percent / 100))
                    if referral_bonus:
                        await db.execute(
                            "UPDATE users SET balance_kopecks=balance_kopecks+?,updated_at=? WHERE id=?",
                            (referral_bonus, now, referrer_id),
                        )
                        await db.execute(
                            "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                            (referrer_id, referral_bonus, "referral_bonus", order_id, now),
                        )
            await db.commit()
            return {
                "already_credited": False,
                **dict(payment),
                "user_id": user_id,
                "amount_kopecks": amount,
                "referrer_id": referrer_id,
                "referral_bonus": referral_bonus,
                "status": provider_status,
                "credited_at": now,
            }
        except Exception:
            await db.rollback()
            raise

    async def _setting_in_transaction(self, key: str) -> str:
        cur = await self._db().execute("SELECT value,is_secret FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        if not row:
            return ""
        return self.cipher.decrypt(row["value"]) if row["is_secret"] else str(row["value"])

    @serialized_write
    async def redeem_promo(self, code: str, user_id: int) -> int:
        code = code.strip().upper()
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT * FROM promocodes WHERE code=?", (code,))
            promo = await cur.fetchone()
            if not promo or int(promo["uses_left"]) <= 0:
                raise ValueError("Промокод не найден или закончился")
            cur = await db.execute(
                "SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id)
            )
            if await cur.fetchone():
                raise ValueError("Вы уже активировали этот промокод")
            amount = int(promo["amount_kopecks"])
            now = utcnow()
            await db.execute("UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?", (code,))
            await db.execute(
                "INSERT INTO promo_uses(code,user_id,used_at) VALUES(?,?,?)", (code, user_id, now)
            )
            await db.execute(
                "UPDATE users SET balance_kopecks=balance_kopecks+?,updated_at=? WHERE id=?",
                (amount, now, user_id),
            )
            await db.execute(
                "INSERT INTO ledger(user_id,amount_kopecks,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, amount, "promocode", code, now),
            )
            await db.commit()
            return amount
        except Exception:
            await db.rollback()
            raise

    @serialized_write
    async def create_promo(self, code: str, amount_kopecks: int, uses: int) -> None:
        await self._db().execute(
            """INSERT INTO promocodes(code,amount_kopecks,uses_left,created_at) VALUES(?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET amount_kopecks=excluded.amount_kopecks,
                 uses_left=excluded.uses_left""",
            (code.strip().upper(), amount_kopecks, uses, utcnow()),
        )
        await self._db().commit()

    async def get_product_override(self, product_id: int) -> dict[str, Any]:
        cur = await self._db().execute(
            "SELECT custom_title,hidden FROM product_overrides WHERE product_id=?",
            (int(product_id),),
        )
        row = await cur.fetchone()
        if not row:
            return {"custom_title": "", "hidden": False}
        return {"custom_title": str(row["custom_title"] or ""), "hidden": bool(row["hidden"])}

    async def get_product_overrides(self, product_ids: list[int]) -> dict[int, dict[str, Any]]:
        ids = [int(pid) for pid in product_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        cur = await self._db().execute(
            f"SELECT product_id,custom_title,hidden FROM product_overrides WHERE product_id IN ({placeholders})",
            tuple(ids),
        )
        return {
            int(row["product_id"]): {
                "custom_title": str(row["custom_title"] or ""),
                "hidden": bool(row["hidden"]),
            }
            for row in await cur.fetchall()
        }

    @serialized_write
    async def set_product_title(self, product_id: int, title: str) -> None:
        await self._db().execute(
            """INSERT INTO product_overrides(product_id,custom_title,hidden,updated_at)
               VALUES(?,?,0,?)
               ON CONFLICT(product_id) DO UPDATE SET custom_title=excluded.custom_title,
                 updated_at=excluded.updated_at""",
            (int(product_id), title, utcnow()),
        )
        await self._db().commit()

    @serialized_write
    async def set_product_hidden(self, product_id: int, hidden: bool) -> None:
        await self._db().execute(
            """INSERT INTO product_overrides(product_id,custom_title,hidden,updated_at)
               VALUES(?,'',?,?)
               ON CONFLICT(product_id) DO UPDATE SET hidden=excluded.hidden,
                 updated_at=excluded.updated_at""",
            (int(product_id), int(hidden), utcnow()),
        )
        await self._db().commit()

    async def service_overrides(self) -> dict[str, dict[str, Any]]:
        """All proxy-service overrides; the table only ever holds a few rows."""
        cur = await self._db().execute(
            "SELECT service_code,custom_title,hidden FROM service_overrides"
        )
        return {
            str(row["service_code"]): {
                "custom_title": str(row["custom_title"] or ""),
                "hidden": bool(row["hidden"]),
            }
            for row in await cur.fetchall()
        }

    async def get_service_override(self, service_code: str) -> dict[str, Any]:
        cur = await self._db().execute(
            "SELECT custom_title,hidden FROM service_overrides WHERE service_code=?",
            (str(service_code),),
        )
        row = await cur.fetchone()
        if not row:
            return {"custom_title": "", "hidden": False}
        return {"custom_title": str(row["custom_title"] or ""), "hidden": bool(row["hidden"])}

    @serialized_write
    async def set_service_title(self, service_code: str, title: str) -> None:
        await self._db().execute(
            """INSERT INTO service_overrides(service_code,custom_title,hidden,updated_at)
               VALUES(?,?,0,?)
               ON CONFLICT(service_code) DO UPDATE SET custom_title=excluded.custom_title,
                 updated_at=excluded.updated_at""",
            (str(service_code), title, utcnow()),
        )
        await self._db().commit()

    @serialized_write
    async def set_service_hidden(self, service_code: str, hidden: bool) -> None:
        await self._db().execute(
            """INSERT INTO service_overrides(service_code,custom_title,hidden,updated_at)
               VALUES(?,'',?,?)
               ON CONFLICT(service_code) DO UPDATE SET hidden=excluded.hidden,
                 updated_at=excluded.updated_at""",
            (str(service_code), int(hidden), utcnow()),
        )
        await self._db().commit()

    DEFAULT_PROXY_SETTINGS: dict[str, Any] = {
        "country": "",
        "rotation": "rotating",
        "session_ttl": 1800,
        "format": "hostname:port:login:password",
        "proxy_count": 10,
        "as_ip": False,
    }

    async def get_proxy_settings(self, user_id: int) -> dict[str, Any]:
        cur = await self._db().execute(
            "SELECT settings_json FROM proxy_settings WHERE user_id=?", (int(user_id),)
        )
        row = await cur.fetchone()
        merged = dict(self.DEFAULT_PROXY_SETTINGS)
        if row:
            try:
                stored = json.loads(row["settings_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                stored = {}
            if isinstance(stored, dict):
                merged.update({k: stored[k] for k in self.DEFAULT_PROXY_SETTINGS if k in stored})
        return merged

    @serialized_write
    async def save_proxy_settings(self, user_id: int, settings: dict[str, Any]) -> None:
        merged = dict(self.DEFAULT_PROXY_SETTINGS)
        merged.update({k: settings[k] for k in self.DEFAULT_PROXY_SETTINGS if k in settings})
        await self._db().execute(
            """INSERT INTO proxy_settings(user_id,settings_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,
                 updated_at=excluded.updated_at""",
            (int(user_id), json.dumps(merged, ensure_ascii=False), utcnow()),
        )
        await self._db().commit()

    async def get_residential_delivery(self, user_id: int) -> list[str]:
        """The last batch of residential proxy lines handed to this customer
        (pre-relay, real upstream host) so a re-check can act on the real list."""
        cur = await self._db().execute(
            "SELECT lines_json FROM residential_deliveries WHERE user_id=?", (int(user_id),)
        )
        row = await cur.fetchone()
        if not row:
            return []
        try:
            data = json.loads(row["lines_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []

    @serialized_write
    async def save_residential_delivery(self, user_id: int, lines: list[str]) -> None:
        clean = [str(x).strip() for x in (lines or []) if str(x).strip()][:2000]
        await self._db().execute(
            """INSERT INTO residential_deliveries(user_id,lines_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET lines_json=excluded.lines_json,
                 updated_at=excluded.updated_at""",
            (int(user_id), json.dumps(clean, ensure_ascii=False), utcnow()),
        )
        await self._db().commit()

    async def list_residential_deliveries(self) -> list[dict[str, Any]]:
        """Every customer who holds a residential proxy list, for the auto-checker."""
        cur = await self._db().execute(
            "SELECT user_id, lines_json, updated_at, last_check_at, last_notice_at "
            "FROM residential_deliveries"
        )
        out: list[dict[str, Any]] = []
        for row in await cur.fetchall():
            try:
                data = json.loads(row["lines_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                data = []
            out.append({
                "user_id": int(row["user_id"]),
                "lines": [str(x) for x in data if str(x).strip()] if isinstance(data, list) else [],
                "updated_at": row["updated_at"],
                "last_check_at": row["last_check_at"],
                "last_notice_at": row["last_notice_at"],
            })
        return out

    @serialized_write
    async def touch_residential_check(self, user_id: int, *, notified: bool = False) -> None:
        now = utcnow()
        if notified:
            await self._db().execute(
                "UPDATE residential_deliveries SET last_check_at=?, last_notice_at=? WHERE user_id=?",
                (now, now, int(user_id)),
            )
        else:
            await self._db().execute(
                "UPDATE residential_deliveries SET last_check_at=? WHERE user_id=?",
                (now, int(user_id)),
            )
        await self._db().commit()

    async def get_residential_entitlement(self, user_id: int) -> float:
        cur = await self._db().execute(
            "SELECT gb FROM residential_entitlements WHERE user_id=?", (int(user_id),)
        )
        row = await cur.fetchone()
        return round(float(row["gb"]), 6) if row else 0.0

    @serialized_write
    async def add_residential_entitlement(self, user_id: int, gb_delta: float) -> float:
        now = utcnow()
        await self._db().execute(
            """INSERT INTO residential_entitlements(user_id, gb, updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 gb = ROUND(residential_entitlements.gb + excluded.gb, 4),
                 updated_at = excluded.updated_at""",
            (int(user_id), round(float(gb_delta), 4), now),
        )
        await self._db().commit()
        return await self.get_residential_entitlement(user_id)

    async def all_user_ids(self) -> list[int]:
        cur = await self._db().execute("SELECT id FROM users ORDER BY id")
        return [int(row["id"]) for row in await cur.fetchall()]

    async def stats(self) -> dict[str, Any]:
        """Detailed CRM snapshot for the admin panel.

        Timestamps are stored as ISO-8601 strings with a fixed +00:00 offset,
        so lexicographic comparison against an ISO cutoff is chronological.
        """
        db = self._db()
        now = datetime.now(timezone.utc)
        d1 = (now - timedelta(days=1)).isoformat(timespec="seconds")
        d7 = (now - timedelta(days=7)).isoformat(timespec="seconds")
        d30 = (now - timedelta(days=30)).isoformat(timespec="seconds")
        paid = "('completed','delivery_pending')"

        async def one(sql: str, params: tuple = ()) -> dict[str, Any]:
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            return dict(row) if row is not None else {}

        async def many(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
            cur = await db.execute(sql, params)
            return [dict(row) for row in await cur.fetchall()]

        def i(value: Any) -> int:
            return int(value or 0)

        users = await one(
            "SELECT COUNT(*) total,"
            " COALESCE(SUM(created_at>=?),0) d1,"
            " COALESCE(SUM(created_at>=?),0) d7,"
            " COALESCE(SUM(created_at>=?),0) d30,"
            " COALESCE(SUM(purchased_count>0),0) paying,"
            " COALESCE(SUM(referrer_id IS NOT NULL),0) referred,"
            " COALESCE(SUM(balance_kopecks>0),0) funded,"
            " COALESCE(SUM(balance_kopecks),0) balance,"
            " COALESCE(SUM(spent_kopecks),0) spent"
            " FROM users",
            (d1, d7, d30),
        )
        orders_paid = await one(
            f"SELECT COUNT(*) c, COALESCE(SUM(total_kopecks),0) revenue,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN 1 END),0) n1,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN 1 END),0) n7,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN 1 END),0) n30,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN total_kopecks END),0) r1,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN total_kopecks END),0) r7,"
            f" COALESCE(SUM(CASE WHEN created_at>=? THEN total_kopecks END),0) r30"
            f" FROM orders WHERE status IN {paid}",
            (d1, d7, d30, d1, d7, d30),
        )
        by_status = await many("SELECT status, COUNT(*) c FROM orders GROUP BY status ORDER BY c DESC")
        by_kind = await many(
            f"SELECT kind, COUNT(*) c, COALESCE(SUM(total_kopecks),0) revenue"
            f" FROM orders WHERE status IN {paid} GROUP BY kind ORDER BY revenue DESC"
        )
        top_products = await many(
            f"SELECT title, COUNT(*) c, COALESCE(SUM(total_kopecks),0) revenue"
            f" FROM orders WHERE status IN {paid} GROUP BY title ORDER BY revenue DESC LIMIT 5"
        )
        attention = await one(
            "SELECT"
            " COALESCE(SUM(status IN ('processing','review','delivery_pending')),0) pending,"
            " COALESCE(SUM(status='review'),0) review,"
            " COALESCE(SUM(status='failed' AND created_at>=?),0) failed7"
            " FROM orders",
            (d7,),
        )
        topups = await one(
            "SELECT COUNT(*) c, COALESCE(SUM(amount_kopecks),0) total,"
            " COALESCE(SUM(CASE WHEN credited_at>=? THEN amount_kopecks END),0) d1,"
            " COALESCE(SUM(CASE WHEN credited_at>=? THEN amount_kopecks END),0) d7,"
            " COALESCE(SUM(CASE WHEN credited_at>=? THEN amount_kopecks END),0) d30"
            " FROM payments WHERE purpose='topup' AND credited_at IS NOT NULL",
            (d1, d7, d30),
        )
        topups_open = await one(
            "SELECT COUNT(*) c FROM payments"
            " WHERE credited_at IS NULL AND status IN ('pending','processing','paid')"
        )
        promo = await one(
            "SELECT COUNT(*) total, COALESCE(SUM(uses_left>0),0) active,"
            " COALESCE(SUM(uses_left),0) uses_left FROM promocodes"
        )
        promo_used = await one(
            "SELECT COUNT(*) c, COALESCE(SUM(p.amount_kopecks),0) credited"
            " FROM promo_uses pu JOIN promocodes p ON p.code=pu.code"
        )

        paying = i(users.get("paying"))
        total_users = i(users.get("total"))
        orders_revenue = i(orders_paid.get("revenue"))
        orders_count = i(orders_paid.get("c"))
        return {
            "users": {
                "total": total_users,
                "new_1d": i(users.get("d1")),
                "new_7d": i(users.get("d7")),
                "new_30d": i(users.get("d30")),
                "paying": paying,
                "conversion": round(paying * 100 / total_users, 1) if total_users else 0.0,
                "referred": i(users.get("referred")),
                "funded": i(users.get("funded")),
                "balance_kopecks": i(users.get("balance")),
                "spent_kopecks": i(users.get("spent")),
            },
            "orders": {
                "paid": orders_count,
                "revenue_kopecks": orders_revenue,
                "avg_kopecks": round(orders_revenue / orders_count) if orders_count else 0,
                "count_1d": i(orders_paid.get("n1")),
                "count_7d": i(orders_paid.get("n7")),
                "count_30d": i(orders_paid.get("n30")),
                "revenue_1d_kopecks": i(orders_paid.get("r1")),
                "revenue_7d_kopecks": i(orders_paid.get("r7")),
                "revenue_30d_kopecks": i(orders_paid.get("r30")),
                "by_status": [(str(r["status"]), i(r["c"])) for r in by_status],
                "by_kind": [(str(r["kind"]), i(r["c"]), i(r["revenue"])) for r in by_kind],
                "top_products": [(str(r["title"]), i(r["c"]), i(r["revenue"])) for r in top_products],
            },
            "attention": {
                "pending": i(attention.get("pending")),
                "review": i(attention.get("review")),
                "failed_7d": i(attention.get("failed7")),
                "topups_open": i(topups_open.get("c")),
            },
            "topups": {
                "count": i(topups.get("c")),
                "total_kopecks": i(topups.get("total")),
                "sum_1d_kopecks": i(topups.get("d1")),
                "sum_7d_kopecks": i(topups.get("d7")),
                "sum_30d_kopecks": i(topups.get("d30")),
            },
            "promo": {
                "total": i(promo.get("total")),
                "active": i(promo.get("active")),
                "uses_left": i(promo.get("uses_left")),
                "redeemed": i(promo_used.get("c")),
                "credited_kopecks": i(promo_used.get("credited")),
            },
            # Backwards-compatible flat keys.
            "users_total": total_users,
            "revenue_kopecks": orders_revenue,
            "pending": i(attention.get("pending")),
            "topups_kopecks": i(topups.get("total")),
        }

