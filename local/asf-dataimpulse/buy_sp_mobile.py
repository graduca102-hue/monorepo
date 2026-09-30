"""Buy StrikeProxy traffic for one shop user through the production code path.

Usage (on srv2):  cd /opt/asf && ./.venv/bin/python buy_sp_mobile.py <user_id> <plan> <gb>

Runs exactly what the bot's «➕ Пополнить трафик» does — reserve the order
against the user's shop balance, push the GB upstream, close the order — then
generates a proxy list so the whole chain is proven end to end.

SPENDS REAL MONEY off the reseller balance.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/asf")
os.chdir("/opt/asf")

import uuid  # noqa: E402

import aiohttp  # noqa: E402

from app.db import Database, InsufficientFunds  # noqa: E402
from app.handlers_user import (  # noqa: E402
    _sp_ensure_row,
    _sp_price_kopecks,
    _sp_provision,
    _sp_usage,
)
from app.security import SecretCipher  # noqa: E402
from app.strikeproxy import StrikeProxyClient, rewrite_lines  # noqa: E402


async def main() -> None:
    user_id, plan, gb = int(sys.argv[1]), sys.argv[2], round(float(sys.argv[3]), 2)
    db = Database(Path("data/shop.db"), SecretCipher(Path("data/.secret.key")))
    await db.init()

    async def key() -> str:
        return await db.get_setting("sp_api_key", secret=True)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        sp = StrikeProxyClient(session, key)
        unit = await _sp_price_kopecks(db, plan)
        if unit <= 0:
            raise SystemExit("цена пула не получена — покупка заблокирована")
        total = max(int(round(unit * gb)), 1)
        print(f"цена {unit / 100:.2f} $/GB · {gb:g} GB = {total / 100:.2f} $ с баланса покупателя")
        print("баланс реселлера до:", await sp.balance())

        await _sp_ensure_row(db, user_id, plan)
        request_id = f"sp_{plan}_{user_id}_{uuid.uuid4().hex}"
        try:
            order_id = await db.reserve_order(
                request_id=request_id,
                user_id=user_id,
                kind="proxy_service",
                title=f"{plan} +{gb:g} GB",
                quantity=gb,
                unit_price_kopecks=max(int(total / gb), 1),
                total_kopecks=total,
                request={"provider": "strikeproxy", "plan": plan, "gb": gb},
            )
        except InsufficientFunds:
            raise SystemExit("недостаточно средств на балансе покупателя")
        print("заказ №", order_id)

        await db.add_sp_paid_gb(user_id, plan, gb)
        try:
            await _sp_provision(sp, db, user_id, plan)
        except Exception as exc:  # noqa: BLE001 - mirror the bot's refund branch
            uncertain = bool(getattr(exc, "uncertain", False))
            if not uncertain:
                await db.add_sp_paid_gb(user_id, plan, -gb)
            await db.fail_order(order_id, {"error": str(exc)}, uncertain=uncertain)
            raise SystemExit(f"покупка не прошла: {exc} (uncertain={uncertain})")
        await db.complete_order(
            order_id,
            external_id=request_id,
            response={"plan": plan, "gb": gb},
            delivery="",
            status="completed",
        )
        print("заказ закрыт")
        print("баланс реселлера после:", await sp.balance())

        row = await db.get_sp_service(user_id, plan)
        print("сервис:", {k: row[k] for k in ("service_id", "proxy_username", "paid_gb", "provisioned_gb")})
        print("закупка по факту списания:", await db.get_setting(f"sp_cost_{plan}"), "$/GB")
        print("остаток/расход:", await _sp_usage(sp, db, user_id, plan, row))

        settings = await db.get_sp_settings(user_id, plan)
        raw = await sp.generate(
            int(row["service_id"]),
            count=3,
            country=str(settings.get("country") or ""),
            rotation=str(settings.get("rotation") or "rotating"),
            session_ttl=int(settings.get("session_ttl") or 0),
        )
        lines = rewrite_lines(raw, str(settings.get("format") or "hpu"))
        print("выдача (через релей):")
        for line in lines:
            print("   ", line)
    await db.close()


asyncio.run(main())
