# AGENTS.md — join-approver

Отдельный Telegram-бот `@BOtautoprinyatbot` (aiogram 3, long-polling), который
**моментально одобряет заявки на вступление** во всех каналах/группах, где он
добавлен админом с правом «Добавление участников». Одна копия обслуживает
любое число чатов — конфигурации под канал не требуется.

## Деплой

- Прод: **srv1** (2.27.22.150), systemd-юнит `join-approver`, каталог
  `/opt/join-approver`, venv `.venv`, автозапуск включён, `Restart=always`.
- Логи: `journalctl -u join-approver -f`.
- Редеплой: `tar` проекта без `.venv/ data/ __pycache__/` →
  `project/_ops/srv_put_b64.py srv1 <tgz> /root/x.tgz` (SFTP на srv1 не
  работает; запускать с `MSYS_NO_PATHCONV=1`) → распаковать в `/opt/join-approver`
  → `.venv/bin/pip install -r requirements.txt` → `systemctl restart join-approver`.

## Код

- `app/main.py` — polling; явно добавляет `chat_join_request` и `my_chat_member`
  в `allowed_updates` (в дефолт они не входят).
- `app/handlers.py` — `req.approve()` на заявку; учёт чатов; команды
  `/start`, `/id`, `/chats`.
- `app/config.py` — `.env`: `BOT_TOKEN`, `OWNER_ID`, `AUTO_APPROVE`,
  `WELCOME_TEXT`, `ALLOWED_CHAT_IDS`, `APPROVE_DELAY`.
- `app/storage.py` — `data/chats.json`, список известных чатов.
- Тесты: `python -m pytest -q` (локально; на проде pytest не ставится).

Секрет `BOT_TOKEN` — в `.env` (в git не коммитится), не печатать в ответы.
