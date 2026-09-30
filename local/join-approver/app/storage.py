"""Простой JSON-стор известных чатов (без БД)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_PATH = Path(__file__).resolve().parent.parent / "data" / "chats.json"


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text("utf-8"))
    except Exception:
        return {}


def remember_chat(chat_id: int, title: str, status: str, username: str | None = None) -> None:
    """Запомнить/обновить чат. status — роль бота: administrator/member/left/kicked."""
    with _LOCK:
        data = _load()
        key = str(chat_id)
        rec = data.get(key, {})
        rec.update(
            id=chat_id,
            title=title or rec.get("title", ""),
            username=username or rec.get("username"),
            status=status,
            updated=int(time.time()),
        )
        rec.setdefault("added", int(time.time()))
        data[key] = rec
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def bump_approved(chat_id: int) -> None:
    with _LOCK:
        data = _load()
        rec = data.get(str(chat_id))
        if not rec:
            return
        rec["approved_count"] = rec.get("approved_count", 0) + 1
        rec["last_approved"] = int(time.time())
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def list_chats() -> list[dict]:
    return sorted(_load().values(), key=lambda r: r.get("title", "").lower())
