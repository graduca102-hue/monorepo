"""Per-partner-bot overrides for the menu screen images (banners).

A partner replaces these in the SousPartners cabinet ("Картинки меню"). The shop
handlers are shared between the main bot and every partner bot, so the override
is looked up by the partner bot that received the update. When a partner has no
custom image for a screen (or the main bot renders the handler) the built-in
default from ``assets/banners`` is used, so nothing changes for anyone who does
not touch this screen.

Custom files live on disk at ``partner_media/banners/<partner_bot_id>/<screen>.<ext>``
and are tracked in the ``partner_bot_banners`` table (path + mtime) so the
cabinet can list what is set and the renderer can resolve without a directory
scan on every message.
"""
from __future__ import annotations

import os
from pathlib import Path

from database.data import (
    delete_partner_bot_banner,
    get_partner_bot_banners,
    set_partner_bot_banner,
)

_BASE = Path(__file__).resolve().parent
ASSETS_BANNERS_DIR = _BASE / "assets" / "banners"
PARTNER_BANNERS_DIR = _BASE / "partner_media" / "banners"

# screen key -> default file name in assets/banners + cabinet metadata.
BANNER_REGISTRY: dict[str, dict] = {
    "main_menu": {
        "default_file": "main_menu.jpg",
        "label": "Главное меню",
        "hint": "Картинка над приветствием и меню (/start).",
    },
    "market": {
        "default_file": "market.jpg",
        "label": "Каталог товаров",
        "hint": "Картинка в разделах каталога и категорий.",
    },
    "profile": {
        "default_file": "profile.jpg",
        "label": "Профиль",
        "hint": "Картинка в разделе профиля и баланса.",
    },
    "referral": {
        "default_file": "referral.jpg",
        "label": "Реферальная программа",
        "hint": "Картинка в разделе «Пригласить друга».",
    },
    "proxy": {
        "default_file": "proxy.png",
        "label": "Прокси",
        "hint": "Картинка в разделе прокси.",
    },
}

BANNER_KEYS: tuple[str, ...] = tuple(BANNER_REGISTRY)

# Uploads: capped after the cabinet downscales client-side. Accept the three
# formats Telegram renders well as a photo.
MAX_BANNER_BYTES = 5 * 1024 * 1024
_EXT_BY_SIGNATURE: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


def default_banner_path(screen_key: str) -> Path | None:
    entry = BANNER_REGISTRY.get(screen_key)
    if not entry:
        return None
    return ASSETS_BANNERS_DIR / str(entry["default_file"])


def sniff_image_ext(data: bytes) -> str | None:
    """Return 'jpg' / 'png' / 'webp' for a supported image, else None."""
    if not data:
        return None
    for signature, ext in _EXT_BY_SIGNATURE:
        if data.startswith(signature):
            return ext
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _partner_dir(partner_bot_id: int) -> Path:
    return PARTNER_BANNERS_DIR / str(int(partner_bot_id))


def partner_banner_path(partner_bot: dict | None, screen_key: str) -> Path | None:
    """Custom banner file for an already-loaded partner bot row, or None."""
    if not partner_bot or screen_key not in BANNER_REGISTRY:
        return None
    partner_id = int(partner_bot.get("id") or 0)
    if partner_id <= 0:
        return None
    stored = get_partner_bot_banners(partner_id).get(screen_key)
    if not stored:
        return None
    path = _BASE / stored
    if path.is_file():
        return path
    return None


def has_custom_banner(partner_bot_id: int, screen_key: str) -> bool:
    return partner_banner_path({"id": int(partner_bot_id or 0)}, screen_key) is not None


def save_partner_banner(partner_bot_id: int, screen_key: str, data: bytes) -> str:
    """Persist an uploaded image and register it. Returns the stored rel path."""
    partner_id = int(partner_bot_id or 0)
    if partner_id <= 0 or screen_key not in BANNER_REGISTRY:
        raise ValueError("bad target")
    ext = sniff_image_ext(data)
    if ext is None:
        raise ValueError("Поддерживаются JPG, PNG или WebP")
    if len(data) > MAX_BANNER_BYTES:
        raise ValueError("Файл слишком большой")
    target_dir = _partner_dir(partner_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    # one file per screen — drop any previous extension for this key
    for old in target_dir.glob(f"{screen_key}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dest = target_dir / f"{screen_key}.{ext}"
    dest.write_bytes(data)
    rel_path = str(dest.relative_to(_BASE)).replace(os.sep, "/")
    set_partner_bot_banner(partner_id, screen_key, rel_path)
    return rel_path


def clear_partner_banner(partner_bot_id: int, screen_key: str) -> None:
    partner_id = int(partner_bot_id or 0)
    if partner_id <= 0 or screen_key not in BANNER_REGISTRY:
        return
    for old in _partner_dir(partner_id).glob(f"{screen_key}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    delete_partner_bot_banner(partner_id, screen_key)


def list_banners_for_cabinet(partner_bot_id: int) -> list[dict]:
    partner_id = int(partner_bot_id or 0)
    stored = get_partner_bot_banners(partner_id)
    items: list[dict] = []
    for key, meta in BANNER_REGISTRY.items():
        custom = bool(stored.get(key)) and (_BASE / str(stored.get(key))).is_file()
        items.append(
            {
                "key": key,
                "label": meta["label"],
                "hint": meta.get("hint", ""),
                "changed": custom,
            }
        )
    return items
