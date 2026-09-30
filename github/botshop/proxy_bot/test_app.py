import unittest

from proxy_bot.app import (
    MODULE_DIR,
    PROXY_ONLY_DATABASE_PATH,
    PRIVACY_URL,
    PROXY_ONLY_RESIDENTIAL_PRICE_USD,
    PROXY_ONLY_MASKIFY_DATABASE_PATH,
    USAGE_URL,
    build_proxy_only_inline_menu,
    build_proxy_only_reply_menu,
    buy_proxy_label,
    is_blocked_callback,
    remove_blocked_buttons,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ProxyOnlyBotTests(unittest.TestCase):
    def test_reply_menu_is_proxy_only(self):
        labels = [
            button.text
            for row in build_proxy_only_reply_menu(1, "ru").keyboard
            for button in row
        ]
        self.assertEqual(labels[0], "Купить прокси")
        self.assertNotIn("Каталог товара", labels)
        self.assertFalse(any("франш" in label.casefold() for label in labels))

    def test_inline_menu_is_proxy_only(self):
        markup = build_proxy_only_inline_menu("ru")
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("proxy_test:home", callbacks)
        self.assertNotIn("magazine", callbacks)
        self.assertNotIn("partner_program", callbacks)

    def test_legacy_routes_are_blocked_but_proxy_checkout_is_not(self):
        self.assertTrue(is_blocked_callback("market_category:75"))
        self.assertTrue(is_blocked_callback("partner_program"))
        self.assertTrue(is_blocked_callback("partner_bot_invite"))
        self.assertFalse(is_blocked_callback("partner_proxy_balance:abc:10"))
        self.assertFalse(is_blocked_callback("proxy_test:service:abc"))

    def test_buy_proxy_label_is_localized(self):
        self.assertEqual(buy_proxy_label("ru"), "Купить прокси")
        self.assertEqual(buy_proxy_label("en"), "Buy proxy")

    def test_residential_price_is_proxy_only_value(self):
        self.assertEqual(PROXY_ONLY_RESIDENTIAL_PRICE_USD, 0.6)

    def test_reply_menu_buttons_have_premium_icons(self):
        buttons = [
            button
            for row in build_proxy_only_reply_menu(1, "ru").keyboard
            for button in row
        ]
        self.assertTrue(all(button.icon_custom_emoji_id for button in buttons))

    def test_customer_databases_are_module_local(self):
        self.assertEqual(PROXY_ONLY_DATABASE_PATH.parent, MODULE_DIR)
        self.assertEqual(PROXY_ONLY_MASKIFY_DATABASE_PATH.parent, MODULE_DIR)
        self.assertNotEqual(PROXY_ONLY_DATABASE_PATH.name, "data1.db")

    def test_franchise_button_is_removed_from_admin_markup(self):
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пользователи", callback_data="admin_users")],
                [InlineKeyboardButton(text="Франшизы", callback_data="admin_franchises")],
            ]
        )
        filtered = remove_blocked_buttons(markup)
        callbacks = [button.callback_data for row in filtered.inline_keyboard for button in row]
        self.assertEqual(callbacks, ["admin_users"])

    def test_information_has_usage_and_privacy_urls(self):
        self.assertEqual(
            USAGE_URL,
            "https://telegra.ph/Polzovatelskoe-soglashenie-08-22-52",
        )
        self.assertEqual(
            PRIVACY_URL,
            "https://telegra.ph/Politika-konfidencialnosti-08-22-78",
        )


if __name__ == "__main__":
    unittest.main()
