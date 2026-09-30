"""
Garvis Chat Parser — асинхронно, все 57 аккаунтов одновременно.

Каждый аккаунт — независимый async-воркер. Все воркеры разбирают общую
очередь поисковых запросов. Внутри своего запроса воркер листает каталог
и раскрывает непокрытые чаты. Как только упирается в reveal-лимит,
запрос возвращается в очередь и подхватывается другими воркерами.

python garvis_multi.py
"""

import asyncio
import json
import os
import time

from garvis_lib import list_sessions, get_init_data, SESSIONS_DIR
from garvis_async import AsyncGarvisClient


LKBOT_USERNAME = "hellogarvisbot"

OUTPUT_FILE = "garvis_chats.txt"
RAW_FILE = "garvis_chats_raw.json"
CACHE_DIR = "sessions_cache"

MAX_CONCURRENCY = 15        # сколько аккаунтов работают одновременно
REVEAL_DELAY = 0.5          # пауза между reveal внутри одного воркера
CATALOG_DELAY = 0.5         # пауза между листами каталога
CONSEC_FAIL_LIMIT = 4       # столько подряд 429 на reveal → аккаунт «остыл»
COOLDOWN_ON_EXHAUST = 300   # cooldown после исчерпания бюджета

SEARCH_QUERIES = [
    # прошлый набор
    "взаимные подписки", "взаимная подписка", "взаимные реакции",
    "взаимный пиар", "пиар чат", "пиар", "вп чат", "взаимный лайк",
    "раскрутка", "продвижение", "взаимка", "вп", "чат пиар",
    "реклама чат", "барахолка", "актив чат", "общение чат",
    "чат общения", "флуд чат", "чат для общения",
    # новые для добора
    "подписки", "реакции", "лайки", "чат вп", "чат раскрутки",
    "тг чат", "пиар канал", "пиар групп", "общий чат", "чат",
    "накрутка", "trade chat", "crypto chat", "nft chat",
    "чат подписчиков", "чат реклама", "объявления", "флуд",
    "чат пиара", "чат вп рп", "vzp",
]

os.makedirs(CACHE_DIR, exist_ok=True)


# ---------- shared state ----------
all_chats: list[dict] = []
chat_ids: set[str] = set()
by_id: dict[str, dict] = {}
revealed_ids: set[str] = set()
seen_links: set[str] = set()

file_lock = asyncio.Lock()  # для txt/raw
state_lock = asyncio.Lock() # для all_chats/by_id/revealed_ids

stats = {
    "revealed_total": 0,
    "chats_total": 0,
    "queries_done": 0,
    "workers_active": 0,
}


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
    stats["chats_total"] = len(all_chats)
    print(f"📂 Загружено: {len(all_chats)} чатов, {len(revealed_ids)} с ссылками")


def _write_raw_sync():
    """Синхронная запись raw. Устойчива к блокировке файла редактором."""
    for _ in range(5):
        try:
            tmp = RAW_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(all_chats, f, ensure_ascii=False, indent=2)
            os.replace(tmp, RAW_FILE)
            return
        except PermissionError:
            time.sleep(0.5)
    try:
        with open(RAW_FILE, "w", encoding="utf-8") as f:
            json.dump(all_chats, f, ensure_ascii=False, indent=2)
    except PermissionError:
        pass


async def save_raw():
    async with file_lock:
        await asyncio.to_thread(_write_raw_sync)


async def append_link(url: str, members, title: str) -> bool:
    async with file_lock:
        if url in seen_links:
            return False
        seen_links.add(url)
        await asyncio.to_thread(
            _append_line_sync, f"{url} | {members} | {title}\n"
        )
        return True


def _append_line_sync(line: str):
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(line)


# ---------- worker ----------

