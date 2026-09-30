# frontend-kit

Общая фронт-база для Telegram mini-app'ов проекта: **React 18 + Vite + TypeScript**,
UI-кит Telegram и Font Awesome, плюс дизайн-скилл impeccable.

## Стек

| Слой | Пакет | Зачем |
|---|---|---|
| UI-компоненты | [`@telegram-apps/telegram-ui`](https://tgui.xelene.me) | нативно выглядящие элементы Telegram (Cell, Section, Button, List, Placeholder…), авто-тема и платформа через `<AppRoot>` |
| Иконки | `@fortawesome/*` (solid / regular / brands + `react-fontawesome`) | иконки по строковому имени: `<FontAwesomeIcon icon="rocket" />`, `icon={['fab','telegram']}` |
| Дизайн-ассистент | [impeccable](https://impeccable.style) | скилл + хуки для ревью/полировки UI (`/impeccable init`, затем `/polish`, `/audit`, …) |

React закреплён на 18.x — `telegram-ui@2` требует React 18 (peer `^18.2.0`).

## Команды

```bash
npm run dev      # дев-сервер с HMR
npm run build    # tsc -b + vite build → dist/
npm run preview  # предпросмотр собранной версии
npm run lint     # oxlint
```

## Точки подключения

- `src/lib/fontawesome.ts` — конфиг Font Awesome: отключает рантайм-инъекцию `<style>`
  (`config.autoAddCss = false`) и регистрирует паки в `library`. Импортируется один раз из `main.tsx`.
- `src/main.tsx` — подключает `@telegram-apps/telegram-ui/dist/styles.css` и `./lib/fontawesome`.
- `src/App.tsx` — демо: `<AppRoot>` + компоненты telegram-ui + иконки FA.

## Размер бандла

Демо тянет все три icon-пака целиком (~1.8 МБ). В реальном приложении импортируй
конкретные иконки вместо `library.add(fas, far, fab)`:

```ts
import { faRocket } from '@fortawesome/free-solid-svg-icons'
// <FontAwesomeIcon icon={faRocket} />
```

## impeccable

Скиллы/агенты поставлены глобально (`~/.claude/skills/impeccable`, общий движок),
хуки — локально в `.claude/settings.local.json` этого проекта (проверка UI после
Edit/Write и глубокий проход на Stop). Инициализация дизайн-контекста:
`/impeccable init` в чате AI-ассистента.
