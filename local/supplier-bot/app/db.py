"""SQLite storage: supplier applications, product cards and uploaded stock."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / blocked
    about       TEXT,
    contact     TEXT,
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  INTEGER
);
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     INTEGER NOT NULL,
    category_id     INTEGER NOT NULL,
    category_name   TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    price_usd       REAL NOT NULL,
    markup_percent  REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',  -- active / deleted
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_supplier_category
    ON products (supplier_id, category_id, status);
CREATE TABLE IF NOT EXISTS stock_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products (id),
    content     TEXT NOT NULL,
    sold_at     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_items (product_id, sold_at);
-- Written by botshop (/opt/botshop/services.py) when a storefront order reserves stock.
CREATE TABLE IF NOT EXISTS supplier_orders (
    uuid              TEXT PRIMARY KEY,
    order_number      TEXT NOT NULL DEFAULT '',
    product_id        INTEGER NOT NULL,
    supplier_id       INTEGER NOT NULL,
    quantity          INTEGER NOT NULL,
    supplier_unit_usd REAL NOT NULL,
    created_at        TEXT NOT NULL,
    notified_at       TEXT
);
"""

MAX_STOCK_LINE_LENGTH = 4000

_PRODUCT_WITH_COUNTS = """
SELECT p.*,
       COALESCE(SUM(CASE WHEN s.id IS NOT NULL AND s.sold_at IS NULL THEN 1 ELSE 0 END), 0) AS available,
       COALESCE(SUM(CASE WHEN s.sold_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold
FROM products p
LEFT JOIN stock_items s ON s.product_id = p.id
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sale_price(price_usd: float, markup_percent: float) -> float:
    # Decimal half-up: float rounding turns 0.33 * 1.5 = 0.495 into 0.49.
    value = Decimal(str(price_usd)) * (1 + Decimal(str(markup_percent)) / 100)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)")}
            if "order_uuid" not in columns:
                try:
                    conn.execute("ALTER TABLE stock_items ADD COLUMN order_uuid TEXT")
                except sqlite3.OperationalError:
                    pass  # added concurrently by botshop
        finally:
            conn.close()

    # --- suppliers -----------------------------------------------------

    def get_supplier(self, user_id: int) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM suppliers WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def submit_application(self, user_id: int, username: str | None, full_name: str | None,
                           about: str, contact: str) -> None:
        """Create or re-open an application; approved/blocked suppliers are left as-is."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO suppliers (user_id, username, full_name, status, about, contact, created_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = excluded.username, full_name = excluded.full_name,
                    status = 'pending', about = excluded.about, contact = excluded.contact,
                    created_at = excluded.created_at, decided_at = NULL, decided_by = NULL
                WHERE suppliers.status NOT IN ('approved', 'blocked')
                """,
                (user_id, username, full_name, about, contact, _now()),
            )
        finally:
            conn.close()

    def set_supplier_status(self, user_id: int, status: str, admin_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE suppliers SET status = ?, decided_at = ?, decided_by = ? WHERE user_id = ?",
                (status, _now(), admin_id, user_id),
            )
        finally:
            conn.close()

    def pending_applications(self, limit: int = 10) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM suppliers WHERE status = 'pending' ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def supplier_summary(self, user_id: int) -> dict:
        conn = self._connect()
        try:
            cards = conn.execute(
                "SELECT COUNT(*) FROM products WHERE supplier_id = ? AND status = 'active'", (user_id,)
            ).fetchone()[0]
            row = conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN s.sold_at IS NULL THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN s.sold_at IS NOT NULL THEN 1 ELSE 0 END), 0)
                FROM stock_items s JOIN products p ON p.id = s.product_id
                WHERE p.supplier_id = ? AND p.status = 'active'
                """,
                (user_id,),
            ).fetchone()
            earned = conn.execute(
                "SELECT COALESCE(SUM(quantity * supplier_unit_usd), 0) FROM supplier_orders WHERE supplier_id = ?",
                (user_id,),
            ).fetchone()[0]
            return {"cards": cards, "available": row[0], "sold": row[1], "earned": round(float(earned), 2)}
        finally:
            conn.close()

    def stats(self) -> dict:
        conn = self._connect()
        try:
            result = {"approved": 0, "pending": 0, "rejected": 0, "blocked": 0}
            for status, count in conn.execute("SELECT status, COUNT(*) FROM suppliers GROUP BY status"):
                result[status] = count
            result["cards"] = conn.execute("SELECT COUNT(*) FROM products WHERE status = 'active'").fetchone()[0]
            result["stock"] = conn.execute(
                """SELECT COUNT(*) FROM stock_items s JOIN products p ON p.id = s.product_id
                   WHERE p.status = 'active' AND s.sold_at IS NULL"""
            ).fetchone()[0]
            return result
        finally:
            conn.close()

    # --- product cards -------------------------------------------------

    def count_active_cards(self, user_id: int, category_id: int) -> int:
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM products WHERE supplier_id = ? AND category_id = ? AND status = 'active'",
                (user_id, category_id),
            ).fetchone()[0]
        finally:
            conn.close()

    def create_product(self, user_id: int, category_id: int, category_name: str, title: str,
                       description: str, price_usd: float, markup_percent: float, limit: int) -> int | None:
        """Insert a card; returns None when the per-category limit is already reached."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute(
                "SELECT COUNT(*) FROM products WHERE supplier_id = ? AND category_id = ? AND status = 'active'",
                (user_id, category_id),
            ).fetchone()[0]
            if used >= limit:
                conn.execute("ROLLBACK")
                return None
            now = _now()
            cur = conn.execute(
                """INSERT INTO products (supplier_id, category_id, category_name, title, description,
                                         price_usd, markup_percent, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (user_id, category_id, category_name, title, description, price_usd, markup_percent, now, now),
            )
            conn.execute("COMMIT")
            return int(cur.lastrowid)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def list_products(self, user_id: int) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                _PRODUCT_WITH_COUNTS
                + " WHERE p.supplier_id = ? AND p.status = 'active' GROUP BY p.id ORDER BY p.category_name, p.id",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_product(self, product_id: int, user_id: int) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                _PRODUCT_WITH_COUNTS
                + " WHERE p.id = ? AND p.supplier_id = ? AND p.status = 'active' GROUP BY p.id",
                (product_id, user_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_product(self, product_id: int, user_id: int) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE products SET status = 'deleted', updated_at = ? "
                "WHERE id = ? AND supplier_id = ? AND status = 'active'",
                (_now(), product_id, user_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    # --- sales ---------------------------------------------------------

    def pending_sale_notifications(self, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT o.uuid, o.order_number, o.supplier_id, o.quantity, o.supplier_unit_usd, p.title
                   FROM supplier_orders o LEFT JOIN products p ON p.id = o.product_id
                   WHERE o.notified_at IS NULL ORDER BY o.created_at LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_sale_notified(self, order_uuid: str) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE supplier_orders SET notified_at = ? WHERE uuid = ?", (_now(), order_uuid))
        finally:
            conn.close()

    # --- stock ---------------------------------------------------------

    def add_stock(self, product_id: int, lines: list[str]) -> tuple[int, int]:
        """Add one stock item per line; returns (added, skipped duplicates/oversized)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT content FROM stock_items WHERE product_id = ? AND sold_at IS NULL", (product_id,)
                )
            }
            now = _now()
            seen: set[str] = set()
            rows: list[tuple[int, str, str]] = []
            skipped = 0
            for line in lines:
                if len(line) > MAX_STOCK_LINE_LENGTH or line in existing or line in seen:
                    skipped += 1
                    continue
                seen.add(line)
                rows.append((product_id, line, now))
            conn.executemany(
                "INSERT INTO stock_items (product_id, content, created_at) VALUES (?, ?, ?)", rows
            )
            conn.execute("COMMIT")
            return len(rows), skipped
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
