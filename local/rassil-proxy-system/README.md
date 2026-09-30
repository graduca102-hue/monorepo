# rassil proxy system

Система управления прокси для масс-сендера `/opt/rassil` на **srv1**.

## Что деплоится

| файл | назначение |
|------|-----------|
| `deploy/proxy_manager.py` | пул прокси, health-check, раздача аккаунтам, перевод с мёртвых |
| `deploy/bot.py` | правки: меню «🌐 Прокси», фоновый `_proxy_watch`, статус пула |
| `deploy/proxies.txt` | стартовый пул (28 HTTP-прокси maskify) |
| `push.py` | загрузка файла на сервер base64-чанками через `srv_ssh.py` |

## Деплой (как делалось)

```
python push.py srv1 deploy/proxy_manager.py /opt/rassil/proxy_manager.py
python push.py srv1 deploy/bot.py            /opt/rassil/bot.py
python push.py srv1 deploy/proxies.txt       /opt/rassil/proxies.txt
# на сервере:
cd /opt/rassil
./venv/bin/pip install 'python-socks[asyncio]'   # ОБЯЗАТЕЛЬНО, иначе Telethon игнорит прокси
./venv/bin/python -m py_compile bot.py proxy_manager.py
./venv/bin/python proxy_manager.py check
./venv/bin/python proxy_manager.py assign
systemctl restart rassil-bot
```

Бэкап оригиналов: `/opt/rassil/_bak/proxy_system_20260904_012820/`.

## CLI

```
./venv/bin/python proxy_manager.py check      # health-check всего пула
./venv/bin/python proxy_manager.py assign     # раздать прокси аккаунтам (--force = сбросить старые)
./venv/bin/python proxy_manager.py reassign   # только увести аккаунты с мёртвых прокси
./venv/bin/python proxy_manager.py status     # сводка
```

## Из Telegram-бота

Меню → «🌐 Прокси»: статус пула + кнопки «Проверить», «Переназначить всем»,
«Переназначить битые», «Добавить к пулу», «Очистить». Прислать список прокси
(по одной на строку) — заменит пул, проверит, раздаст, переподключит клиентов
(если рассылка не идёт).

Фон: `_proxy_watch` каждые 10 мин перепроверяет пул и уводит аккаунты с
умерших прокси на живые.
