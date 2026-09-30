"""Конфиг из окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    # override=True: у этого бота свой .env с собственным BOT_TOKEN — не должен
    # тихо проигрывать одноимённой переменной, уже гуляющей в окружении
    # (например BOT_TOKEN основного kiro-bot в той же оболочке).
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except Exception:  # dotenv необязателен в проде
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    default_reply_text: str

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")
        owner_raw = (os.getenv("OWNER_ID") or "").strip()
        if not owner_raw:
            raise RuntimeError("OWNER_ID не задан — бот управляет чужими токенами, без владельца нельзя")
        return cls(
            bot_token=token,
            owner_id=int(owner_raw),
            default_reply_text=(os.getenv("DEFAULT_REPLY_TEXT", "") or "Спасибо за сообщение!").strip(),
        )
