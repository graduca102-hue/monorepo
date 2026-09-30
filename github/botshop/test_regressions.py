import json
import sqlite3

from aiogram import Bot

import main
import miniapp
import services
import maskify_service
import keyboard
from database import data


def test_tgads1_deep_link_opens_proxy_section():
    assert main.get_start_section("tgads1") == "proxy"
    assert main.get_start_section("TGADS1") == "proxy"
    assert main.get_start_section("section_proxy") == "proxy"
    assert main.get_start_section("unrelated_campaign") == ""


def test_replaced_payment_invoice_remains_reconcilable():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(
        data,
        "DB_PATH",
        str(Path(directory) / "invoice-history.db"),
    ):
        data.create_db()
        order_id = data.create_order(
            user_id=42,
            proxy_kind="account",
            category_id=1,
            category_name="Instagram",
            item_id=1,
            protocol="Аккаунты",
            quantity=1,
            unit_price=0.5,
            total_price=0.5,
        )
        data.update_order_invoice(order_id, "old", "old", "https://pay.test/old", "USD", "crystalpay")
        data.update_order_invoice(order_id, "new", "new", "https://pay.test/new", "USD", "crystalpay")

        invoices = [row for row in data.list_pending_invoice_reminders() if row["id"] == order_id]
        assert {row["invoice_id"] for row in invoices} == {"old", "new"}
        old_invoice = next(row for row in invoices if row["invoice_id"] == "old")
        paid_order = data.mark_order_paid_from_invoice(order_id, old_invoice)
        assert paid_order["status"] == "paid"
        assert paid_order["xrocket_invoice_id"] == "old"


def test_instagram_delivery_parsing_and_partial_proxy_rejection():
    import asyncio
    from unittest.mock import patch

    record = "sample.user:password:||sessionid=abc; csrftoken=def; ds_user_id=1||"
    assert services.is_instagram_market_order({"product_title": "Instagram account"})
    assert services._extract_instagram_username(record) == "sample.user"
    assert services._extract_instagram_cookie_header(record).startswith("sessionid=abc")

    details = [
        {"host": "one.test", "login": "u", "password": "p", "portHttp": 80},
        {"host": "two.test", "login": "u", "password": "p", "portHttp": 80},
    ]

    async def fake_check(endpoint, protocol, timeout=8):
        return "one.test" in endpoint

    with patch.object(services, "PROXY_PROVIDER_VALIDATE_BEFORE_DELIVERY", True), patch.object(
        services,
        "check_proxy_endpoint",
        fake_check,
    ):
        assert asyncio.run(services.filter_working_proxy_details(details, "HTTP")) == []


def test_instagram_validation_treats_validator_failures_as_retry():
    assert services._classify_instagram_validation_response(
        429,
        None,
        cookie_header=False,
    )[0] == "retry"
    assert services._classify_instagram_validation_response(
        200,
        None,
        cookie_header=False,
    )[0] == "retry"
    assert services._classify_instagram_validation_response(
        400,
        {"message": "useragent mismatch"},
        cookie_header=True,
    )[0] == "retry"
    assert services._classify_instagram_validation_response(
        401,
        {"message": "login_required"},
        cookie_header=True,
    )[0] == "invalid"
    assert services._classify_instagram_validation_response(
        200,
        {"data": {"user": {"username": "available"}}},
        cookie_header=False,
    )[0] == "valid"


