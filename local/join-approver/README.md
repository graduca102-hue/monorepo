# join-approver

Telegram-бот, который **моментально одобряет заявки на вступление** во всех
каналах и группах, где он добавлен админом. Одна копия бота обслуживает
сколько угодно чатов одновременно.

## Как работает

Telegram шлёт боту апдейт `chat_join_request` в момент, когда человек нажимает
«Подать заявку». Бот сразу вызывает `approveChatJoinRequest` — пользователь
попадает в чат без ручной модерации.

## Настройка чата

1. Создай бота у [@BotFather](https://t.me/BotFather), получи `BOT_TOKEN`.
2. Добавь бота в канал/группу **администратором** с правом
   **«Добавление участников»** (Add members / Invite users).
3. В настройках чата включи **«Заявки на вступление»**
   (для каналов/групп с публичной ссылкой или через ссылку-приглашение с
   «Требовать одобрения»).

Больше ничего настраивать не нужно — бот работает сразу во всех таких чатах.

## Запуск локально

```bash
cd project/join-approver
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env    # впиши BOT_TOKEN
.venv/Scripts/python -m app.main
```

## Конфиг (`.env`)

| Переменная | Смысл |
|---|---|
| `BOT_TOKEN` | токен от @BotFather (обязательно) |
| `OWNER_ID` | ID для уведомлений об одобренных заявках, `0` = выкл |
| `AUTO_APPROVE` | `1` одобрять моментально, `0` — только логировать |
| `WELCOME_TEXT` | приветствие одобрённому в ЛС (пусто = не слать) |
| `ALLOWED_CHAT_IDS` | список `chat_id` через запятую; пусто = все чаты |
| `APPROVE_DELAY` | пауза перед одобрением, сек (`0` = моментально) |

Узнать `chat_id`: добавить бота в чат и отправить `/id`, либо переслать
сообщение из канала боту [@userinfobot](https://t.me/userinfobot).

## Деплой на сервер (systemd)

```bash
scp -r project/join-approver root@SRV:/opt/join-approver
ssh root@SRV 'cd /opt/join-approver && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
# положить .env в /opt/join-approver/.env
ssh root@SRV 'cp /opt/join-approver/deploy/join-approver.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now join-approver'
```

Логи: `journalctl -u join-approver -f`

## Тесты

```bash
cd project/join-approver && python -m pytest -q
```

## Команды бота

- `/start`, `/help` — статус и инструкция
- `/id` — показать `chat_id` текущего чата
