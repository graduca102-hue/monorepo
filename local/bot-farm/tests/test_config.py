import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Config  # noqa: E402


def test_config_requires_bot_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("OWNER_ID", "1")
    with pytest.raises(RuntimeError):
        Config.from_env()


def test_config_requires_owner_id(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.delenv("OWNER_ID", raising=False)
    with pytest.raises(RuntimeError):
        Config.from_env()


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("OWNER_ID", "42")
    monkeypatch.delenv("DEFAULT_REPLY_TEXT", raising=False)
    cfg = Config.from_env()
    assert cfg.owner_id == 42
    assert cfg.default_reply_text == "Спасибо за сообщение!"