def test_referral_profit_sharing_and_existing_links():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(
        data,
        "DB_PATH",
        str(Path(directory) / "referrals.db"),
    ):
        data.create_db()

        # Existing main-bot relations remain attached to the same users.
        data.add_user(10)
        data.add_user(20, referred_by=10)
        assert data.get_user_profile(20)["referred_by"] == 10

        main_order_id = data.create_order(
            user_id=20,
            proxy_kind="static",
            category_id=1,
            category_name="Test",
            item_id=1,
            protocol="http",
            quantity=1,
            unit_price=15,
            total_price=15,
            purchase_unit_price=10,
        )
        data.complete_order(main_order_id, 1, "ok")
        assert data.get_user_profile(10)["referral_earnings"] == 2.5

        # Existing relations are frozen on the legacy 25%-of-top-up model and
        # must not also receive a marketplace-profit reward.
        data.add_user(50)
        data.add_user(60, referred_by=50)
        connection = data.get_connection()
        connection.execute(
            "UPDATE users SET referral_reward_model = ? WHERE user_id = ?",
            (data.LEGACY_REFERRAL_MODEL, 60),
        )
        connection.commit()
        connection.close()
        legacy_order_id = data.create_order(
            user_id=60,
            proxy_kind="static",
            category_id=1,
            category_name="Test",
            item_id=1,
            protocol="http",
            quantity=1,
            unit_price=15,
            total_price=15,
            purchase_unit_price=10,
        )
        data.complete_order(legacy_order_id, 3, "ok")
        assert data.get_user_profile(50)["referral_earnings"] == 0
        legacy_topup_id = data.create_topup(60, 20)
        data.complete_topup_payment(legacy_topup_id)
        assert data.get_user_profile(50)["referral_earnings"] == 5

        partner = data.upsert_partner_bot(
            owner_id=99,
            bot_token="1" * 10 + ":" + "A" * 35,
            bot_username="test_franchise_bot",
        )
        partner_id = int(partner["id"])
        data.add_user(30)
        data.add_user(40, referred_by=30, partner_bot_id=partner_id)
        # A franchise relation must not overwrite the user's main-bot relation.
        assert data.get_user_profile(40)["referred_by"] is None

        partner_order_id = data.create_order(
            user_id=40,
            proxy_kind="static",
            category_id=1,
            category_name="Test",
            item_id=1,
            protocol="http",
            quantity=1,
            unit_price=20,
            total_price=20,
            partner_bot_id=partner_id,
            purchase_unit_price=10,
            partner_profit_amount=10,
        )
        data.complete_order(partner_order_id, 2, "ok")
        assert data.get_user_profile(30)["referral_earnings"] == 2.5
        partner = data.get_partner_bot_by_id(partner_id)
        assert partner["partner_earnings"] == 7.5


def test_money_owner_report_contains_leader_referrals_and_languages():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(
        data,
        "DB_PATH",
        str(Path(directory) / "money-report.db"),
    ):
        data.create_db()
        data.add_user(10, language_code="uk")
        data.add_user(20, referred_by=10, language_code="ru")
        data.add_user(21, referred_by=10, language_code="en")
        connection = data.get_connection()
        connection.executemany(
            "UPDATE users SET registered_at = ? WHERE user_id = ?",
            [
                ("15:00:00 22-08-2026", 20),
                ("13:59:59 25-08-2026", 21),
            ],
        )
        connection.commit()
        connection.close()

        report = data.get_money_referral_admin_report()
        assert report == [
            {
                "position": 1,
                "user_id": 10,
                "language_code": "uk",
                "count": 2,
                "invited_users": [
                    {
                        "user_id": 20,
                        "language_code": "ru",
                        "registered_at": "15:00:00 22-08-2026",
                        "registered_at_msk": "22.08.2026 18:00",
                    },
                    {
                        "user_id": 21,
                        "language_code": "en",
                        "registered_at": "13:59:59 25-08-2026",
                        "registered_at_msk": "25.08.2026 16:59",
                    },
                ],
            }
        ]
        chunks = keyboard.split_money_owner_report("line 1\n" + ("x" * 30) + "\nline 3", limit=20)
        assert chunks == ["line 1", "x" * 20, ("x" * 10) + "\nline 3"]


def test_market_site_is_kept_in_market_directory():
    assert miniapp.MARKET_SITE_INDEX_PATH.name == "index.html"
    assert miniapp.MARKET_SITE_INDEX_PATH.parent.name == "market"
    assert miniapp.MARKET_SITE_INDEX_PATH.is_file()


def test_partner_cabinet_has_telegram_init_data_fallback_and_cache_busting():
    partner_app = miniapp.PARTNER_APP_JS_PATH.read_text(encoding="utf-8")
    partner_index = miniapp.PARTNER_INDEX_PATH.read_text(encoding="utf-8")
    assert "getTelegramInitData" in partner_app
    assert "tgWebAppData" in partner_app
    assert "sessionStorage.getItem('tgWebAppData')" in partner_app
    assert "error.status = response.status" in partner_app
    assert "Не удалось загрузить кабинет" in partner_app
    assert 'styles.css?v=__ASSET_VERSION__' in partner_index
    assert 'app.js?v=__ASSET_VERSION__' in partner_index


