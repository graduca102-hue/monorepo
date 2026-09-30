"""Diagnostic: try to resolve a few group sizes on the server, verbosely."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/rassil")
os.chdir("/opt/rassil")

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSIONS = Path("sessions")

TEST = ["durov", "telegram", "vzaimnyy_piar5", "Top_Podpiska", "piarv"]


async def main():
    sess = sorted(SESSIONS.glob("*.session"))[:3]
    print("sessions:", [s.stem for s in sess])
    for s in sess:
        client = TelegramClient(str(SESSIONS / s.stem), API_ID, API_HASH, receive_updates=False)
        await client.connect()
        if not await client.is_user_authorized():
            print(s.stem, "NOT AUTHORIZED")
            await client.disconnect()
            continue
        print("=== client", s.stem)
        for t in TEST:
            try:
                full = await client(GetFullChannelRequest(t))
                print(f"  {t}: participants_count={full.full_chat.participants_count}")
            except Exception as e:
                print(f"  {t}: {type(e).__name__}: {e}")
        await client.disconnect()
        break  # one client is enough


asyncio.run(main())
