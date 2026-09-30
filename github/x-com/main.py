"""
x.com request sniffer — Playwright + Frida
Открывает x.com в Chromium через Playwright, аттачит Frida к процессу
браузера и собирает все сетевые запросы через хуки (hooks.js).

Использование:
    python main.py
    python main.py --headless        # без окна
    python main.py --timeout 120     # работать 120 секунд
    python main.py --output log.json # сохранить лог в файл
"""

import argparse
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

import frida
from playwright.sync_api import sync_playwright


HOOKS_JS = Path(__file__).parent / "hooks.js"
COLLECTED = []          # все сообщения от Frida
LOCK = threading.Lock()


# ─── Frida callback ───────────────────────────────────────────────
def on_message(message, data):
    """Обработка сообщений от Frida-скрипта."""
    if message["type"] == "send":
        payload = message["payload"]
        ts = datetime.now(timezone.utc).isoformat()

        with LOCK:
            COLLECTED.append({"ts": ts, **payload})

        kind = payload.get("type", "?")
        if kind == "info":
            print(f"  [frida] {payload.get('msg', '')}")
        elif kind == "error":
            print(f"  [frida ERROR] {payload.get('msg', '')}")
        elif kind == "winhttp_request":
            print(f"  ► {payload.get('verb', '?')} {payload.get('path', '?')}")
        elif kind == "winhttp_send":
            hdrs = (payload.get("headers") or "")[:120]
            print(f"  ↑ send  headers={hdrs}…  body={payload.get('bodyLen', 0)}b")
        elif kind == "winhttp_response":
            print(f"  ↓ response  ok={payload.get('success')}")
        elif kind == "tcp_connect":
            print(f"  ⇄ TCP {payload.get('ip')}:{payload.get('port')}")
        elif kind == "raw_send":
            first_line = (payload.get("preview") or "").split("\r\n", 1)[0]
            print(f"  ↑ raw  {first_line}")
        elif kind == "raw_recv":
            first_line = (payload.get("preview") or "").split("\r\n", 1)[0]
            print(f"  ↓ raw  {first_line}")
        else:
            print(f"  [{kind}] {json.dumps(payload, ensure_ascii=False)[:200]}")
    elif message["type"] == "error":
        print(f"  [frida script error] {message.get('description', message)}")


# ─── Поиск PID рендер-процесса Chromium ──────────────────────────
def find_browser_pid(browser):
    """
    Playwright не даёт PID напрямую. Берём из системы — ищем chromium
    процесс, запущенный Playwright (по пути user-data-dir).
    """
    import psutil

    # Playwright хранит данные в temp-папке с «playwright» в имени
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = proc.info["cmdline"] or []
            cmdline_str = " ".join(cmdline).lower()
            if ("chromium" in name or "chrome" in name) and "playwright" in cmdline_str:
                # Нам нужен главный процесс (с --type= нет)
                if not any(c.startswith("--type=") for c in cmdline):
                    return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def find_all_browser_pids():
    """Возвращает все PID процессов Chromium от Playwright (main + renderer)."""
    import psutil
    pids = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = proc.info["cmdline"] or []
            cmdline_str = " ".join(cmdline).lower()
            if ("chromium" in name or "chrome" in name) and "playwright" in cmdline_str:
                pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


# ─── Main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="x.com request sniffer (Playwright + Frida)")
    parser.add_argument("--headless", action="store_true", help="Запуск без GUI")
    parser.add_argument("--timeout", type=int, default=0, help="Остановка через N сек (0 = ручная)")
    parser.add_argument("--output", "-o", type=str, default="requests.json", help="Файл для сохранения лога")
    parser.add_argument("--url", type=str, default="https://x.com", help="URL для открытия")
    args = parser.parse_args()

    if not HOOKS_JS.exists():
        print(f"[!] Не найден файл хуков: {HOOKS_JS}")
        sys.exit(1)

    hooks_code = HOOKS_JS.read_text(encoding="utf-8")

    print(f"[*] Запуск Playwright Chromium ({'headless' if args.headless else 'headed'})...")
    pw = sync_playwright().start()

    browser = pw.chromium.launch(
        headless=args.headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    )
    page = context.new_page()

    # Также ловим запросы через CDP (Playwright) — как бонус
    page.on("request", lambda req: print(f"  [pw] → {req.method} {req.url[:150]}"))
    page.on("response", lambda resp: print(f"  [pw] ← {resp.status} {resp.url[:150]}"))

    print(f"[*] Открываю {args.url} ...")
    page.goto(args.url, wait_until="domcontentloaded", timeout=30000)

    # ─── Аттач Frida ──────────────────────────────────────────────
    print("[*] Ищу процессы Chromium от Playwright...")
    time.sleep(2)  # дать время процессам подняться

    pids = find_all_browser_pids()
    if not pids:
        print("[!] Не нашёл процессы Chromium. Frida не подключена.")
        print("    Но CDP-перехват через Playwright всё равно работает.")
        print("    Нажми Ctrl+C для завершения.")
    else:
        print(f"[*] Найдено {len(pids)} процессов Chromium: {pids}")
        # Аттачимся к главному (первому по списку — обычно parent)
        main_pid = pids[0]
        sessions = []
        for pid in pids:
            try:
                print(f"[*] Аттачу Frida к PID {pid}...")
                session = frida.attach(pid)
                script = session.create_script(hooks_code)
                script.on("message", on_message)
                script.load()
                sessions.append(session)
                print(f"[+] Frida подключена к PID {pid}")
            except frida.ProcessNotFoundError:
                print(f"[!] Процесс {pid} уже завершился, пропускаю")
            except Exception as e:
                print(f"[!] Не удалось аттачнуть Frida к {pid}: {e}")

    # ─── Ожидание ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(" Сниффер запущен. Работай с x.com в открытом браузере.")
    print(" Все запросы логируются ниже и сохранятся в файл.")
    if args.timeout > 0:
        print(f" Автостоп через {args.timeout} сек.")
    else:
        print(" Нажми Ctrl+C для остановки.")
    print("=" * 60)
    print()

    try:
        if args.timeout > 0:
            time.sleep(args.timeout)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Останавливаю...")

    # ─── Сохранение ───────────────────────────────────────────────
    output_path = Path(__file__).parent / args.output
    with LOCK:
        total = len(COLLECTED)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(COLLECTED, f, ensure_ascii=False, indent=2)

    print(f"[*] Сохранено {total} записей → {output_path}")

    # ─── Cleanup ──────────────────────────────────────────────────
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass

    print("[*] Готово.")


if __name__ == "__main__":
    main()
