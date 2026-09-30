"""What happens to a StrikeProxy purchase when the provider errors out.

Run on srv2 against the staged tree:

    /opt/asf/.venv/bin/python /opt/asf/_stage_sp/test_sp_recovery.py

The provider is stubbed — no network, no money. Each case checks the one thing
that matters: the customer is only refunded when the shop has *verified* that
nothing was bought upstream.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="sp_rec_"))
_PKG = _ROOT / "app"
_PKG.mkdir()
for _src in sorted(Path("/opt/asf/app").glob("*.py")):
    shutil.copy2(_src, _PKG / _src.name)
for _src in sorted(Path("/opt/asf/_stage_sp").glob("*.py")):
    shutil.copy2(_src, _PKG / _src.name)
sys.path.insert(0, str(_ROOT))

from app import handlers_user as hu  # noqa: E402
from app.clients import ApiError  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import SecretCipher  # noqa: E402


class StubProvider:
    """Stands in for StrikeProxyClient with scripted failures."""

    def __init__(self, *, order_errors=(), services=(), services_error=None, add_errors=()):
        self.order_errors = list(order_errors)
        self.add_errors = list(add_errors)
        self.services_rows = list(services)
        self.services_error = services_error
        self.orders = 0
        self.adds = 0

    async def order(self, plan, quantity):
        self.orders += 1
        if self.order_errors:
            raise self.order_errors.pop(0)
        row = {
            "id": 9001,
            "plan_type": plan,
            "proxy_username": "fresh_user",
            "proxy_password": "pw",
            "purchased_gb": quantity,
        }
        self.services_rows.append(row)
        return {"ok": True, "service": row, "cost": 0.8 * quantity}

    async def add_bandwidth(self, username, add_gb):
        self.adds += 1
        if self.add_errors:
            raise self.add_errors.pop(0)
        for row in self.services_rows:
            if row["proxy_username"] == username:
                row["purchased_gb"] = float(row.get("purchased_gb") or 0) + add_gb
        return {"ok": True, "cost": 0.8 * add_gb}

    async def services(self):
        if self.services_error:
            raise self.services_error
        return list(self.services_rows)


def gateway_error() -> ApiError:
    return ApiError("Сервис временно недоступен, попробуйте позже", status=502, uncertain=True)


async def fresh_db(tmp: Path) -> Database:
    """Every case gets its own database — shared state would make one case's
    leftovers look like another case's result."""
    room = Path(tempfile.mkdtemp(dir=tmp))
    db = Database(room / "t.db", SecretCipher(room / ".secret.key"))
    await db.init()
    return db


async def case(name: str, tmp: Path, stub: StubProvider, *, paid_gb=10.0, existing=None):
    hu._sp_invalidate_services()
    db = await fresh_db(tmp)
    user_id, plan = 42, "mobile"
    if existing:
        await db.save_sp_service(user_id, plan, **existing["service"])
        await db.add_sp_provisioned_gb(user_id, plan, existing["provisioned"])
    else:
        await db.save_sp_service(
            user_id, plan, service_id=0, proxy_username="", proxy_password=""
        )
    await db.add_sp_paid_gb(user_id, plan, paid_gb)
    outcome = "ok"
    try:
        await hu._sp_provision(stub, db, user_id, plan)
    except ApiError as exc:
        outcome = "review" if exc.uncertain else "refund"
    row = await db.get_sp_service(user_id, plan)
    print(
        f"{name}: итог={outcome} · заказов={stub.orders} добавок={stub.adds} · "
        f"логин={row['proxy_username'] or '—'} · залито={row['provisioned_gb']} GB"
    )
    await db.close()
    return outcome, dict(row), stub


async def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        # 1. The order POST dies on a gateway error but the purchase landed:
        #    an unclaimed service of the right plan is sitting upstream.
        orphan = {
            "id": 5533,
            "plan_type": "mobile",
            "proxy_username": "lost_user",
            "proxy_password": "pw",
            "purchased_gb": 10,
        }
        outcome, row, stub = await case(
            "1. заказ потерян в 502, но у поставщика он есть",
            tmp,
            StubProvider(order_errors=[gateway_error()], services=[orphan]),
        )
        assert outcome == "ok", outcome
        assert row["proxy_username"] == "lost_user", row
        assert row["provisioned_gb"] == 10.0, row
        assert stub.orders == 1, stub.orders

        # 1b. An unclaimed service that is too small is somebody else's leftover
        #     (an admin test, say) — adopting it would shortchange the buyer.
        small = {
            "id": 5599,
            "plan_type": "mobile",
            "proxy_username": "tiny_user",
            "proxy_password": "pw",
            "purchased_gb": 1,
        }
        outcome, row, stub = await case(
            "1b. у поставщика висит чужой мелкий сервис",
            tmp,
            StubProvider(order_errors=[gateway_error(), gateway_error()], services=[small]),
        )
        assert outcome == "refund", outcome
        assert row["proxy_username"] == "", row

        # 2. Gateway error and the provider really holds nothing: retry once,
        #    then refund — the customer must not pay for a failed purchase.
        outcome, row, stub = await case(
            "2. 502 и у поставщика пусто",
            tmp,
            StubProvider(order_errors=[gateway_error(), gateway_error()], services=[]),
        )
        assert outcome == "refund", outcome
        assert stub.orders == 2, stub.orders
        assert row["proxy_username"] == "", row

        # 3. Gateway error on the first try, provider fine on the retry.
        outcome, row, stub = await case(
            "3. 502, повтор прошёл",
            tmp,
            StubProvider(order_errors=[gateway_error()], services=[]),
        )
        assert outcome == "ok", outcome
        assert stub.orders == 2, stub.orders
        assert row["proxy_username"] == "fresh_user", row

        # 4. The provider cannot be asked at all — never refund on a guess,
        #    send it to the admin instead.
        outcome, row, stub = await case(
            "4. 502 и список сервисов недоступен",
            tmp,
            StubProvider(
                order_errors=[gateway_error()],
                services_error=ApiError("Сервис временно недоступен", status=503),
            ),
        )
        assert outcome == "review", outcome

        # 5. A top-up of an existing service errors out but landed upstream.
        existing_row = {
            "id": 5530,
            "plan_type": "mobile",
            "proxy_username": "old_user",
            "proxy_password": "pw",
            # 1 GB was already there and the lost top-up (9 GB, bringing the
            # paid total to 10) did land, so the provider holds 11.
            "purchased_gb": 11,
        }
        outcome, row, stub = await case(
            "5. докупка потерялась в 502, но трафик залит",
            tmp,
            StubProvider(add_errors=[gateway_error()], services=[existing_row]),
            paid_gb=10.0,
            existing={
                "service": dict(
                    service_id=5530, proxy_username="old_user", proxy_password="pw"
                ),
                "provisioned": 1.0,
            },
        )
        assert outcome == "ok", outcome
        # 1 GB known before + the 9 GB the shop was pushing = the 10 GB paid for.
        assert row["provisioned_gb"] == 10.0, row
        assert stub.adds == 1, stub.adds

        # 6. Same, but the provider really did not take it: refund.
        stale_row = dict(existing_row, purchased_gb=1)
        outcome, row, stub = await case(
            "6. докупка не прошла",
            tmp,
            StubProvider(
                add_errors=[gateway_error(), gateway_error()], services=[stale_row]
            ),
            paid_gb=10.0,
            existing={
                "service": dict(
                    service_id=5530, proxy_username="old_user", proxy_password="pw"
                ),
                "provisioned": 1.0,
            },
        )
        assert outcome == "refund", outcome
        assert row["provisioned_gb"] == 1.0, row

    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