def test_miscellaneous_and_promotion_catalog_branches_are_hidden():
    categories = [
        {"id": 68, "name": "Остальное", "children": [{"id": 72, "name": "Skype"}]},
        {
            "id": 3,
            "name": "Instagram",
            "children": [
                {"id": 9, "name": "Авторег"},
                {"id": 17, "name": "Раскрученные с подписчиками"},
                {"id": 137, "name": "Instagram накрутка Подписчиков, Лайков"},
            ],
        },
        {
            "id": 24,
            "name": "TikTok",
            "children": [
                {"id": 25, "name": "Авторег"},
                {"id": 27, "name": "Раскрученные"},
                {"id": 119, "name": "Авторег раскрученные по ГЕО"},
            ],
        },
        {
            "id": 34,
            "name": "Twitter",
            "children": [
                {"id": 36, "name": "Авторег"},
                {"id": 37, "name": "С заполнением и фолловерами"},
                {"id": 132, "name": "Услуги накрутки"},
            ],
        },
    ]
    filtered = services._filter_financial_account_categories(categories)
    visible_ids = {
        int(category["id"])
        for category in filtered
    } | {
        int(child["id"])
        for category in filtered
        for child in category.get("children") or []
    }
    assert {68, 17, 137, 27, 119, 37, 132}.isdisjoint(visible_ids)
    assert {3, 9, 24, 25, 34, 36}.issubset(visible_ids)
    assert all(
        not services.is_market_category_available(category_id)
        for category_id in (68, 17, 137, 27, 119, 37, 132)
    )
    assert not services.is_market_product_allowed(
        {"category_id": 9, "title": "Услуга накрутки подписчиков"}
    )
    assert services.is_market_product_allowed(
        {"category_id": 9, "title": "Instagram авторег аккаунт"}
    )
    keyboard_markup = keyboard.build_market_group_keyboard(
        [{"id": 68, "name": "Остальное"}, {"id": 28, "name": "Telegram"}],
        (68, 28),
    )
    callbacks = [
        button.callback_data
        for row in keyboard_markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("market_category:")
    ]
    assert callbacks == ["market_category:28"]


def test_proxy_formats():
    raw = "proxy.sousmarketfranchize.shop:80:d8d981f3f4be482891adc96063498f6c-cc-PH-s-de4811961-ttl-1800:452e17fb2f23582cca35fbf48425622d"
    assert maskify_service.format_proxy_export(raw, "hostname:port:login:password", "http") == raw
    assert maskify_service.format_proxy_export(raw, "login:password@hostname:port", "http") == (
        "d8d981f3f4be482891adc96063498f6c-cc-PH-s-de4811961-ttl-1800:"
        "452e17fb2f23582cca35fbf48425622d@proxy.sousmarketfranchize.shop:80"
    )
    canonical = "user:pass@proxy.example:8080"
    assert maskify_service.format_proxy_export(canonical, "hostname:port:login:password", "http") == (
        "proxy.example:8080:user:pass"
    )


def test_market_order_status_is_never_cached():
    assert services._is_market_cacheable_request("GET", "/api/v2/categories", None)
    assert services._is_market_cacheable_request("GET", "/api/v2/products/123", None)
    assert not services._is_market_cacheable_request("GET", "/api/v2/orders/order-uuid", None)
    assert not services._is_market_cacheable_request("GET", "/api/v2/user/balance", None)
    assert not services._is_market_cacheable_request("POST", "/api/v2/orders", {"items": []})


def test_market_customer_order_number_uses_djekxa_number():
    order = {"id": 412, "proxy_kind": "account", "supplier_order_number": "2-190"}
    assert keyboard.get_customer_order_number(order) == "2-190"
    assert keyboard.get_customer_order_number({"id": 412, "proxy_kind": "account"}) == "—"
    assert keyboard.get_customer_order_number({"id": 412, "proxy_kind": "static"}) == "412"


def test_market_api_serialization_exposes_djekxa_order_number():
    server = miniapp.MiniAppServer(_TestBot())
    order = {
        "id": 412,
        "proxy_kind": "account",
        "supplier_order_number": "2-190",
        "product_title": "Test product",
        "category_name": "Test",
        "quantity": 1,
        "unit_price": 1.0,
        "total_price": 1.0,
        "status": "delivery_pending",
        "delivery_text": "",
        "created_at": "2026-08-17 12:00:00",
        "updated_at": "2026-08-17 12:00:00",
    }
    assert server._serialize_order_detail(order)["order_number"] == "2-190"
    assert server._serialize_orders([order])[0]["order_number"] == "2-190"


