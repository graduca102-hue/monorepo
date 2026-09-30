# Proxy-only bot

This is a standalone Telegram polling process that reuses BotShop's shared
users, balance, payment providers and proxy delivery handlers. Its public
navigation exposes proxy purchases only and does not expose the catalogue or
franchise screens. Users, balances, orders and admin statistics are stored in
the module's own `data.db`; Maskify allocations use its own `maskify_users.db`.

Copy `.env.example` to `.env`, set `PROXY_ONLY_BOT_TOKEN`, then start it from
the repository root:

```powershell
python -m proxy_bot.app
```
