import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Config, _parse_ids  # noqa: E402
from app.handlers import _chat_allowed  # noqa: E402


def test_parse_ids_mixed():
    assert _parse_ids("-100123, 456 ;789 , x, ") == {-100123, 456, 789}


def test_parse_ids_empty():
    assert _parse_ids("") == set()


def test_config_requires_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        Config.from_env()


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    for k in ("OWNER_ID", "AUTO_APPROVE", "WELCOME_TEXT", "ALLOWED_CHAT_IDS", "APPROVE_DELAY"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.auto_approve is True
    assert cfg.owner_id == 0
    assert cfg.allowed_chat_ids == set()
    assert cfg.approve_delay == 0.0


def test_config_auto_approve_off(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("AUTO_APPROVE", "0")
    assert Config.from_env().auto_approve is False


def test_chat_allowed():
    empty = Config(bot_token="t")
    assert _chat_allowed(empty, 123) is True
    scoped = Config(bot_token="t", allowed_chat_ids={1, 2})
    assert _chat_allowed(scoped, 1) is True
    assert _chat_allowed(scoped, 9) is False
