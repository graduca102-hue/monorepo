import asyncio
from garvis_lib import list_sessions, get_init_data, SESSIONS_DIR
import os

async def main():
    sessions = list_sessions()
    print(f"Сессий: {len(sessions)}")
    # тест первых 3
    for s in sessions[:3]:
        path = os.path.join(SESSIONS_DIR, s)
        init, status = await get_init_data(path)
        if init:
            print(f"✅ {s}: initData ok ({len(init)} симв.) — {init[:80]}...")
        else:
            print(f"❌ {s}: {status}")

asyncio.run(main())
