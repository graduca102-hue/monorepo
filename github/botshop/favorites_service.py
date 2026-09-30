from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path(os.getenv("DATABASE_PATH", "data1.db"))


@contextmanager
def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_favorites_db() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_favorites (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, product_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_favorites_user_created "
            "ON product_favorites(user_id, created_at DESC)"
        )


def list_favorite_product_ids(user_id: int) -> set[int]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT product_id FROM product_favorites WHERE user_id = ?",
            (int(user_id),),
        ).fetchall()
    return {int(row["product_id"]) for row in rows}


def set_product_favorite(user_id: int, product_id: int, favorite: bool) -> bool:
    normalized_user_id = int(user_id)
    normalized_product_id = int(product_id)
    if normalized_user_id <= 0 or normalized_product_id <= 0:
        raise ValueError("Некорректный пользователь или товар")

    with _connect() as connection:
        if favorite:
            connection.execute(
                "INSERT OR IGNORE INTO product_favorites(user_id, product_id) VALUES (?, ?)",
                (normalized_user_id, normalized_product_id),
            )
        else:
            connection.execute(
                "DELETE FROM product_favorites WHERE user_id = ? AND product_id = ?",
                (normalized_user_id, normalized_product_id),
            )
    return bool(favorite)