class _TestBot:
    token = "0" * 10 + ":" + "A" * 35


def test_public_api_proxy_services_contract_and_prices():
    screenshot_tariffs = {
        "abcproxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
        "9proxy": ((10, 1), (50, 3), (100, 5), (200, 10), (400, 19), (800, 36), (1930, 83), (3600, 152), (5000, 200)),
        "9proxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
        "lokiproxy": ((10, 1), (25, 2), (50, 3), (100, 5), (200, 9), (400, 18), (800, 34), (1930, 80), (3600, 147), (5000, 199)),
        "711proxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 11), (400, 20), (800, 38), (1930, 89), (3600, 162), (5000, 200)),
        "proxy001_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 16), (50, 38), (100, 70), (200, 130), (500, 310)),
        "cliproxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 11), (400, 20), (800, 38), (1930, 89), (3600, 162), (5000, 225)),
        "922proxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
        "piaproxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
    }
    for service, tariffs in screenshot_tariffs.items():
        assert main.PARTNER_PROXY_TARIFFS[service] == tariffs

    services_catalog = {item["service"]: item for item in miniapp.get_public_api_proxy_services()}
    new_services = {
        "piaproxy",
        "piaproxy_gb",
        "piaproxy_long_acting",
        "922proxy",
        "922proxy_gb",
        "abcproxy",
        "abcproxy_gb",
        "lokiproxy",
    }
    assert services_catalog["residential"]["price"] == 0.50
    assert services_catalog["residential"]["delivery"] == "instant"
    assert services_catalog["residential"]["settings"]["country"].endswith("UA")
    assert miniapp.PUBLIC_API_PROXY_SERVICE_TARIFFS == main.PARTNER_PROXY_TARIFFS
    assert main.PARTNER_PROXY_SERVICES == set(miniapp.PUBLIC_API_PROXY_SERVICE_TARIFFS)
    assert new_services <= services_catalog.keys()
    assert services_catalog["piaproxy"]["unit"] == "IP"
    assert services_catalog["piaproxy"]["price"] == 0.18
    assert services_catalog["piaproxy_gb"]["unit"] == "GB"
    assert services_catalog["piaproxy_gb"]["price"] == 0.9
    assert services_catalog["piaproxy_long_acting"]["price"] == 0.27
    assert services_catalog["922proxy"]["price"] == 0.18
    assert services_catalog["922proxy_gb"]["price"] == 0.9
    assert services_catalog["abcproxy"]["price"] == 0.18
    assert services_catalog["abcproxy_gb"]["price"] == 0.9
    assert services_catalog["lokiproxy"]["price"] == 0.09
    assert services_catalog["9proxy"]["price"] == 0.09
    assert services_catalog["9proxy_gb"]["price"] == 0.9
    assert services_catalog["proxy001_gb"]["price"] == 0.9
    assert services_catalog["711proxy"]["price"] == 0.18
    assert services_catalog["cliproxy"]["price"] == 0.18
    assert miniapp.get_public_api_proxy_service_total("9proxy", 10) == 0.9
    assert miniapp.get_public_api_proxy_service_total("9proxy", 50) == 2.7
    assert miniapp.get_public_api_proxy_service_total("proxy001_gb", 20) == 14.4
    for service, tariffs in screenshot_tariffs.items():
        api_tariffs = {
            int(item["quantity"]): float(item["total_price"])
            for item in services_catalog[service]["tariffs"]
        }
        for quantity, shop_total in tariffs:
            assert api_tariffs[int(quantity)] == round(float(shop_total) * 0.9 + 1e-9, 2)


def test_rotation_and_ukraine_normalization(tmp_path):
    original_path = maskify_service.MASKIFY_DB_PATH
    db_path = tmp_path / "proxy.db"
    maskify_service.MASKIFY_DB_PATH = str(db_path)
    try:
        maskify_service.init_maskify_db()
        with sqlite3.connect(maskify_service.MASKIFY_DB_PATH) as db:
            db.execute(
                "INSERT INTO maskify_proxy_settings(telegram_user_id,pool,subuser_id,settings_json,created_at) VALUES(1,'residential',0,?,0)",
                (json.dumps({"country": "UK", "country_name": "Украина", "type": "rotating", "sessionttl": 0}),),
            )
        settings = maskify_service.get_proxy_settings(1, "residential")
        assert settings["country"] == "UA"
        assert settings["sessionttl"] == 0
    finally:
        maskify_service.MASKIFY_DB_PATH = original_path
        # sqlite may keep the file handle briefly on Windows.
        try:
            db_path.unlink()
        except (FileNotFoundError, PermissionError):
            pass



