from __future__ import annotations

import base64
import hashlib
import unittest

from app.clients import HeleketClient, external_order_id, extract_delivery, sale_price_kopecks, tariff_total
from app.ui import is_auto_delivery_product, product_display_title, strip_emojis


class PricingTests(unittest.TestCase):
    def test_sale_price_rounds_up_to_kopeck(self) -> None:
        self.assertEqual(sale_price_kopecks("0.11", 82, 25), 1128)

    def test_volume_tariff(self) -> None:
        service = {
            "price": 0.18,
            "tariffs": [{"quantity": 25, "total_price": 2.7}],
        }
        self.assertEqual(tariff_total(service, 25), 2.7)
        self.assertEqual(tariff_total({"price": 0.4}, 5), 2.0)


class HeleketTests(unittest.TestCase):
    def test_request_signature(self) -> None:
        body = '{"amount":"15","currency":"USD","order_id":"1"}'
        key = "secret"
        expected = hashlib.md5((base64.b64encode(body.encode()).decode() + key).encode()).hexdigest()
        self.assertEqual(HeleketClient.signature(body, key), expected)

    def test_webhook_signature(self) -> None:
        key = "secret"
        payload = {"order_id": "topup_1", "status": "paid", "is_final": True}
        body = HeleketClient.encode_body(payload)
        signed = {**payload, "sign": HeleketClient.signature(body, key)}
        self.assertTrue(HeleketClient.verify_webhook(signed, key))
        signed["status"] = "cancel"
        self.assertFalse(HeleketClient.verify_webhook(signed, key))


class UiTests(unittest.TestCase):
    def test_product_title_comes_from_api(self) -> None:
        product = {"id": 24594, "title": "High Quality Instagram account with two-factor key"}
        self.assertEqual(
            product_display_title(product, 9),
            "High Quality Instagram account with two-factor key",
        )

    def test_custom_title_override_wins(self) -> None:
        product = {
            "id": 24594,
            "title": "High Quality Instagram account",
            "_custom_title": "Instagram Премиум 2FA",
        }
        self.assertEqual(product_display_title(product, 9), "Instagram Премиум 2FA")

    def test_title_fallback_when_api_empty(self) -> None:
        self.assertEqual(product_display_title({"id": 1, "title": ""}, 41), "Аккаунт YouTube")

    def test_emojis_are_removed(self) -> None:
        self.assertEqual(strip_emojis("⭐ Товар ✅"), "Товар")

    def test_auto_delivery_product_is_hidden(self) -> None:
        self.assertTrue(is_auto_delivery_product({"title": "Instagram | Автовыдача"}))
        self.assertFalse(is_auto_delivery_product({"title": "Instagram", "attributes": ["Ручная выдача"]}))


class DeliveryTests(unittest.TestCase):
    def test_market_order_delivery_text_is_extracted(self) -> None:
        response = {"orders": [{"id": 42, "status": "completed", "delivery_text": "login: demo"}]}
        self.assertEqual(extract_delivery(response), "login: demo")

    def test_empty_market_delivery_is_not_extracted(self) -> None:
        response = {"orders": [{"id": 42, "status": "delivery_pending", "delivery_text": ""}]}
        self.assertEqual(extract_delivery(response), "")

    def test_market_order_id_is_extracted_from_orders(self) -> None:
        self.assertEqual(external_order_id({"orders": [{"id": 42}]}), "42")


if __name__ == "__main__":
    unittest.main()
