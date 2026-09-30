# mtproxy-farm

Собственные Telegram MTProxy (FakeTLS) на наших серверах — потому что пул
MT3S Public РКН режет для РФ (проверено дважды: с srv2 хендшейк проходит, у
хозяина из РФ «Status: Unavailable»). Один процесс `mtprotoproxy` (alexbers) на
сервер, **10 пользователей-секретов**, каждый вынесен на свой поддомен
`*.aikort.lol`.

## Раскладка

| Компонент | Значение |
|---|---|
| Софт | `mtprotoproxy.py` (alexbers/mtprotoproxy, вендорнут; пропатчен под Python 3.14 — `asyncio.Task(..., loop=loop)` → `loop.create_task(...)`) |
| srv1 (`2.27.22.150`) | `/opt/mtproxy-farm`, systemd `mtproxy-farm`, порт **8443** |
| srv2 (`31.77.145.160`) | то же, порт **8444** (8443 занят sing-box) |
| Крипто-бэкенд | системный `cryptography` |
| FakeTLS decoy | `swcdn.apple.com` |
| Конфиг | `config.py` (10 `USERS`, `PORT` из `MTPROXY_PORT` в unit) — **не в git**. Шаблон: `config.example.py` |
| Список секретов / ссылок | `PROXIES.md` — **не в git** |

Оба сервера несут **все 10** секретов; поддомен указывает A-записью на «свой»
сервер:

- srv1: `gacha` `mtproto` `vpn` `gay` `nogay`
- srv2: `femboy` `bitch` `publick-mtproto` `free-mtproto` `proxy`

`mtproxy-ddns` при этом снова ведёт только запись `mt` (на пуле MT3S) — если
хозяин когда-нибудь получит private-пул MT3S без блока.

## Установка / обновление

```bash
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'   # иначе Git Bash ломает пути /opt/...
python project/_ops/srv_run.py <srv> 'mkdir -p /opt/mtproxy-farm'
for f in mtprotoproxy.py config.py mtproxy-farm.service deploy.sh; do
  python project/_ops/srv_put_b64.py <srv> project/mtproxy-farm/$f /opt/mtproxy-farm/$f
done
python project/_ops/srv_run.py <srv> 'bash /opt/mtproxy-farm/deploy.sh <PORT>'   # srv1=8443, srv2=8444
```

## DNS-записи

`zone_edit.py` — запускать на srv2 при **остановленном** `mtproxy-ddns`. Ставит
10 статических A-записей (5→srv1, 5→srv2), убирает прежние копии, валидирует
`named-checkzone`, бампит SOA-serial, `rndc reload`. Идемпотентен. Перед этим —
`MTPROXY_RECORD_NAME=mt` в `/opt/mtproxy-ddns/.env`.

## Проверка

```bash
python project/_ops/srv_run.py srv2 'cd /opt/mtproxy-ddns && python3 -c "
from app.health import faketls_ok
print([faketls_ok(\"2.27.22.150\",8443,\"<raw-secret>\",\"swcdn.apple.com\",5,attempts=1) for _ in range(5)])"'
```

## Если один из серверов заблокируют для РФ

Перекинуть его 5 поддоменов на живой сервер: поправить `zone_edit.py` (все имена
на один IP) и прогнать заново. Ферма на обоих серверах несёт все 10 секретов, так
что менять надо только DNS.