def test_residential_session_login_modes(monkeypatch=None):
    """Rotating logins must not contain a sticky-session suffix."""
    import asyncio

    async def fake_subuser(_user_id):
        return {"username": "user", "password": "pass"}

    async def fake_filter(lines, _protocol, required):
        return lines[:required]

    from unittest.mock import patch
    with (
        patch.object(maskify_service, "ensure_maskify_personal_subuser", fake_subuser),
        patch.object(maskify_service, "_filter_working_proxy_lines", fake_filter),
        patch.object(maskify_service, "MASKIFY_PROXY_HOST", "proxy.example"),
        patch.object(maskify_service, "MASKIFY_PROXY_PORT", 80),
    ):
        rotating = asyncio.run(maskify_service.generate_maskify_proxies(1, {
            "type": "rotating", "sessionttl": 0, "country": "UA", "protocol": "http", "quantity": 3,
        })).splitlines()
        assert len(rotating) == len(set(rotating)) == 3
        assert all("-cc-UA-s-" in proxy and "-ttl-" not in proxy for proxy in rotating)
        sticky = asyncio.run(maskify_service.generate_maskify_proxies(1, {
            "type": "sticky", "sessionttl": 1800, "country": "UA", "protocol": "http", "quantity": 1,
        }))
        assert "-cc-UA-s-" in sticky and "-ttl-1800" in sticky


async def _test_lolz_fiat_conversion():
    from unittest.mock import patch

    captured = {}

    async def fake_request(_method, _path, *, params=None, json_body=None):
        if _path == "/currency":
            return {"currencyList": {"USD": {"rate": 84.0}, "RUB": {"rate": 1.0}}}
        captured.update(json_body or {})
        return {"invoice": {"invoice_id": "1", "url": "https://example.test/pay", "payment_id": "test"}}

    with (
        patch.object(maskify_service, "MASKIFY_DB_PATH", maskify_service.MASKIFY_DB_PATH),
        patch("services.LOLZ_PAYMENT_CURRENCY", "RUB"),
        patch("services.LOLZ_MERCHANT_ID", 1),
        patch("services._lolz_request", fake_request),
    ):
        from services import create_lolz_invoice
        invoice = await create_lolz_invoice(0.30, "test", "test", "https://example.test")
    assert captured["currency"] == "RUB"
    assert captured["amount"] == 25.2
    assert invoice["amount"] == 25.2

def test_legacy_proxy_pricing_uses_franchise_markup(monkeypatch):
    class DummyBot:
        pass

    monkeypatch.setattr(
        main,
        "get_current_partner_bot",
        lambda bot: {"proxy_markup_percentage": 150} if bot is not None else None,
    )
    assert main.get_proxy_traffic_sale_price("residential", 10, DummyBot()) == 10.0
    assert main.get_proxy_traffic_sale_price("residential", 10, None) == 4.0


async def _test_legacy_partner_proxy_quote(monkeypatch):
    class DummyBot:
        pass

    monkeypatch.setattr(
        main,
        "get_current_partner_bot",
        lambda bot: {"proxy_markup_percentage": 100} if bot is not None else None,
    )
    async def fake_account_info():
        return {"balance_usd": "100", "prices": {"9proxy": "0.029"}}
    monkeypatch.setattr(main, "get_partner_proxy_account_info", fake_account_info)
    supplier_unit_price, total_price = await main.get_partner_proxy_quote("9proxy", 10, DummyBot())
    assert supplier_unit_price == 0.029
    assert total_price == 2.0


