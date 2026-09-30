"""Конфиг из окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    # override=True: у этого бота свой .env — он не должен проигрывать
    # одноимённому BOT_TOKEN основного kiro-bot, уже висящему в окружении.
    load_dotenv(BASE_DIR / ".env", override=True)
except Exception:  # dotenv необязателен в проде
    pass


def _flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    lines_per_user: int
    notify_owner: bool
    dedupe_lines: bool
    db_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")
        owner_raw = (os.getenv("OWNER_ID") or "").strip()
        if not owner_raw:
            raise RuntimeError("OWNER_ID не задан — загружать файлы может только владелец")
        per_user = int((os.getenv("LINES_PER_USER") or "10").strip() or 10)
        if per_user < 1:
            raise RuntimeError("LINES_PER_USER должен быть >= 1")
        return cls(
            bot_token=token,
            owner_id=int(owner_raw),
            lines_per_user=per_user,
            notify_owner=_flag("NOTIFY_OWNER", True),
            dedupe_lines=_flag("DEDUPE_LINES", True),
            db_path=BASE_DIR / "data" / "drops.sqlite3",
        )
