# support-ai — ИИ-поддержка SOUS MARKET

ИИ-агент первого уровня для магазина. Читает `project/support-faq/system_prompt.md`
+ `project/support-faq/faq.md`, отвечает на типовые вопросы клиентов, а
сложные (возвраты, замены, зависшие заказы, отсутствие ответа в базе)
эскалирует живому оператору.

Модель — **DeepSeek** (`deepseek-chat`, V3) через OpenAI-совместимый
эндпоинт `https://api.deepseek.com`. Дёшево, быстро, поддерживает
function/tool calling — то, что нужно под эскалацию.

## Как устроено

- `ai_agent.py` — `SupportAgent`: обёртка над DeepSeek через `AsyncOpenAI`
  с кастомным `base_url`.
  - Загружает системный промпт и подставляет `faq.md` вместо плейсхолдера.
  - Хранит историю сообщений по `user_id` (в памяти, последние 12 ходов).
  - Регистрирует у модели один tool `escalate_to_operator` — модель сама решает
    когда его вызвать по правилам из блока `<escalation>` промпта.
  - Возвращает `Reply(text, escalation)`: `text` уходит клиенту, `escalation`
    (если есть) содержит `section / order_id / summary / checked / missing`
    для оператора.
- `bot.py` — standalone Telegram-бот на aiogram. Каждое текстовое сообщение
  клиента → `SupportAgent.reply` → ответ клиенту. Если модель решила
  эскалировать, бот дополнительно постит в `OPERATOR_CHAT_ID` строку
  `user_id · order · раздел · суть · проверено · не хватает` и форвардит
  исходное сообщение клиента. Вложения (фото, .txt, скриншоты) сразу уходят
  оператору — модель их не видит по соображениям prompt-injection.
- `cli.py` — консольный тестер, чтобы поговорить с агентом без телеграма.

## Первый запуск

```bash
cd project/support-ai
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # Linux
pip install -r requirements.txt
copy .env.example .env
# отредактировать .env: BOT_TOKEN, OPERATOR_CHAT_ID, ANTHROPIC_API_KEY
python cli.py             # проверить логику без телеграма
python bot.py             # запустить бота
```

## Переменные окружения

| Ключ | Что | Дефолт |
|---|---|---|
| `BOT_TOKEN` | токен бота от @BotFather | — |
| `OPERATOR_CHAT_ID` | куда бот шлёт эскалации (user_id оператора или id группы, где бот админ) | — |
| `DEEPSEEK_API_KEY` | ключ DeepSeek (https://platform.deepseek.com/api_keys) | — |
| `DEEPSEEK_BASE_URL` | эндпоинт API | `https://api.deepseek.com` |
| `SUPPORT_AI_MODEL` | id модели | `deepseek-chat` |
| `SUPPORT_AI_MAX_TOKENS` | лимит ответа | `800` |
| `SUPPORT_AI_TEMPERATURE` | температура сэмплинга | `0.2` |
| `SUPPORT_AI_HISTORY_TURNS` | ходов в памяти на пользователя | `12` |
| `LOG_LEVEL` | уровень логов | `INFO` |
| `CLI_USER_ID` | id пользователя для `cli.py` | `1` |

## Модель

По умолчанию **`deepseek-chat`** (DeepSeek V3) — быстро и очень дёшево,
поддерживает function calling, подходит под тикеты в 1–5 сообщений.
`deepseek-reasoner` (R1) — сильнее в рассуждениях, но **не поддерживает tool
calling**, поэтому эскалация не сработает; для этого агента брать не стоит.
Если хочется больше стойкости к джейлбрейкам — поднимаем `max_tokens`,
понижаем `temperature` до `0.1`, либо переезжаем на другой провайдер.

## Как эскалирует

1. Модель вызывает tool `escalate_to_operator` с полями `section`, `order_id`,
   `summary`, `checked`, `missing` — это описано в её же system prompt
   (`<escalation>`).
2. `ai_agent.SupportAgent` возвращает `Reply.escalation` как dict.
3. `bot.py` формирует строку оператору
   `format_operator_line(...)` и постит её в `OPERATOR_CHAT_ID`, следом
   форвардит исходное сообщение клиента.
4. Клиенту уходит либо текст модели (обычно «Передаю оператору. Ответ придёт
   в этот чат.»), либо, если модель промолчала, — эта фраза дословно.

Модель НЕ имеет доступа ни к каким инструментам кроме `escalate` — не читает
БД заказов, не создаёт возвраты, не пишет ничего в telegram. Всё, что может
случиться от её действий: сообщение клиенту + строка оператору.

## Куда встраивать

Сейчас это отдельный процесс. Логичная интеграция дальше:

- Прицепить к существующему тикет-боту `@UniversallSupportBot` на srv2 —
  первое сообщение клиента идёт в ИИ, при вызове `escalate` создаётся
  обычный тикет.
- Или использовать как pre-filter перед созданием тикета в SOUS MARKET
  (`/opt/botshop`): клиент открыл раздел «Поддержка» → бот-агент → если
  не решил, создаёт `support_tickets`-запись как раньше.

Обе интеграции — отдельная задача; сейчас у нас автономный бот, который
можно запустить и проверить на живых клиентах или в тестовом чате.

## Ограничения

- История в памяти — при рестарте контекст диалогов теряется. Для прода
  добавить SQLite-хранилище (`sessions.db`, поля `user_id, messages_json,
  updated_at`).
- Вложения не анализируются (специально: prompt-injection через картинку).
  Всё, что не текст, форвардится оператору.
- Rate-limit / антиспам не реализован — если понадобится, стандартный
  aiogram middleware на N сообщений в минуту.
- `DEEPSEEK_API_KEY` стоит хранить не в `.env` рядом с кодом, а в системе
  (systemd `EnvironmentFile=` или Windows env). Для прода на srv2 —
  добавить в `/etc/support-ai.env` с правами `600`.
