"""Probe: does @CLAUDEEVREYBOT support forum topics in the owner's private chat?

Read-only-ish: creates one test topic, posts one message into it, renames it,
then deletes it. Prints every raw API response so we can see the exact field
names before touching bot.py.
"""
import asyncio
import json
import os
from pathlib import Path

import aiohttp

BASE = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env(BASE / ".env")
TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.environ["OWNER_ID"])
API = f"https://api.telegram.org/bot{TOKEN}/"


async def main() -> None:
    async with aiohttp.ClientSession() as http:
        async def call(method: str, **params):
            async with http.post(API + method, json=params) as r:
                data = await r.json()
            print(f"\n=== {method} ===\n{json.dumps(data, ensure_ascii=False, indent=1)}")
            return data

        await call("getMe")
        await call("getChat", chat_id=OWNER)

        created = await call("createForumTopic", chat_id=OWNER, name="probe: тема-тест")
        if not created.get("ok"):
            print("\n>>> createForumTopic FAILED in private chat — topic mode not usable yet.")
            return
        tid = created["result"]["message_thread_id"]

        await call("sendMessage", chat_id=OWNER, message_thread_id=tid,
                   text="probe: сообщение внутри темы (проверка message_thread_id)")
        await call("editForumTopic", chat_id=OWNER, message_thread_id=tid, name="probe: переименована")
        await call("sendChatAction", chat_id=OWNER, message_thread_id=tid, action="typing")

        # draft-streaming method added in 9.3 — check availability
        await call("sendMessageDraft", chat_id=OWNER, message_thread_id=tid,
                   text="probe draft (частичное сообщение)")

        await call("deleteForumTopic", chat_id=OWNER, message_thread_id=tid)
        print("\n>>> DONE — topic created, used and deleted successfully.")


if __name__ == "__main__":
    asyncio.run(main())
