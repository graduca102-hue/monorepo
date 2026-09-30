from __future__ import annotations

import re
from typing import Any


EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U0001FC00-\U0001FFFF"
    "\u2300-\u23FF"
    "\u2500-\u27FF"
    "\u2B00-\u2BFF"
    "\uFE0E-\uFE0F"
    "\u200D"
    "]+"
)


def strip_emojis(value: str) -> str:
    text = EMOJI_RE.sub("", str(value))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    return text.strip()


# Идентификаторы категорий раздела "Gmail.com + Youtube.com + Google",
# которые показываются пользователю как YouTube.
YOUTUBE_CATEGORY_IDS = frozenset({40, 41, 42, 43})


def product_display_title(product: dict[str, Any], category_id: int) -> str:
    """Название товара берётся напрямую из API (поле title).

    Мы только убираем декоративные эмодзи и лишние разделители, но не
    подменяем текст поставщика синтетическими названиями.
    """
    custom = str(product.get("_custom_title", "")).strip()
    if custom:
        return custom

    title = strip_emojis(str(product.get("title", "")))
    title = title.strip(" |-–—•·►").strip()
    if title:
        return title

    # Фолбэк на случай, если API не прислал название.
    name = strip_emojis(str(product.get("name", ""))).strip(" |-–—•·►").strip()
    if name:
        return name
    if category_id in YOUTUBE_CATEGORY_IDS:
        return "Аккаунт YouTube"
    return "Аккаунт Instagram"


def is_auto_delivery_product(product: dict[str, Any]) -> bool:
    parts = [
        str(product.get("title", "")),
        str(product.get("description", "")),
        *(str(item) for item in product.get("attributes") or []),
    ]
    haystack = " ".join(parts).lower()
    markers = (
        "автовыдач",
        "автоматическая выдача",
        "автоматической выдач",
        "auto delivery",
        "automatic delivery",
        "instant delivery",
    )
    return any(marker in haystack for marker in markers)
