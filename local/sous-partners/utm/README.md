# UTM-ссылки в кабинете SousPartners

Экран «UTM-ссылки» в мини-апп кабинете партнёра (`/opt/botshop/partner_static/`).
Раньше UTM-ссылки были только в inline-меню бота (`keyboard.py`,
`partner_bot_utm:*`), в кабинете их не было.

Задеплоено на srv2 21.09.2026 (бэкапы `/opt/botshop/backups/partner-utm-20260921_175015`
и `…_175405`, локально — `project/_backups/20260921_175015/sous-partners-utm/`).

## Что добавлено

| Файл | Изменение |
|---|---|
| `database/data.py` | `delete_partner_bot_utm_link(bot_id, source_word)` — единственное, чего не хватало (создание/чтение уже были) |
| `miniapp.py` | `GET/POST /partner/{slug}/api/utm`, константы `PARTNER_UTM_LINK_LIMIT=20`, `PARTNER_UTM_STATS_LIMIT=100`, хелпер `normalize_partner_utm_source` |
| `partner_static/app.js` | экран `utm`: создание, копирование, удаление ссылки, статистика по каждой и блок «Откуда приходят» |
| `partner_static/styles.css` | `.inline-btn.ghost` (кнопка «Удалить») |

Ссылка строится как `https://t.me/<бот>?start=utm_<источник>` — этот формат уже
разбирает `tracking.parse_start_tracking`, а переходы пишутся в
`partner_bot_starts`, поэтому счётчики появляются сами. Выручка по ссылке —
сумма `orders.total_price` в статусах `delivered`/`credited` по пользователям,
пришедшим с этим `start_param`.

Имя источника нормализуется в латиницу/цифры/дефис, **подчёркивания заменяются
на дефис**: `parse_start_tracking` считает `utm_<источник>` источником только
пока в payload один `_`, иначе строка попала бы в статистику целиком.

## Как воспроизвести/обновить

`patch.py` берёт эталонные файлы из `../live/` (скачаны с сервера) и кладёт
пропатченные рядом с собой; переносы строк сохраняются как в оригинале
(на сервере `.py` — CRLF, `partner_static/*` — LF).

```
python patch.py                       # собрать пропатченные файлы
node --check app.js && python -m py_compile miniapp.py data.py
```

Деплой — `deploy_utm.sh <ts>` на сервере: бэкап → `py_compile` → подмена →
рестарт `botshop` → проверка (`is-active`, `/partner/souspartnersbot/` = 200,
`/api/utm` ≠ 404, нет свежих Traceback) → авто-откат, если что-то не так.

## Превью без сервера

`../new/preview.html` умеет мокать `/api/utm` (кнопка `utm` в панели сверху):

```bash
cd project/sous-partners/new && python -m http.server 8777 --bind 127.0.0.1
```