def test_api_residential_traffic_split_storage():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir, patch.object(maskify_service, "MASKIFY_DB_PATH", str(Path(temp_dir) / "maskify.db")):
        client = maskify_service.create_api_residential_client(1001, "customer_42", "Customer 42")
        assert client["client_id"] == "customer_42"
        assert client["agent_key"].startswith("sous_agent_")
        resolved = maskify_service.get_api_residential_client_by_key(client["agent_key"])
        assert resolved and resolved["client_id"] == "customer_42"
        unchanged = maskify_service.create_api_residential_client(1001, "customer_42", "Renamed")
        assert "agent_key" not in unchanged
        rotated = maskify_service.create_api_residential_client(1001, "customer_42", rotate_agent_key=True)
        assert rotated["agent_key"] != client["agent_key"]
        assert maskify_service.get_api_residential_client_by_key(client["agent_key"]) is None
        assert maskify_service.credit_api_residential_pool(1001, 10, 501) == 10
        assert maskify_service.credit_api_residential_pool(1001, 10, 501) == 10
        pool = maskify_service.get_api_residential_pool(1001)
        assert pool == {"purchased_gb": 10.0, "available_gb": 10.0}
        assert maskify_service._adjust_api_residential_pool(1001, -4) == 6
        try:
            maskify_service._adjust_api_residential_pool(1001, -7)
        except maskify_service.MaskifyError:
            pass
        else:
            raise AssertionError("pool allowed overspending")


def test_public_openapi_contains_residential_split_and_webhooks():
    import asyncio
    class Request:
        scheme = "https"
        host = "example.test"
        headers = {}
    server = object.__new__(miniapp.MiniAppServer)
    response = asyncio.run(server.handle_public_api_openapi(Request()))
    payload = json.loads(response.text)
    paths = payload["paths"]
    assert "/proxy/residential/traffic" in paths
    assert "/proxy/residential/clients/{client_id}/traffic" in paths
    assert "/proxy/residential/subagent/proxies" in paths
    assert "/webhooks" in paths


if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    test_proxy_formats()
    test_tgads1_deep_link_opens_proxy_section()
    test_replaced_payment_invoice_remains_reconcilable()
    test_instagram_delivery_parsing_and_partial_proxy_rejection()
    test_referral_profit_sharing_and_existing_links()
    test_money_owner_report_contains_leader_referrals_and_languages()
    test_market_site_is_kept_in_market_directory()
    test_partner_cabinet_has_telegram_init_data_fallback_and_cache_busting()
    test_miscellaneous_and_promotion_catalog_branches_are_hidden()
    test_market_order_status_is_never_cached()
    test_market_customer_order_number_uses_djekxa_number()
    test_market_api_serialization_exposes_djekxa_order_number()
    test_public_api_proxy_services_contract_and_prices()
    test_residential_session_login_modes()
    test_api_residential_traffic_split_storage()
    test_public_openapi_contains_residential_split_and_webhooks()
    asyncio.run(_test_lolz_fiat_conversion())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        test_rotation_and_ukraine_normalization(Path(directory))
    with patch.object(main, "get_current_partner_bot", lambda bot: {"proxy_markup_percentage": 150} if bot is not None else None):
        class DummyBot:
            pass
        assert main.get_proxy_traffic_sale_price("residential", 10, DummyBot()) == 10.0
        assert main.get_proxy_traffic_sale_price("residential", 10, None) == 4.0
    async def fake_account_info():
        return {"balance_usd": "100", "prices": {"9proxy": "0.029"}}
    with patch.object(main, "get_current_partner_bot", lambda bot: {"proxy_markup_percentage": 100} if bot is not None else None), patch.object(main, "get_partner_proxy_account_info", fake_account_info):
        class DummyBot:
            pass
        supplier_unit_price, total_price = asyncio.run(main.get_partner_proxy_quote("9proxy", 10, DummyBot()))
        assert supplier_unit_price == 0.029
        assert total_price == 2.0
        menu = main.build_proxy_test_menu_keyboard(DummyBot())
        assert menu.inline_keyboard[0][0].text == "Резидентские | 0.8$ / GB"
        menu_labels = [row[0].text for row in menu.inline_keyboard]
        assert "PIA Proxy | IPs / GB" in menu_labels
        assert "922 Proxy | IPs / GB" in menu_labels
        assert "ABC Proxy | IPs / GB" in menu_labels
        assert "LokiProxy | IPs" in menu_labels
        tariffs = main.build_partner_proxy_service_keyboard("9proxy", DummyBot())
        assert tariffs.inline_keyboard[0][0].text == "10 IPs | 2$"
    print("regression tests: OK")
