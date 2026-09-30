# AGENTS.md — vpn-panel

Telegram-бот-магазин VPN со встроенной панелью (Python, aiogram 3). Подробности —
`README.md`. Это активная реализация VPN-проекта; `project/vpn-ru/` (Rust) —
только справочный скелет архитектуры, не запускается.

## Быстрый старт для агента

- Код: `app/` (bot / web / provisioner / db / subscription). Точка входа `run.py`.
- Своё venv: `project/vpn-panel/.venv`. Зависимости — `requirements.txt`.
- Конфиг — `.env` (ключ `BOT_TOKEN` относится к боту `@MITVPNROBOT`, не к kiro-bot;
  `.env` намеренно перекрывает переменные окружения).
- Проверка перед любым изменением:
  ```
  .venv\Scripts\python.exe -m py_compile run.py app\*.py app\**\*.py
  .venv\Scripts\python.exe tests\test_core.py
  ```

## Границы и риски

- Провиженинг реально ходит по SSH на чужие серверы (root по паролю из карточки
  сервера) и ставит там софт. Это ядро продукта, отдельного разрешения на сам
  провиженинг не нужно — сервера добавляет владелец через админку бота.
- НЕ провижинить на srv1/srv2/srv3 из `servers/servers.json` — это инфраструктура
  kiro-bot, не VPN-ноды.
- SSH-пароли серверов хранятся в `data/vpn.db` открытым текстом. Не логировать,
  не копировать наружу, не коммитить `data/`.
- Данные пользователей (uuid, sub_token, mtproto secret) — тоже секреты.

## Как это работает (одним абзацем)

БД — источник истины. Конфиг Xray (`/usr/local/etc/xray/config.json`) и
mtprotoproxy (`/opt/mtprotoproxy/config.py`) на каждой ноде — чистая функция от
таблиц `servers` + активных `users`. Любое изменение состава активных юзеров
(триал, покупка, бан, истечение, ротация ссылки) вызывает `provisioner.sync_all()`
→ перегенерация конфигов + рестарт служб на всех active-серверах. Подписка
(`GET /sub/<token>`) стабильна: клиент сам подхватывает новый список серверов.

Спонсорский канал MTProto — таблица `sponsor_channels` (`title`, `username`,
`ad_tag`, `active`). Активная строка (не более одной) добавляет `AD_TAG` в
`mtprotoproxy/config.py` на всех active-серверах; выбор канала в админке
(`/admin → 📣 Спонсор. канал`) триггерит `sync_all()`. Тег берётся у
@MTProxybot (`Attach channel`), панель его только валидирует и раздаёт.
