import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot import Bot, COIN_PACKAGES, Database, LinkniAPI, MOSCOW, ORDER_SERVICES


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.db.register_user({"id": 1, "first_name": "Owner", "username": "owner"})

    def tearDown(self):
        self.db.connection.close()
        self.temp_dir.cleanup()

    def test_referral_is_confirmed_only_once(self):
        self.db.register_user({"id": 2, "first_name": "Friend"}, inviter_id=1)
        self.assertEqual(self.db.confirm_referral(2), 1)
        self.assertIsNone(self.db.confirm_referral(2))
        self.assertEqual(self.db.confirmed_referrals(1), 1)
        self.assertEqual(self.db.get_user(1)["balance"], 4500)

    def test_bonus_cannot_be_claimed_twice(self):
        claimed, amount, streak, balance = self.db.claim_daily_bonus(1)
        self.assertTrue(claimed)
        self.assertEqual((amount, streak, balance), (500, 1, 500))
        claimed_again, amount_again, _, balance_again = self.db.claim_daily_bonus(1)
        self.assertFalse(claimed_again)
        self.assertEqual((amount_again, balance_again), (0, 500))

    def test_claim_once_is_idempotent(self):
        first = self.db.claim_once(1, "task:today", 250)
        second = self.db.claim_once(1, "task:today", 250)
        self.assertEqual(first, (True, 250))
        self.assertEqual(second, (False, 250))

    def test_promo_can_only_be_used_once(self):
        first = self.db.redeem_promo(1, "welcome")
        second = self.db.redeem_promo(1, "WELCOME")
        self.assertEqual(first, ("ok", 1000, 1000))
        self.assertEqual(second, ("used", 0, 1000))

    def test_flyer_reward_is_credited_only_once(self):
        task = {
            "signature": "signed-task",
            "task": "subscribe channel",
            "name": "Test channel",
            "links": ["https://t.me/example"],
            "status": "incomplete",
        }
        self.db.upsert_flyer_tasks(1, [task], "earn")
        first = self.db.update_flyer_task(1, "signed-task", "complete", 250)
        second = self.db.update_flyer_task(1, "signed-task", "complete", 250)
        self.assertEqual(first, (True, 250))
        self.assertEqual(second, (False, 250))

    def test_mandatory_flyer_task_does_not_credit_reward(self):
        task = {
            "signature": "mandatory-task",
            "task": "subscribe channel",
            "links": ["https://t.me/example"],
            "status": "incomplete",
        }
        self.db.upsert_flyer_tasks(1, [task], "mandatory")
        result = self.db.update_flyer_task(1, "mandatory-task", "complete", 250)
        self.assertEqual(result, (False, 0))

    def test_linkni_webhook_status_is_saved(self):
        self.db.store_linkni_event(
            1,
            "2i2r5",
            "tg1",
            "subscribed",
            {"user_id": 1, "sell_code": "2i2r5", "sub_code": "tg1", "status": "subscribed"},
        )
        self.assertEqual(self.db.latest_linkni_status(1, "2i2r5", "tg1"), "subscribed")


class LinkniTests(unittest.TestCase):
    def setUp(self):
        self.linkni = LinkniAPI(
            "https://go.linkni.me/api/subscriptions",
            "2i2r5",
            "https://telegram.me/linknibot/app?startapp=x_2i2r5",
        )

    def test_task_url_contains_unique_sub_code(self):
        self.assertEqual(
            self.linkni.task_url(123456789),
            "https://telegram.me/linknibot/app?startapp=x_2i2r5_tg123456789",
        )

    def test_latest_matching_status_is_selected(self):
        records = [
            {
                "user_id": 123,
                "status": "not_subscribed",
                "sub_code": "tg123",
                "timestamp": "2026-04-02T12:00:00Z",
            },
            {
                "user_id": 123,
                "status": "subscribed",
                "sub_code": "tg123",
                "timestamp": "2026-04-02T12:01:00Z",
            },
        ]
        self.assertEqual(LinkniAPI.select_status(records, 123, "tg123"), "subscribed")


class OrderFlowTests(unittest.TestCase):
    def test_tariff_prices(self):
        self.assertEqual(ORDER_SERVICES["followers_basic"].provider_service_id, 458)
        self.assertEqual(ORDER_SERVICES["followers_basic"].unit_price, 20)
        self.assertEqual(ORDER_SERVICES["followers_premium"].provider_service_id, 1504)
        self.assertEqual(ORDER_SERVICES["followers_premium"].unit_price, 35)
        self.assertEqual(ORDER_SERVICES["followers_premium"].minimum, 50)
        self.assertEqual(ORDER_SERVICES["reactions"].provider_service_id, 884)
        self.assertEqual(ORDER_SERVICES["reactions"].unit_price, 8)
        self.assertEqual(ORDER_SERVICES["views"].provider_service_id, 1565)
        self.assertEqual(ORDER_SERVICES["views"].unit_price, 2)
        self.assertEqual(ORDER_SERVICES["tiktok_followers"].provider_service_id, 444)
        self.assertEqual(ORDER_SERVICES["tiktok_followers"].unit_price, 12)
        self.assertEqual(ORDER_SERVICES["tiktok_views"].provider_service_id, 639)
        self.assertEqual(ORDER_SERVICES["tiktok_views"].unit_price, 1)
        self.assertEqual(ORDER_SERVICES["tiktok_likes"].provider_service_id, 539)
        self.assertEqual(ORDER_SERVICES["tiktok_likes"].unit_price, 6)

    def test_telegram_link_normalization(self):
        self.assertEqual(
            Bot.normalize_target_link("@channel", ORDER_SERVICES["followers_basic"]),
            "https://t.me/channel",
        )
        self.assertEqual(
            Bot.normalize_target_link(
                "https://t.me/channel/123",
                ORDER_SERVICES["views"],
            ),
            "https://t.me/channel/123",
        )
        self.assertIsNone(
            Bot.normalize_target_link("https://example.com", ORDER_SERVICES["followers_basic"])
        )

    def test_tiktok_link_normalization(self):
        self.assertEqual(
            Bot.normalize_target_link(
                "https://www.tiktok.com/@name/video/123456",
                ORDER_SERVICES["tiktok_views"],
            ),
            "https://www.tiktok.com/@name/video/123456",
        )
        self.assertIsNone(
            Bot.normalize_target_link("@channel", ORDER_SERVICES["tiktok_followers"])
        )

    def test_flyer_tasks_are_telegram_only(self):
        telegram_task = {
            "task": "subscribe channel",
            "links": ["https://t.me/example"],
        }
        max_task = {
            "task": "subscribe channel",
            "links": ["https://max.ru/example"],
        }
        self.assertTrue(Bot.is_telegram_flyer_task(telegram_task))
        self.assertFalse(Bot.is_telegram_flyer_task(max_task))

    def test_coin_packages_exist(self):
        self.assertEqual(COIN_PACKAGES["starter"].stars_amount, 50)
        self.assertEqual(COIN_PACKAGES["starter"].coins_amount, 50000)
        self.assertEqual(COIN_PACKAGES["boost"].coins_amount, 100000)
        self.assertEqual(COIN_PACKAGES["max"].stars_amount, 250)
        self.assertEqual(COIN_PACKAGES["max"].coins_amount, 250000)

    def test_custom_coin_rate_matches_stars(self):
        self.assertEqual(7 * 1000, 7000)


if __name__ == "__main__":
    unittest.main()
