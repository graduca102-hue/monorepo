"""Конфиг из окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv необязателен в проде
    pass


def _parse_ids(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int = 0
    auto_approve: bool = True
    welcome_text: str = ""
    allowed_chat_ids: set[int] = field(default_factory=set)
    approve_delay: float = 0.0

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")
        try:
            delay = float(os.getenv("APPROVE_DELAY", "0") or "0")
        except ValueError:
            delay = 0.0
        return cls(
            bot_token=token,
            owner_id=int(os.getenv("OWNER_ID", "0") or "0"),
            auto_approve=(os.getenv("AUTO_APPROVE", "1").strip() not in ("0", "false", "False", "no")),
            welcome_text=(os.getenv("WELCOME_TEXT", "") or "").strip(),
            allowed_chat_ids=_parse_ids(os.getenv("ALLOWED_CHAT_IDS", "")),
            approve_delay=max(0.0, delay),
        )
