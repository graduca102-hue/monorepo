from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _admin_ids(value: str) -> frozenset[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return frozenset(result)


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    sous_api_base: str
    sous_api_key: str
    heleket_merchant_id: str
    heleket_api_key: str
    public_base_url: str
    support_username: str
    host: str
    port: int
    database_path: Path
    encryption_key_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is not configured")
        admins = _admin_ids(os.getenv("ADMIN_IDS", ""))
        if not admins:
            raise RuntimeError("ADMIN_IDS is not configured")
        return cls(
            bot_token=token,
            admin_ids=admins,
            sous_api_base=os.getenv(
                "SOUS_API_BASE", "https://sousmarketfranchize.shop/api/v1"
            ).rstrip("/"),
            sous_api_key=os.getenv("SOUS_API_KEY", "").strip(),
            heleket_merchant_id=os.getenv("HELEKET_MERCHANT_ID", "").strip(),
            heleket_api_key=os.getenv("HELEKET_API_KEY", "").strip(),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
            support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8080")),
            database_path=Path(os.getenv("DATABASE_PATH", "shop.db")),
            encryption_key_path=Path(os.getenv("ENCRYPTION_KEY_PATH", ".secret.key")),
        )

