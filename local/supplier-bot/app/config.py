"""Configuration from the environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional in production (systemd EnvironmentFile)
    pass


def _parse_ids(raw: str) -> frozenset[int]:
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return frozenset(out)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: Path
    catalog_url: str
    markup_percent: float
    max_cards_per_category: int
    support_username: str
    notify_bot_token: str

    @property
    def support_url(self) -> str:
        return f"https://t.me/{self.support_username}"

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is not set (see .env.example)")
        return cls(
            bot_token=token,
            admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "")),
            db_path=Path(os.getenv("DB_PATH", "data/supplier.db")),
            catalog_url=(os.getenv("CATALOG_URL") or "http://127.0.0.1:8081/market/api/categories").strip(),
            markup_percent=float(os.getenv("MARKUP_PERCENT", "50") or 50),
            max_cards_per_category=max(1, int(os.getenv("MAX_CARDS_PER_CATEGORY", "5") or 5)),
            support_username=(os.getenv("SUPPORT_USERNAME") or "UniversallSupportBot").strip().lstrip("@"),
            notify_bot_token=(os.getenv("NOTIFY_BOT_TOKEN") or "").strip(),
        )
