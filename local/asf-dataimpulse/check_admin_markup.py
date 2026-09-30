"""Render the admin «💹 Наценка Proxy» screen offline, to prove it builds.

Usage (on srv2):  cd /opt/asf && ./.venv/bin/python check_admin_markup.py
Read-only.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/asf")
os.chdir("/opt/asf")

from decimal import Decimal, InvalidOperation  # noqa: E402

from app.clients import sale_price_kopecks  # noqa: E402
from app.db import Database, proxy_markup_key  # noqa: E402
from app.dataimpulse import POOL_TYPES  # noqa: E402
from app.handlers_admin import PROXY_SECTION_CODES  # noqa: E402
from app.handlers_user import _di_configured, _sp_configured  # noqa: E402
from app.keyboards import admin_proxy_markup_menu, admin_section_title  # noqa: E402
from app.security import SecretCipher  # noqa: E402
from app.strikeproxy import (  # noqa: E402
    cost_setting_key as sp_cost_key,
    plan_of as sp_plan_of,
    section_codes as sp_section_codes,
    topup_cost_setting_key as sp_topup_cost_key,
)


def _as_cost(raw: str) -> Decimal | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value > 0 else None


async def cost(db: Database, code: str) -> Decimal | None:
    """Same rule as the admin screen: for StrikeProxy the dearer of the new-order
    and top-up rates, because a repeat customer always pays the top-up one."""
    plan = sp_plan_of(code)
    if not plan:
        return _as_cost(await db.get_setting(f"di_cost_{code}"))
    values = [
        value
        for value in (
            _as_cost(await db.get_setting(sp_cost_key(plan))),
            _as_cost(await db.get_setting(sp_topup_cost_key(plan))),
        )
        if value is not None
    ]
    return max(values) if values else None


async def main() -> None:
    db = Database(
        Path(os.getenv("DATABASE_PATH", "data/shop.db")),
        SecretCipher(Path(os.getenv("ENCRYPTION_KEY_PATH", "data/.secret.key"))),
    )
    await db.init()
    # Mirrors _active_proxy_sections() in the admin router: only configured
    # providers are listed.
    codes: list[str] = []
    if await _di_configured(db):
        codes.extend(POOL_TYPES)
    if await _sp_configured(db):
        codes.extend(sp_section_codes())
    codes = codes or list(PROXY_SECTION_CODES)
    common = await db.get_setting("proxy_markup_percent") or "0"
    overrides = await db.proxy_markup_overrides(codes)
    titles = await db.service_overrides()
    print("секции:", tuple(codes))
    print("общая наценка:", common, "| свои наценки:", overrides or "нет")
    for code in codes:
        value = await cost(db, code)
        markup = await db.proxy_markup(code)
        title = admin_section_title(code, titles)
        if value is None:
            print(f"  {title}: закупка не получена · наценка {markup:g}%")
            continue
        print(f"  {title}: {value:g} $ → {sale_price_kopecks(value, 1.0, markup) / 100:.2f} $/GB"
              f" · наценка {markup:g}%")
    for row in admin_proxy_markup_menu(codes, overrides, titles).inline_keyboard:
        for btn in row:
            print(f"  [{btn.text}] -> {btn.callback_data}")
    await db.close()


asyncio.run(main())
