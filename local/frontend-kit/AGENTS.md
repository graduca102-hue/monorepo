# AGENTS.md — frontend-kit

Общая фронт-база для Telegram mini-app'ов проекта. Часть репозитория `kiro-bot`
— общие правила см. в `../../AGENTS.md` (границы, разрешения, секреты).

## Стек

- **React 18 + Vite + TypeScript.** React закреплён на `^18.3.1` — НЕ поднимать
  до 19: `@telegram-apps/telegram-ui@2` требует peer `react@^18.2.0`.
- **`@telegram-apps/telegram-ui`** (tgui.xelene.me) — компоненты Telegram.
  Оборачивай приложение в `<AppRoot>` (сам определяет платформу и тему).
  CSS подключается в `src/main.tsx`: `@telegram-apps/telegram-ui/dist/styles.css`.
- **Font Awesome** — `@fortawesome/*` (solid + regular + brands) +
  `@fortawesome/react-fontawesome`. Конфиг в `src/lib/fontawesome.ts`:
  `config.autoAddCss = false` (CSS импортируется явно), `library.add(fas, far, fab)`
  для строковых имён (`icon="rocket"`, `icon={['fab','telegram']}`).

## Команды

```
npm run dev      # дев-сервер (HMR)
npm run build    # tsc -b + vite build → dist/
npm run preview
npm run lint     # oxlint
```

## Размер бандла

Демо тянет все три icon-пака целиком (~1.8 МБ). В реальном приложении импортируй
конкретные иконки (`import { faRocket } from '@fortawesome/free-solid-svg-icons'`)
вместо `library.add(fas, far, fab)`.

## impeccable

Дизайн-скилл `impeccable` установлен глобально; хуки дизайн-детектора —
локально в `.claude/settings.local.json` этого проекта (проверка UI после
Edit/Write + глубокий проход на Stop). Инициализация дизайн-контекста —
`/impeccable init` в чате.

## Существующие мини-аппы

`project/sous-market-debug/` и `project/botshop-speed/miniapp.py` — сейчас на
чистом HTML/CSS/JS, не на React. Переезд на этот кит — отдельная задача, только
по явной просьбе владельца.
