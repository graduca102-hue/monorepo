"""
Garvis Chat Parser — Playwright.

Открывает мини-апп в браузере. Пока ты листаешь каталог, скрипт:
  • перехватывает ответы /chat-catalog → собирает чаты (chatId, title, memberCount)
  • автоматически кликает по карточкам чатов → мини-апп сам делает reveal
    своими валидными подписями (без 404 / budget-обхода с нашей стороны)
  • перехватывает ответы /reveal и КОРРЕКТНО связывает ссылку с чатом
    по chatId из тела запроса (не гадая из DOM)
  • пишет строку "https://t.me/... | участники | название" в реальном времени

Запуск:
  python garvis_playwright.py            # авто-клик по карточкам + сбор
  python garvis_playwright.py --manual   # без авто-клика: листаешь/кликаешь сам

Ctrl+C — остановить (прогресс сохраняется постоянно).
"""

import json
import sys
import os
import asyncio

OUTPUT_FILE = "garvis_chats.txt"
RAW_FILE = "garvis_chats_raw.json"
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".garvis_parser_profile")

AUTO_CLICK = "--manual" not in sys.argv

# ------- состояние -------
all_chats = []
chat_ids = set()
by_id = {}            # chatId -> chat dict
revealed_ids = set()  # chatId, для которых уже есть ссылка
seen_links = set()    # ссылки, уже записанные в txt


def load_existing():
    if os.path.exists(RAW_FILE):
        try:
            with open(RAW_FILE, "r", encoding="utf-8") as f:
                for chat in json.load(f):
                    if isinstance(chat, dict):
                        cid = str(chat.get("chatId", ""))
                        if cid and cid not in chat_ids:
                            chat_ids.add(cid)
                            all_chats.append(chat)
                            by_id[cid] = chat
                            if chat.get("joinUrl"):
                                revealed_ids.add(cid)
        except Exception as e:
            print(f"⚠️ raw: {e}")
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    seen_links.add(line.split(" | ")[0])
    print(f"📂 Загружено: {len(all_chats)} чатов, {len(revealed_ids)} с ссылками")


def save_raw():
    tmp = RAW_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_chats, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RAW_FILE)


