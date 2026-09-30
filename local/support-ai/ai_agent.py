"""LLM support agent on DeepSeek API (OpenAI-compatible).

Public entry point is `SupportAgent`:

    agent = SupportAgent()
    reply = await agent.reply(user_id=123, text="оплатил, баланс не пришёл")

`reply` returns a `Reply` with `text` for the client, plus optional
`escalation` dict when the model decided to hand the case to a human.
Conversation history is kept in-memory per `user_id`.

DeepSeek exposes an OpenAI-compatible endpoint at https://api.deepseek.com,
so we use the openai SDK with a custom base_url. Models:
- deepseek-chat     — V3 chat (fast, cheap, default here)
- deepseek-reasoner — R1 reasoning (slower, more expensive, no tool calling)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
FAQ_PATH = ROOT.parent / "support-faq" / "faq.md"
PROMPT_PATH = ROOT.parent / "support-faq" / "system_prompt.md"
FAQ_PLACEHOLDER = "Сюда вставляется полное содержимое файла `faq.md` без изменений."

DEFAULT_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.getenv("SUPPORT_AI_MODEL", "deepseek-chat")
MAX_HISTORY_TURNS = int(os.getenv("SUPPORT_AI_HISTORY_TURNS", "12"))
MAX_TOKENS = int(os.getenv("SUPPORT_AI_MAX_TOKENS", "800"))
TEMPERATURE = float(os.getenv("SUPPORT_AI_TEMPERATURE", "0.2"))

ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate_to_operator",
        "description": (
            "Передать диалог живому оператору поддержки. Вызывай, когда: "
            "(1) вопрос требует решения о деньгах — возврат, замена, компенсация, "
            "начисление, списание; (2) заказ висит дольше сроков из FAQ; "
            "(3) в базе знаний нет ответа; (4) клиент пишет повторно по той же "
            "нерешённой проблеме; (5) любой другой случай из раздела «Эскалация» "
            "FAQ. После вызова инструмента клиенту будет отправлено «Передаю "
            "оператору. Ответ придёт в этот чат.» — не дублируй эту фразу."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["аккаунты", "прокси", "SMS", "email", "оплата", "прочее"],
                    "description": "Раздел, к которому относится обращение.",
                },
                "order_id": {
                    "type": "string",
                    "description": (
                        "Номер заказа, если клиент его прислал. "
                        "Иначе пустая строка — не выдумывать."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "Суть проблемы одной строкой для оператора.",
                },
                "checked": {
                    "type": "string",
                    "description": (
                        "Что уже проверено с клиентом в этом диалоге. "
                        "Пустая строка, если ничего."
                    ),
                },
                "missing": {
                    "type": "string",
                    "description": (
                        "Чего не хватает от клиента (например «скриншот, "
                        "строка прокси»). Пустая строка, если всё есть."
                    ),
                },
            },
            "required": ["section", "summary"],
        },
    },
}

ESCALATION_REPLY = "Передаю оператору. Ответ придёт в этот чат."


def _load_system_prompt() -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    faq = FAQ_PATH.read_text(encoding="utf-8")
    if FAQ_PLACEHOLDER not in prompt:
        raise RuntimeError(
            f"placeholder not found in {PROMPT_PATH}: {FAQ_PLACEHOLDER!r}"
        )
    return prompt.replace(FAQ_PLACEHOLDER, faq)


@dataclass
class Reply:
    text: str
    escalation: dict[str, Any] | None = None


@dataclass
class _Session:
    messages: list[dict[str, Any]] = field(default_factory=list)


class SupportAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
        max_history_turns: int = MAX_HISTORY_TURNS,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
            base_url=base_url,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_history_turns = max_history_turns
        self._system_prompt = _load_system_prompt()
        self._sessions: dict[int, _Session] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def reset(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)

    async def reply(self, *, user_id: int, text: str) -> Reply:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            session = self._sessions.setdefault(user_id, _Session())
            session.messages.append({"role": "user", "content": text})
            self._truncate(session)

            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                tools=[ESCALATE_TOOL],
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    *session.messages,
                ],
            )

            msg = resp.choices[0].message
            content = (msg.content or "").strip()
            tool_calls = msg.tool_calls or []

            assistant_entry: dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_entry["content"] = content
            else:
                assistant_entry["content"] = None
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            session.messages.append(assistant_entry)

            escalation: dict[str, Any] | None = None
            for tc in tool_calls:
                if tc.function.name != "escalate_to_operator":
                    continue
                try:
                    escalation = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    log.warning(
                        "bad tool arguments from model, user_id=%s: %r",
                        user_id,
                        tc.function.arguments,
                    )
                    escalation = {
                        "section": "прочее",
                        "summary": "модель прислала невалидные аргументы escalate",
                    }
                session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "escalated",
                })
                break

            if escalation is not None:
                reply_text = content or ESCALATION_REPLY
                return Reply(text=reply_text, escalation=escalation)

            if not content:
                log.warning("empty LLM reply, user_id=%s", user_id)
                return Reply(
                    text=ESCALATION_REPLY,
                    escalation={
                        "section": "прочее",
                        "summary": "LLM вернул пустой ответ",
                        "order_id": "",
                        "checked": "",
                        "missing": "",
                    },
                )
            return Reply(text=content)

    def _truncate(self, session: _Session) -> None:
        if self._max_history_turns <= 0:
            return
        keep = self._max_history_turns * 2
        if len(session.messages) <= keep:
            return
        session.messages[:] = session.messages[-keep:]


def format_operator_line(
    *,
    user_id: int,
    escalation: dict[str, Any],
) -> str:
    section = escalation.get("section", "прочее")
    order_id = escalation.get("order_id") or "—"
    summary = escalation.get("summary", "").strip() or "—"
    checked = escalation.get("checked", "").strip() or "—"
    missing = escalation.get("missing", "").strip()
    line = f"{user_id} · {order_id} · {section} · {summary} · проверено: {checked}"
    if missing:
        line += f" · не хватает: {missing}"
    return line


__all__ = ["SupportAgent", "Reply", "format_operator_line", "ESCALATION_REPLY"]
