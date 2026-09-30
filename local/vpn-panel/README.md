# vpn-panel

Telegram-бот-магазин VPN **со встроенной панелью**. Отдельная self-hosted панель
(Remnawave/Marzban) не нужна — бот сам ставит и настраивает серверы, ведёт
пользователей, отдаёт подписку.

## Что делает

- **Пользователь:** `/start` → меню `🎁 Триал · 👤 Профиль · ⚙️ Управление
  подпиской · 💳 Пополнить баланс`. Один ключ на все серверы и протоколы,
  подписка в Happ / v2rayNG / Hiddify / Streisand + `happ://` deep-link + MTProto
  для Telegram.
- **Админ** (`ADMIN_IDS`): `/admin` → серверы, пользователи, тарифы, статистика,
  рассылка, спонсорский канал. Добавление сервера — вставляешь
  `host login password name` (по одной строке на сервер), бот сам по SSH ставит
  Xray-core (VLESS + REALITY + Vision) и mtprotoproxy, генерит REALITY-ключи,
  раскатывает конфиг со всеми активными юзерами и поднимает службы.
- **Спонсорский канал (MTProto):** `/admin → 📣 Спонсор. канал`. Экран показывает
  готовые данные для регистрации прокси в @MTProxybot (сервер/порт/секрет) и
  публичную `tg://proxy`-ссылку. Постоянный «house»-секрет
  (`settings.mtproto_house_secret`) всегда есть в `USERS` как `"house"` —
  бесплатный публичный доступ без срока. Добавляешь каналы строкой
  `Название | @канал | тег` (тег — 32 hex от @MTProxybot после `Attach channel`),
  выбираешь активный — панель пишет `AD_TAG` в `mtprotoproxy/config.py` на всех
  активных серверах и рестартит службу. Канал показывается закреплённым всем, кто
  подключился через прокси. «Выключить спонсорство» убирает `AD_TAG`.
- **Оплата:** Telegram Stars (без провайдера и эквайринга) — пополнение баланса
  и прямая покупка тарифа. С баланса оплачивается любой тариф.
- **Синхронизация:** конфиг каждого сервера — чистая функция от БД. Любое
  изменение (новый триал, покупка, бан, истечение, смена ссылки) триггерит
  переกатку конфигов на все активные серверы и рестарт служб.

## Архитектура

```
пользователь ──Telegram──▶ bot (aiogram)  ─┐
                                            ├─ SQLite (источник истины)
клиент VPN ──HTTP GET /sub/<token>──▶ web  ─┘
                    │
                    ▼ (SSH, при изменениях)
             VPS: Xray-core :443 (VLESS-Reality)  +  mtprotoproxy :8443
```

| Файл | Роль |
|---|---|
| `run.py` | точка входа: web + бот в одном процессе |
| `app/config.py` | конфиг из `.env` (файл имеет приоритет над окружением) |
| `app/db.py` | SQLite: users / servers / plans / txns / settings / sponsor_channels |
| `app/keys.py` | per-user секреты (uuid, shortId, mtproto secret, sub_token) |
| `app/subscription.py` | рендер подписки (v2ray-b64 / sing-box / clash), `happ://`, MTProto-линки |
| `app/provisioner/` | SSH, bootstrap сервера, рендер `xray/config.json` и `mtprotoproxy/config.py`, sync |
| `app/web/server.py` | `GET /sub/<token>` c UA-сниффингом и Happ-заголовками |
| `app/bot/` | `user.py` (меню), `payments.py` (Stars), `admin.py` (панель) |

## Запуск (dev)

```powershell
cd C:\Users\ewg\Desktop\kiro-bot\project\vpn-panel
copy .env.example .env   # заполнить BOT_TOKEN, ADMIN_IDS, SUB_BASE_URL
.\.venv\Scripts\python.exe run.py
```

Тесты офлайн: `.\.venv\Scripts\python.exe tests\test_core.py`
Живой смоук (нужна сеть): `.\.venv\Scripts\python.exe tests\smoke_live.py`

## Прод

`SUB_BASE_URL` должен быть публичным HTTPS-доменом, указывающим на этот процесс
(порт `WEB_PORT`). Домен — за Cloudflare/DDoS-Guard, чтобы скрыть origin.
Разворачивание на VPS — `ops/` (systemd-юнит + `deploy.sh`).

## Что требует внимания

- **SSH-пароли серверов лежат в SQLite открытым текстом.** Вся БД
  (`data/vpn.db`) — чувствительный файл, права `600`, бэкапить отдельно.
- Провиженинг протестирован только на Debian/Ubuntu (apt) и частично на
  dnf-дистрибутивах. Xray и mtprotoproxy ставятся официальным скриптом и из
  git — нужен исходящий интернет на сервере.
- Xray на :443 и mtprotoproxy на :8443 по умолчанию; порт правится в карточке
  сервера до провиженинга (или в `.env` глобально).
- Учёт трафика (`traffic_used_bytes`) пока не собирается с нод — поле есть,
  апдейтера нет. Лимиты по времени работают, по трафику — нет.
