# Фаза 1 — «Тексты и кнопки» в кабинете SousPartners

Взято из Hex (`hexpartners.pro/vpn/app/texts`). Форм-фактор — остаётся Telegram
Mini App, фича добавляется **поверх** уже отредизайненного кабинета
(`/opt/botshop/partner_static/`).

## Как устроены партнёрские боты (факты с srv2)

- `partner_runtime.py` поллит каждого партнёрского бота и кормит апдейты в **тот
  же общий `Dispatcher`**, что и основной botshop. То есть партнёрские боты
  выполняют те же хендлеры (`main.py`, `keyboard.py`, `miniapp.py`), просто с
  другим `Bot`.
- Тема партнёра резолвится на лету: `get_current_partner_bot(bot)` в `main.py`
  возвращает строку `partners_bots` по токену бота, получившего апдейт.
- Тексты клиенту идут через `i18n.tr(lang, key, **kw)` —
  `TRANSLATIONS[lang][key].format(**kw)`. Приветствие — `keyboard.build_welcome_text()`
  (`welcome.text`). Уже есть прецедент партнёро-зависимого текста:
  `build_partner_subscription_gate_text(partner_bot, lang)`.
- Таблица партнёрских ботов называется **`partners_bots`** (не `partner_bots`),
  50 строк. БД — `/opt/botshop/data1.db` (WAL).

## Что делаем

### 1. БД (`database/data.py`)
Новая таблица:
```sql
CREATE TABLE IF NOT EXISTS partner_bot_texts (
  partner_bot_id INTEGER NOT NULL,
  text_key       TEXT    NOT NULL,
  value          TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  PRIMARY KEY (partner_bot_id, text_key)
);
```
Аксессоры: `get_partner_bot_texts(partner_bot_id) -> dict[str,str]` (кэш с TTL
~60 c, как у остальных партнёрских данных), `set_partner_bot_text(id, key, value)`,
`delete_partner_bot_text(id, key)`.

### 2. Резолвер текста (новый модуль `partner_texts.py`, ~40 строк)
```python
partner_tr(bot, lang, key, **kw) -> str
```
Если `get_current_partner_bot(bot)` не None и есть оверрайд для `key` —
`value.format(**kw)` (с safe-format: недостающие плейсхолдеры не роняют),
иначе `i18n.tr(lang, key, **kw)`.

Оверрайд **один на ключ, без языков** — показывается любому клиенту независимо
от его языка (малому партнёру так проще; мультиязычность — не сейчас).

### 3. Точечная замена call-site'ов (~12 шт.)
Только курируемый набор — «голос» бота, не весь `i18n` (там сотни ключей):

| Ключ | Что это | Плейсхолдеры |
|---|---|---|
| `welcome.text` | приветствие `/start` | brand |
| `menu.anchor` | подпись под меню | — |
| `menu.goods` / `menu.proxy` / `menu.profile` / `menu.support` / `menu.language` | подписи кнопок меню | — |
| `menu.partner_program` | кнопка «партнёрская программа» | — |
| `generic.subscription_required` | текст гейта подписки | — |
| `common.to_menu` / `common.pay` | ключевые кнопки | — |
| `balance.payment_confirmed` | успех оплаты | — |

`build_welcome_text()` и эти `tr(...)` в `main.py`/`keyboard.py` → `partner_tr(bot, ...)`.
Замена аддитивная: для основного бота (`partner_bot is None`) поведение 1:1.

### 4. API (`miniapp.py`, рядом с `handle_partner_*`)
- `GET  /partner/{slug}/api/texts` → `{items:[{key,label,hint,default,value,multiline}], brand}`
- `POST /partner/{slug}/api/texts`  `{key, value}` — длина ≤ 1200, проверка что
  плейсхолдеры из дефолта не потеряны (предупреждение, не блок)
- `POST /partner/{slug}/api/texts`  `{key, reset:true}` — снять оверрайд

### 5. UI кабинета (`partner_static/app.js` + `styles.css`)
Новый экран `texts` в текущем редизайне:
- в `overview()` в группе «Настройки бота» — ячейка «Тексты и кнопки» (иконка `pen`/`font`)
- экран: группированный список блоков; строка = метка + превью значения + `›`
- тап → редактор: `<textarea>` (или `<input>` для кнопок), чипы-плейсхолдеры,
  кнопка «Сбросить к стандартному», MainButton «Сохранить»
- бейдж «изменено» на редактированных

### 6. Кнопки (inline-строки)
Фаза 1 = только **подписи** штатных кнопок меню (те же `menu.*` ключи).
Произвольные кнопки (добавить/удалить/URL) — фаза 1b, отдельно.

## Риск и деплой

Правки `main.py` + `keyboard.py` + `miniapp.py` + `database/data.py` = **горячий
код живого botshop**. Протокол:
1. Всё собрать и обкатать на копии в `project\sous-partners\botshop-src\`.
2. Бэкап каждого файла → `/opt/botshop/backups/partner-texts-<ts>/`.
3. `.venv/bin/python -m py_compile` всех тронутых файлов.
4. Миграция таблицы — отдельным шагом, идемпотентно (`CREATE TABLE IF NOT EXISTS`).
5. Рестарт через таймер-обёртку: ждёт 25 c → `systemctl restart botshop` →
   через 60 c проверяет `is-active` + `curl 127.0.0.1:8081` + отсутствие свежего
   `Traceback` в логе → иначе восстанавливает бэкап и рестартит.
6. Отчёт хозяину до и после.

`partner_static/*` (UI) — как обычно, без рестарта.

## Оценка
Backend (data.py + partner_texts.py + 12 call-sites + API): ~1 подход.
UI (экран + редактор): ~1 подход. Деплой с обкаткой: отдельно.
