import asyncio
import json
import os
import sqlite3
import time

import aiohttp
from dotenv import load_dotenv


DB_PATH = "/opt/botshop/data1.db"
LOG_PATH = "/opt/botshop/notify_referrers.log"
PREMIUM_EMOJI_IDS = (
    "6028171274939797252",
    "6037083366438737901",
    "6039451237743595514",
)


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def premium_emoji(document_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{document_id}">{fallback}</tg-emoji>'


async def api(session: aiohttp.ClientSession, token: str, method: str, data: dict) -> dict:
    async with session.post(f"https://api.telegram.org/bot{token}/{method}", data=data) as response:
        return await response.json(content_type=None)


async def send_with_retry(session: aiohttp.ClientSession, token: str, user_id: int, text: str):
    for attempt in range(5):
        try:
            result = await api(
                session,
                token,
                "sendMessage",
                {
                    "chat_id": str(user_id),
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 4:
                return False, f"network: {exc}"
            await asyncio.sleep(min(2 ** attempt, 8))
            continue
        if result.get("ok"):
            return True, ""
        retry_after = int((result.get("parameters") or {}).get("retry_after") or 0)
        if retry_after and attempt < 4:
            await asyncio.sleep(retry_after + 0.2)
            continue
        return False, str(result.get("description") or json.dumps(result, ensure_ascii=False))
    return False, "retry limit reached"


async def main() -> None:
    load_dotenv("/opt/botshop/.env")
    token = (os.getenv("TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TOKEN is missing")

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    recipients = [
        (int(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT referred_by, COUNT(*)
            FROM users
            WHERE referred_by IS NOT NULL
              AND referred_by != user_id
            GROUP BY referred_by
            ORDER BY referred_by
            """
        )
    ]
    connection.close()

    title = premium_emoji(PREMIUM_EMOJI_IDS[0], "🤝")
    income = premium_emoji(PREMIUM_EMOJI_IDS[1], "💎")
    saved = premium_emoji(PREMIUM_EMOJI_IDS[2], "✨")
    text = (
        f"{title} <b>Реферальная система изменилась!</b>\n\n"
        f"{income} Теперь по новым рефералам вы получаете <b>50% от дохода маркетплейса</b> "
        "с покупок приглашённых пользователей.\n\n"
        f"{saved} Ваши старые рефералы сохранены на прежних условиях — "
        "<b>25% от их пополнений</b>."
    )

    log(f"START recipients={len(recipients)} referrals={sum(count for _, count in recipients)}")
    errors: dict[str, int] = {}
    success = 0
    failed = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
        for index, (user_id, referral_count) in enumerate(recipients, 1):
            ok, error = await send_with_retry(session, token, user_id, text)
            if ok:
                success += 1
            else:
                failed += 1
                errors[error] = errors.get(error, 0) + 1
            log(
                f"PROGRESS {index}/{len(recipients)} user={user_id} "
                f"referrals={referral_count} success={success} failed={failed}"
            )
            await asyncio.sleep(0.05)
    log(f"DONE success={success} failed={failed} errors={json.dumps(errors, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
