"""Полный цикл на одном аккаунте: initData → сессия каталога → поиск → reveal."""
import asyncio, os
from garvis_lib import list_sessions, get_init_data, SESSIONS_DIR
from garvis_parser import GarvisCatalogClient, LKBOT_USERNAME, API_BASE
import json


async def main():
    sessions = list_sessions()
    path = os.path.join(SESSIONS_DIR, sessions[0])
    print(f"Аккаунт: {sessions[0]}")

    init, status = await get_init_data(path)
    if not init:
        print(f"❌ initData: {status}")
        return
    print(f"✅ initData ({len(init)} симв.)")

    # свежий клиент БЕЗ кэша (новый ключ, новая сессия для этого аккаунта)
    client = GarvisCatalogClient(init, LKBOT_USERNAME)
    if not client.bootstrap_session():
        print("❌ не создал сессию")
        return

    # поиск
    data = client._request("GET", "/webapp/chat-catalog",
                           params={"q": "взаимные подписки", "random": "false", "limit": "10"})
    if not data:
        print("❌ каталог не ответил")
        return
    items = data.get("items", [])
    print(f"✅ каталог: {len(items)} чатов (в API: {data.get('totalCount')})")

    # пробуем reveal 3 чатов
    ok = 0
    for chat in items[:3]:
        url = client.reveal_chat(chat["chatId"])
        if url:
            ok += 1
            print(f"  🔗 {url} | {chat.get('memberCount')} | {chat.get('title','')[:40]}")
        else:
            print(f"  ❌ reveal не прошёл для {chat.get('title','')[:40]}")
    print(f"\nReveal успешных: {ok}/3 (у свежего аккаунта свой бюджет)")


asyncio.run(main())