def append_link(url, members, title):
    if url in seen_links:
        return False
    seen_links.add(url)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url} | {members} | {title}\n")
    return True


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ pip install playwright && python -m playwright install chromium")
        sys.exit(1)

    load_existing()

    stats = {"revealed": 0, "new_chats": 0, "rate_limited": False}

    def handle_response(response):
        url = response.url
        if response.status not in (200, 429):
            return
        if "api.garvisbot.org" not in url:
            return
        if "/avatar" in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct and response.status == 200:
            return

        async def process():
            try:
                # reveal: связываем joinUrl с chatId из тела запроса
                if "reveal" in url:
                    if response.status == 429:
                        stats["rate_limited"] = True
                        return
                    req = response.request
                    post = req.post_data
                    cid = None
                    if post:
                        try:
                            cid = str(json.loads(post).get("chatId", ""))
                        except Exception:
                            pass
                    body = await response.text()
                    data = json.loads(body)
                    join_url = data.get("joinUrl") or data.get("join_url")
                    if join_url and cid and cid in by_id:
                        chat = by_id[cid]
                        chat["joinUrl"] = join_url
                        revealed_ids.add(cid)
                        title = chat.get("title", "")
                        members = chat.get("memberCount", "")
                        if append_link(join_url, members, title):
                            stats["revealed"] += 1
                            print(f"  🔗 [{stats['revealed']}] {join_url} | {members} | {title[:40]}")
                        save_raw()
                    elif join_url:
                        # нет привязки к chatId — пишем только ссылку
                        if append_link(join_url, "", ""):
                            stats["revealed"] += 1
                            print(f"  🔗 [{stats['revealed']}] {join_url}")
                    return

                # каталог: собираем чаты
                if "chat-catalog" in url and "session" not in url:
                    if response.status != 200:
                        return
                    body = await response.text()
                    data = json.loads(body)
                    items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    added = 0
                    for chat in items:
                        if isinstance(chat, dict):
                            cid = str(chat.get("chatId", ""))
                            if cid and cid not in chat_ids:
                                chat_ids.add(cid)
                                all_chats.append(chat)
                                by_id[cid] = chat
                                added += 1
                    if added:
                        stats["new_chats"] += added
                        print(f"  📥 +{added} чатов (всего: {len(all_chats)})")
                        save_raw()
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"  ⚠️ {e}")

        asyncio.ensure_future(process())

    print("=" * 60)
    print("🔍 Garvis Parser — Playwright")
    print("=" * 60)
    print(f"📄 {os.path.abspath(OUTPUT_FILE)}")
    print(f"режим: {'АВТО-КЛИК' if AUTO_CLICK else 'РУЧНОЙ'}")
    print()
    print("1. Откроется браузер → залогинься (профиль сохраняется)")
    print("2. Открой @hellogarvisbot → мини-апп → каталог")
    print("3. Введи поисковый запрос (например 'взаимные подписки')")
    if AUTO_CLICK:
        print("4. Скрипт сам кликает по карточкам и собирает ссылки")
    else:
        print("4. Листай и кликай сам — ссылки перехватываются")
    print("5. Ctrl+C — остановить")
    print()

    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()
    page.on("response", handle_response)
    await page.goto("https://web.telegram.org/k/")

    print("⏳ Жду мини-апп...\n")

    def find_frame():
        for fr in page.frames:
            if "webapp.garvisbot.org" in fr.url:
                return fr
        return None

    try:
        if not AUTO_CLICK:
            # просто слушаем
            while True:
                await asyncio.sleep(1)
        else:
            frame = None
            while not frame:
                frame = find_frame()
                if not frame:
                    await asyncio.sleep(2)
            print("✅ Мини-апп найден. Начинаю авто-клик по карточкам.\n")

            idle_rounds = 0
            while True:
                frame = find_frame() or frame

                # если поймали rate limit — большая пауза
                if stats["rate_limited"]:
                    print("  ⏳ reveal rate limit — пауза 60с (мини-апп сам восстановит бюджет)")
                    stats["rate_limited"] = False
                    await asyncio.sleep(60)
                    continue

                # ищем карточки чатов
                try:
                    cards = await frame.query_selector_all(".chat-preview-card, .command-row.chat-preview-card")
                except Exception:
                    cards = []

                if not cards:
                    idle_rounds += 1
                    if idle_rounds % 5 == 0:
                        print("  … жду карточки (введи поисковый запрос в каталоге)")
                    await asyncio.sleep(2)
                    continue
                idle_rounds = 0

                clicked_any = False
                for card in cards:
                    try:
                        # открываем карточку
                        await card.click(timeout=3000)
                        await asyncio.sleep(0.6)

                        # ТОЛЬКО точная кнопка "Скопировать ссылку" (не жмём другое!)
                        btn = None
                        for sel in [
                            'text="Скопировать ссылку"',
                            'text="Копировать ссылку"',
                            'text="Copy link"',
                        ]:
                            btn = await frame.query_selector(sel)
                            if btn:
                                break
                        if btn:
                            await btn.click(timeout=3000)
                            clicked_any = True
                            await asyncio.sleep(0.8)  # ждём reveal-ответ

                        # закрываем drawer
                        try:
                            await frame.evaluate("document.querySelector('[data-vaul-overlay]')?.click()")
                        except Exception:
                            pass
                        await asyncio.sleep(0.4)

                        if stats["rate_limited"]:
                            break
                    except Exception:
                        try:
                            await frame.evaluate("document.querySelector('[data-vaul-overlay]')?.click()")
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)

                # скроллим вниз чтобы подгрузить новые
                try:
                    await frame.evaluate("""
                        const el = document.querySelector('.chat-catalog-cards')
                                   || document.querySelector('.chat-catalog-page')
                                   || document.scrollingElement;
                        if (el) el.scrollTop = el.scrollHeight;
                        window.scrollBy(0, 1200);
                    """)
                except Exception:
                    pass
                print(f"  📊 ссылок: {stats['revealed']} | чатов: {len(all_chats)} — скроллю дальше")
                await asyncio.sleep(2)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        save_raw()
        total = len(seen_links)
        print(f"\n{'='*60}")
        print(f"📊 Чатов: {len(all_chats)} | Ссылок в файле: {total}")
        print(f"   Файл: {os.path.abspath(OUTPUT_FILE)}")
        print(f"{'='*60}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        save_raw()
        print(f"\n📊 Ссылок в файле: {len(seen_links)}")
