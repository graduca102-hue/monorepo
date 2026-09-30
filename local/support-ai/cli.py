"""Console tester for SupportAgent — talk to the bot without Telegram.

    python cli.py

Type a question, press Enter, get the reply. `/reset` clears history,
`/quit` exits.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from ai_agent import SupportAgent, format_operator_line

load_dotenv(Path(__file__).resolve().parent / ".env")


async def main() -> None:
    agent = SupportAgent()
    user_id = int(os.getenv("CLI_USER_ID", "1"))
    print("support-ai CLI. /reset, /quit. Пиши вопрос:")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text == "/quit":
            return
        if text == "/reset":
            agent.reset(user_id)
            print("[история очищена]")
            continue
        reply = await agent.reply(user_id=user_id, text=text)
        print(f"\n{reply.text}\n")
        if reply.escalation is not None:
            line = format_operator_line(user_id=user_id, escalation=reply.escalation)
            print(f"[оператору] {line}\n")


if __name__ == "__main__":
    asyncio.run(main())
