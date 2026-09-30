"""Category tree taken from the SOUS MARKET storefront API (with a static fallback)."""
from __future__ import annotations

import logging
import re
import time

import aiohttp

log = logging.getLogger("supplier-bot.catalog")

# Used when the storefront catalogue is empty (goods switched off) or unreachable.
FALLBACK_CATEGORIES: list[dict] = [
    {"id": -1, "name": "Telegram", "children": []},
    {"id": -2, "name": "Instagram", "children": []},
    {"id": -3, "name": "TikTok", "children": []},
    {"id": -4, "name": "ВКонтакте", "children": []},
    {"id": -5, "name": "Почты", "children": []},
    {"id": -6, "name": "Игры", "children": []},
    {"id": -7, "name": "VPN и прокси", "children": []},
    {"id": -8, "name": "Другое", "children": []},
]

_DECORATIONS = set("►▶▷▸➤➜➡→★☆✓✔✅❌✮✌❤⭐♛✨❗")


def clean_name(value: object) -> str:
    text = "".join(
        ch for ch in str(value or "")
        if ch not in _DECORATIONS
        and not (0x1F000 <= ord(ch) <= 0x1FAFF)
        and ord(ch) not in (0xFE0F, 0x200D)
    )
    text = re.sub(r"\s+", " ", text).strip(" |—–-")
    return text or "Без названия"


def shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse(node: dict) -> dict:
    return {
        "id": int(node["id"]),
        "name": clean_name(node.get("name")),
        "children": [
            _parse(child) for child in node.get("children") or []
            if isinstance(child, dict) and child.get("id") is not None
        ],
    }


class Catalog:
    def __init__(self, url: str, ttl: float = 600.0) -> None:
        self.url = url
        self.ttl = ttl
        self._cache: list[dict] = []
        self._expires_at = 0.0

    async def categories(self) -> list[dict]:
        now = time.monotonic()
        if self._cache and now < self._expires_at:
            return self._cache
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                async with session.get(self.url) as response:
                    data = await response.json(content_type=None)
            items = data.get("items") if isinstance(data, dict) else data
            parsed = [_parse(c) for c in items or [] if isinstance(c, dict) and c.get("id") is not None]
            if parsed:
                self._cache, self._expires_at = parsed, now + self.ttl
                return parsed
        except Exception as error:
            log.warning("catalogue fetch failed: %s", error)
        return self._cache or FALLBACK_CATEGORIES

    async def find(self, category_id: int) -> tuple[dict | None, dict | None]:
        """Return (node, parent) for a category id."""

        def walk(nodes: list[dict], parent: dict | None):
            for node in nodes:
                if node["id"] == category_id:
                    return node, parent
                found = walk(node["children"], node)
                if found[0] is not None:
                    return found
            return None, None

        return walk(await self.categories(), None)