async def process_query(client: AsyncGarvisClient, query: str) -> tuple[bool, int]:
    """
    Проходит запрос текущим аккаунтом. Возвращает (exhausted, revealed_count).
    exhausted=True — аккаунт исчерпал reveal-бюджет (нужно уйти на cooldown).
    """
    cursor = None
    revealed_here = 0
    consec_fail = 0

    while True:
        status, data = await client.catalog_page(query, cursor)
        if status != 200 or not isinstance(data, dict):
            return False, revealed_here

        items = data.get("items", [])
        cursor = data.get("nextCursor")

        # добавляем новые чаты в базу
        async with state_lock:
            for chat in items:
                cid = str(chat.get("chatId", ""))
                if cid and cid not in chat_ids:
                    chat_ids.add(cid)
                    all_chats.append(chat)
                    by_id[cid] = chat
                    stats["chats_total"] = len(all_chats)

        # раскрываем непокрытые
        for chat in items:
            cid = str(chat.get("chatId", ""))
            if not cid:
                continue
            async with state_lock:
                already = cid in revealed_ids
            if already:
                continue

            status, res = await client.reveal(cid)
            if status == 200 and isinstance(res, dict) and res.get("joinUrl"):
                url = res["joinUrl"]
                async with state_lock:
                    revealed_ids.add(cid)
                    by_id[cid]["joinUrl"] = url
                    title = by_id[cid].get("title", "")
                    members = by_id[cid].get("memberCount", "")
                added = await append_link(url, members, title)
                if added:
                    stats["revealed_total"] += 1
                    n = stats["revealed_total"]
                    if n <= 20 or n % 25 == 0:
                        print(f"🔗 [{n}] {url} | {members} | {title[:35]}  [acc {client.name}]")
                revealed_here += 1
                consec_fail = 0
            elif status == 429:
                consec_fail += 1
                if consec_fail >= CONSEC_FAIL_LIMIT:
                    return True, revealed_here
            else:
                # 4xx/5xx/сеть — не считаем за бюджет, идём дальше
                pass

            await asyncio.sleep(REVEAL_DELAY)

        if not cursor or len(items) < 50:
            return False, revealed_here
        await asyncio.sleep(CATALOG_DELAY)


async def worker(worker_id: int, session_file: str, semaphore: asyncio.Semaphore):
    """
    Воркер = один аккаунт. Крутит все поисковые запросы в случайном порядке
    (чтобы разные аккаунты не читали ту же страницу одновременно) и раскрывает
    только те chatId, что ещё не покрыты глобально.
    """
    import random

    async with semaphore:
        stats["workers_active"] += 1
        item_id = session_file[:-len(".session")]
        path = os.path.join(SESSIONS_DIR, session_file)

        init, status_str = await get_init_data(path)
        if not init:
            print(f"👤 [{worker_id}] {item_id}: ⚠️ initData failed ({status_str})")
            stats["workers_active"] -= 1
            return

        client = AsyncGarvisClient(
            init, LKBOT_USERNAME,
            cache_file=os.path.join(CACHE_DIR, f"{item_id}.json"),
            name=item_id,
        )
        client.load_cache()
        if not await client.ensure_session():
            print(f"👤 [{worker_id}] {item_id}: ⚠️ session failed")
            await client.aclose()
            stats["workers_active"] -= 1
            return

        print(f"👤 [{worker_id}] {item_id}: ✅ активен")

        try:
            queries = SEARCH_QUERIES.copy()
            random.shuffle(queries)

            for query in queries:
                # проходим запрос; при exhaust — cooldown и повтор
                while True:
                    exhausted, revealed = await process_query(client, query)
                    if not exhausted:
                        stats["queries_done"] += 1
                        break
                    print(f"👤 [{worker_id}] {item_id}: 🧊 '{query}' cooldown "
                          f"{COOLDOWN_ON_EXHAUST}с (+{revealed} за этот заход)")
                    await asyncio.sleep(COOLDOWN_ON_EXHAUST)

                # сбрасываем raw после каждого запроса
                await save_raw()
        finally:
            await client.aclose()
            stats["workers_active"] -= 1
            print(f"👤 [{worker_id}] {item_id}: 🚪 завершил")


async def progress_reporter():
    while True:
        await asyncio.sleep(30)
        print(f"\n📊 прогресс: ссылок {stats['revealed_total']} | чатов {stats['chats_total']} | "
              f"запросов пройдено {stats['queries_done']}/{len(SEARCH_QUERIES)} | "
              f"воркеров активно {stats['workers_active']}\n")


async def main():
    print("=" * 60)
    print("🔍 Garvis Multi-Account Async Parser")
    print("=" * 60)
    print(f"📄 {os.path.abspath(OUTPUT_FILE)}\n")

    load_existing()

    sessions = list_sessions()
    print(f"👥 Аккаунтов: {len(sessions)} | параллельно: {MAX_CONCURRENCY}")
    print(f"🔍 Запросов: {len(SEARCH_QUERIES)}\n")

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    reporter = asyncio.create_task(progress_reporter())

    workers = [
        asyncio.create_task(worker(i, sess, semaphore))
        for i, sess in enumerate(sessions, 1)
    ]

    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        reporter.cancel()
        for w in workers:
            if not w.done():
                w.cancel()
        for w in workers:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass
        await save_raw()

        with_links = sum(1 for c in all_chats if c.get("joinUrl"))
        print(f"\n{'='*60}")
        print(f"✅ Готово!")
        print(f"   Чатов в базе: {len(all_chats)}")
        print(f"   Ссылок получено: {with_links}")
        print(f"   Строк в файле: {len(seen_links)}")
        print(f"   Файл: {os.path.abspath(OUTPUT_FILE)}")
        print(f"{'='*60}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
