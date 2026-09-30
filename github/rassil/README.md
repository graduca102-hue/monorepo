# LZT Telegram buyer

Searches `GET https://api.lzt.market/telegram`, buys matching listings through
`POST /{item_id}/fast-buy`, and writes each completed API response to
`accounts/<item_id>.json`.

## Setup

1. Copy `config.example.json` to `config.json` and set a strict price limit,
   item limit, currency, and optional Telegram search filters.
2. Create an LZT access token with the `market` scope and set it only in the
   process environment:

   ```powershell
   $env:LZT_TOKEN = 'your-token'
   ```

3. Test the selection without spending money:

   ```powershell
   python .\lzt_telegram_buyer.py --config .\config.json --dry-run
   ```

4. After reviewing the dry run, set `"auto_buy": true` in `config.json` and run:

   ```powershell
   python .\lzt_telegram_buyer.py --config .\config.json
   ```

The script will never purchase more than `max_items_per_run` per invocation,
does not re-buy an item already stored in `accounts/`, retries temporary HTTP
failures, and handles the API's `retry_request` responses.

`accounts/` is ignored by Git because it may contain sensitive purchase data.
