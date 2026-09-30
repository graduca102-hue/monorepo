from aiogram.fsm.state import State, StatesGroup


class PurchaseState(StatesGroup):
    market_quantity = State()


class DataImpulseState(StatesGroup):
    proxy_count = State()
    topup_amount = State()
    country_search = State()


class StrikeProxyState(StatesGroup):
    proxy_count = State()
    topup_amount = State()
    country_search = State()


class PaymentState(StatesGroup):
    amount = State()


class PromoState(StatesGroup):
    code = State()


class AdminState(StatesGroup):
    setting_value = State()
    add_balance = State()
    create_promo = State()
    broadcast = State()
    product_rename = State()
    service_rename = State()

