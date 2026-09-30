"""One-off delivery of the last broadcast post to every real user.

Resume-safe: every delivered user_id is appended to a state file, so re-running
after an interruption never sends a duplicate.
"""
import asyncio
import json
import os
import sqlite3
import time

import aiohttp
from dotenv import load_dotenv

SOURCE_CHAT_ID = -1003845201683
SOURCE_MESSAGE_ID = 72
DB_PATH = "/opt/botshop/data1.db"
LOG_PATH = "/opt/botshop/broadcast_post_72.log"
SENT_PATH = "/opt/botshop/broadcast_post_72.sent"


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def load_sent() -> set[int]:
    if not os.path.exists(SENT_PATH):
        return set()
    with open(SENT_PATH, encoding="utf-8") as stream:
        return {int(line) for line in stream if line.strip().lstrip("-").isdigit()}


async def api(session, token, method, data):
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with session.post(url, data=data) as response:
        payload = await response.json(content_type=None)
        payload["http_status"] = response.status
        return payload


async def copy_with_retry(session, token, user_id):
    for attempt in range(6):
        try:
            result = await api(session, token, "copyMessage", {
                "chat_id": str(user_id),
                "from_chat_id": str(SOURCE_CHAT_ID),
                "message_id": str(SOURCE_MESSAGE_ID),
            })
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 5:
                return False, f"network: {exc}"
            await asyncio.sleep(min(2 ** attempt, 10))
            continue
        if result.get("ok"):
            return True, ""
        params = result.get("parameters") or {}
        retry_after = int(params.get("retry_after") or 0)
        if retry_after and attempt < 5:
            await asyncio.sleep(retry_after + 0.2)
            continue
        return False, str(result.get("description") or json.dumps(result, ensure_ascii=False))
    return False, "retry limit reached"


async def main() -> None:
    load_dotenv("/opt/botshop/.env")
    token = (os.getenv("TOKEN") or "").strip()
    admin_id = int(os.getenv("ADMIN_ID") or 0)
    if not token:
        raise RuntimeError("TOKEN is missing")

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    raw = [int(row[0]) for row in connection.execute("SELECT DISTINCT user_id FROM users")]
    connection.close()

    # Telegram ids are positive; negative rows are synthetic and always fail.
    users = sorted({uid for uid in raw if uid > 0})
    already = load_sent()
    users = [uid for uid in users if uid not in already]
    if admin_id in users:
        users.remove(admin_id)
        users.insert(0, admin_id)

    log(f"START post={SOURCE_MESSAGE_ID} recipients={len(users)} skipped_already_sent={len(already)}")

    timeout = aiohttp.ClientTimeout(total=45)
    connector = aiohttp.TCPConnector(limit=30)
    success = 0
    failed = 0
    errors: dict[str, int] = {}

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        me = await api(session, token, "getMe", {})
        if not me.get("ok"):
            raise RuntimeError(f"getMe failed: {me}")
        source = await api(session, token, "getChat", {"chat_id": str(SOURCE_CHAT_ID)})
        if not source.get("ok"):
            raise RuntimeError(f"source chat unavailable: {source}")
        log(f"BOT @{me['result'].get('username')} source={source['result'].get('title')!r}")

        with open(SENT_PATH, "a", encoding="utf-8") as sent_stream:
            for index, user_id in enumerate(users, 1):
                ok, error = await copy_with_retry(session, token, user_id)
                if ok:
                    success += 1
                    sent_stream.write(f"{user_id}\n")
                    sent_stream.flush()
                else:
                    failed += 1
                    errors[error] = errors.get(error, 0) + 1
                    lowered = error.casefold()
                    if index == 1 and (
                        "message to copy not found" in lowered
                        or "not enough rights" in lowered
                    ):
                        raise RuntimeError(f"source validation failed: {error}")
                if index % 200 == 0 or index == len(users):
                    log(f"PROGRESS {index}/{len(users)} success={success} failed={failed}")
                await asyncio.sleep(0.045)

    common = sorted(errors.items(), key=lambda item: item[1], reverse=True)[:10]
    log(f"DONE success={success} failed={failed} errors={json.dumps(common, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
