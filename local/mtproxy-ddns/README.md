# mtproxy-ddns

Держит **набор A-записей** в зоне `aikort.lol` на **живом** IP MTProxy от сервиса
**MT3S Public** (`@confmtbot` / `api.confmtbot.com`). Крутится на **srv2** рядом с
BIND.

Имена задаются в `MTPROXY_RECORD_NAME` — одно имя или список через запятую (все
ведутся на один IP листенера, первое имя каноническое). Форс-реконсиляция: имя,
добавленное в список, получает запись сразу, не дожидаясь ротации IP MT3S.

**Сейчас (`.env` на srv2):** `MTPROXY_HEALTHCHECK=off`, `MTPROXY_RUCHECK=off`,
`MTPROXY_RECORD_NAME=` `mt` + 10 поддоменов `gacha,mtproto,vpn,gay,nogay,femboy,`
`bitch,publick-mtproto,free-mtproto,proxy`. Запасная ферма
`project/mtproxy-farm/` — `disabled`.

**DNS переехал на Cloudflare (2026-09-11).** Собственные NS (BIND на srv2,
делегирование через `sslip.io` без glue) периодически не отвечали ("DNS request
timed out"). Теперь `aikort.lol` — зона на Cloudflare (NS `leif.ns.cloudflare.com`
/ `rosalyn.ns.cloudflare.com`, `CF_ZONE_ID` в `.env`), `mtproxy-ddns` пишет A-записи
через Cloudflare API (`MTPROXY_DNS_BACKEND=cloudflare`). Зона `aikort.lol` убрана
из BIND на srv2 (там остались только `sousmarket*`). `bind`/`both` в
`MTPROXY_DNS_BACKEND` — на случай, если понадобится снова свой NS.

## Как работает

Каждый цикл:

1. `GET https://api.confmtbot.com/public-listener` с Bearer-токеном → `ipv4`.
2. **Local health-check** (`MTPROXY_HEALTHCHECK`): `tcp` — проходит ли TCP `:443`;
   `faketls` — плюс настоящий MTProto FakeTLS-хендшейк секретом из `.env`;
   `off` — просто зеркалить листенер. Бьётся с srv2 (заграница) — ловит мёртвые
   IP пула, но блок РКН «только для РФ» не видит.
3. **RU-check** (`MTPROXY_RUCHECK=on`): просит check-host.net открыть TCP к IP
   `:443` со своих российских нод (Москва/Питер). Если не достучались — IP
   считается заблокированным для РФ. Результат кешируется на
   `MTPROXY_RUCHECK_INTERVAL` секунд. Неопределённый ответ (API недоступен) не
   считается блоком.
4. В DNS уходит только IP, прошедший обе проверки. Если текущий IP выглядит
   заблокированным — апдейтер учащает опрос (`POLL_SECONDS_BLOCKED`, 3 с) и
   перебирает листенер (`PROBE_RETRIES`), пока MT3S не отдаст IP, доступный из РФ
   (в пуле MT3S есть и такие — напр. `201.4.76.x` против режущихся `201.7.22.x`).
5. Если живого IP нет нигде — запись **не трогаем**, лог `ERROR`, и (если задан
   `MTPROXY_TG_BOT_TOKEN`/`CHAT_ID`) через `ALERT_AFTER_SECONDS` — алерт в Telegram.

Правка зоны зависит от `MTPROXY_DNS_BACKEND`:
- `bind` — `named-checkzone` → bump SOA-serial → `rndc reload aikort.lol` в
  локальном zone-файле srv2;
- `cloudflare` — PUT A-записей через Cloudflare API (grey-cloud, `proxied:false`),
  id записей кешируются, `all_match` не дёргает API если IP не менялся;
- `both` — оба (на время переезда NS). Ошибка Cloudflare в режиме `both` не
  роняет цикл — BIND остаётся основным.

TTL записи — 60 с (Cloudflare не принимает меньше; в инструкции MT3S Public — 60).

## Установка / обновление (srv2)

```bash
scp -r project/mtproxy-ddns root@SRV2:/opt/mtproxy-ddns   # .env с MT3S_API_TOKEN
ssh root@SRV2 'bash /opt/mtproxy-ddns/deploy/install.sh'  # первый раз
# обновление кода:
ssh root@SRV2 'cd /opt/mtproxy-ddns && systemctl restart mtproxy-ddns'
```

Логи: `journalctl -u mtproxy-ddns -f`

## Конфиг (`.env`) — ключевое

| Переменная | По умолч. | Смысл |
|---|---|---|
| `MT3S_API_TOKEN` | — | Merchant API token (обязателен, только в `.env`) |
| `MTPROXY_DNS_BACKEND` | `bind` | `bind` / `cloudflare` / `both` |
| `CF_API_TOKEN` | — | Cloudflare-токен Zone.DNS Edit (нужен для `cloudflare`/`both`, только в `.env`) |
| `CF_ZONE_ID` | — | id зоны в Cloudflare; пусто → поиск по имени `MTPROXY_ZONE` |
| `MTPROXY_RECORD_NAME` | `mt` | имя записи или список имён через запятую |
| `MTPROXY_HEALTHCHECK` | `tcp` | `tcp` / `faketls` / `off` |
| `MTPROXY_SECRET_HEX` | — | raw 32-hex секрет (нужен для `faketls`) |
| `MTPROXY_FAKETLS_SNI` | `xapi.ozon.ru` | SNI из FakeTLS-секрета |
| `MTPROXY_POLL_SECONDS` / `_BLOCKED` | `10` / `3` | опрос обычный / когда текущий IP заблокирован |
| `MTPROXY_PROBE_RETRIES` | `6` | перезапросов листенера в поиске рабочего IP |
| `MTPROXY_RECORD_TTL` | `60` | TTL A-записи |
| `MTPROXY_RUCHECK` | `off` | `on` — проверять доступность IP из РФ (check-host.net) |
| `MTPROXY_RUCHECK_NODES` | `ru2…,ru3…` | RU-ноды check-host.net через запятую |
| `MTPROXY_RUCHECK_INTERVAL` | `240` | кеш вердикта по IP, сек |
| `MTPROXY_RUCHECK_REQUIRE_ALL` / `_MIN_OK` | `true` / `1` | все ответившие ноды / хотя бы N |
| `MTPROXY_TG_BOT_TOKEN` / `_CHAT_ID` | — | алерт в Telegram при затяжном простое |

## Ссылки для пользователей

Один общий бэкенд MT3S (raw-секрет `3c0c8c099abd558ceaab5f5187cbc83e`, порт 443),
любой из поддоменов `mt gacha mtproto vpn gay nogay femboy bitch publick-mtproto
free-mtproto proxy`.

**Рабочий вариант (проверено хозяином из РФ) — `dd` (secure, без FakeTLS):**

```
tg://proxy?server=<sub>.aikort.lol&port=443&secret=dd3c0c8c099abd558ceaab5f5187cbc83e
```

FakeTLS-вариант (`ee…786170692e6f7a6f6e2e7275`, SNI `xapi.ozon.ru`) у клиента
хозяина не заработал — оставлен как запасной.

`<sub>` ∈ `mt gacha mtproto vpn gay nogay femboy bitch publick-mtproto free-mtproto proxy`.

## Тесты

```bash
cd project/mtproxy-ddns && python -m pytest -q
```
