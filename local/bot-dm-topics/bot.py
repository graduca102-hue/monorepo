"""Telegram bot that drives a local coding agent CLI.

Supports:
- Kiro CLI via ACP
- Codex via `codex app-server`

UI: inline-button menus + Telegram forum topics ("threads") — each session
lives in its own topic of a forum supergroup. Falls back to a plain DM chat
when no forum group is configured.

Only OWNER_ID can talk to it. When the agent asks permission to run a tool,
the bot posts approve buttons in that session's topic and waits for the
owner's tap.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp

from acp_client import ACPError, KiroSession
from codex_client import CodexError, CodexSession, fetch_codex_models
from claude_client import ClaudeSession, fetch_claude_models

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "state.json"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env(BASE / ".env")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DEFAULT_CWD = os.environ.get("DEFAULT_CWD", str(BASE))
AGENT_BACKEND = (os.environ.get("AGENT_BACKEND", "kiro").strip().lower() or "kiro")
if AGENT_BACKEND not in {"kiro", "codex", "claude"}:
    AGENT_BACKEND = "kiro"
AGENT_TITLE = {"codex": "Codex", "claude": "Claude"}.get(AGENT_BACKEND, "Kiro")
AGENT_CLI = {"codex": "codex", "claude": "claude-agent-acp"}.get(AGENT_BACKEND, "kiro-cli")
DEFAULT_MODEL = (
    os.environ.get("AGENT_MODEL", "").strip()
    or os.environ.get(f"{AGENT_BACKEND.upper()}_MODEL", "").strip()
    or (os.environ.get("KIRO_MODEL", "").strip() if AGENT_BACKEND == "kiro" else "")
    or None
)

_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    from logging.handlers import RotatingFileHandler

    _fh = RotatingFileHandler(
        BASE / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    _log_handlers.append(_fh)
except Exception:
    pass

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("bot")

API = f"https://api.telegram.org/bot{BOT_TOKEN}/"
TG_MAX = 3900  # keep well below Telegram's 4096 char limit
EDIT_INTERVAL = 0.9  # seconds between streaming edits


def esc(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text), quote=False)


import re as _re

_FENCE_RE = _re.compile(r"```[ \t]*([a-zA-Z0-9_+\-.]*)\n?(.*?)```", _re.DOTALL)
_INLINE_CODE_RE = _re.compile(r"`([^`\n]+)`")


def md_to_html(text: str) -> str:
    """Convert a subset of Markdown to Telegram HTML (bold, italic, code,
    fenced code, headers, quotes, links, spoilers, strikethrough)."""
    if not text:
        return ""
    stash: list[str] = []

    def keep(html_frag: str) -> str:
        stash.append(html_frag)
        return f"\x00{len(stash) - 1}\x00"

    def _fence(m: "_re.Match") -> str:
        code = m.group(2)
        return keep(f"<pre>{html.escape(code, quote=False)}</pre>")

    text = _FENCE_RE.sub(_fence, text)
    text = _INLINE_CODE_RE.sub(
        lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text
    )
    text = html.escape(text, quote=False)

    text = _re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )
    text = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = _re.sub(r"(?<!_)__(?!_)(.+?)__", r"<b>\1</b>", text)
    text = _re.sub(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", text)
    text = _re.sub(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])", r"<i>\1</i>", text)
    text = _re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = _re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)

    out: list[str] = []
    quote: list[str] = []

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for ln in text.split("\n"):
        h = _re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", ln)
        if h:
            flush_quote()
            out.append(f"<b>{h.group(2)}</b>")
        elif ln.startswith("&gt;"):
            quote.append(ln[4:].lstrip())
        else:
            flush_quote()
            out.append(ln)
    flush_quote()
    text = "\n".join(out)

    for i, frag in enumerate(stash):
        text = text.replace(f"\x00{i}\x00", frag)
    return text


def spoiler(text: str) -> str:
    """Wrap already-escaped text in a Telegram spoiler (HTML mode)."""
    return f"<tg-spoiler>{text}</tg-spoiler>"


def fence(text: str, lang: str = "") -> str:
    """Markdown fenced code block (safe: neutralizes inner backticks)."""
    text = (text or "").replace("```", "``\u200b`")
    return f"```{lang}\n{text}\n```"


def mono(text: str) -> str:
    """Inline monospace, neutralizing backticks."""
    return "`" + (text or "").replace("`", "ʼ") + "`"


# Premium (custom) emoji from the "Telegram iOS Icons" set (tgiosicons).
# name -> (custom_emoji_id, fallback unicode)
PREMIUM_EMOJI: dict[str, tuple[str, str]] = {
    "new":     ("5895669571058142797", "🆕"),
    "sessions":("5766994197705921104", "🗂"),
    "brain":   ("5864019342873598613", "🧠"),
    "gear":    ("5904258298764334001", "⚙️"),
    "stop":    ("5938215362473496448", "🛑"),
    "status":  ("5936143551854285132", "📊"),
    "robot":   ("6030400221232501136", "🤖"),
    "tool":    ("5962952497197748583", "🔧"),
    "ok":      ("6030839471832829491", "✅"),
    "cross":   ("6030757850274336631", "❌"),
    "warn":    ("6030563507299160824", "⚠️"),
    "edit":    ("6039779802741739617", "✏️"),
    "read":    ("6037286673010660132", "📖"),
    "search":  ("6032850693348399258", "🔎"),
    "fetch":   ("5776233299424843260", "🌐"),
    "think":   ("5904248647972820334", "💭"),
    "delete":  ("6039522349517115015", "🗑"),
    "plan":    ("5920046907782074235", "📝"),
    "lock":    ("6037249452824072506", "🔒"),
    "zap":     ("5884428842780594914", "⚡"),
    "queue":   ("6041730074376410123", "📥"),
    "play":    ("5773626993010546707", "▶️"),
    "chat":    ("6030784887093464891", "💬"),
    "back":    ("6039539366177541657", "⬅️"),
    "plus":    ("6032924188828767321", "➕"),
    "folder":  ("6037475557082403885", "📁"),
    "cloud":   ("5776233299424843260", "🌐"),
}


def pe(name: str) -> str:
    """Render a premium custom emoji as HTML tg-emoji (falls back to unicode)."""
    item = PREMIUM_EMOJI.get(name)
    if not item:
        return ""
    eid, fallback = item
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def _tool_pe(kind: str) -> str:
    return pe({
        "read": "read", "edit": "edit", "delete": "delete", "move": "folder",
        "search": "search", "execute": "gear", "think": "think", "fetch": "fetch",
    }.get(kind or "", "tool"))


_TRANSIENT_MARKERS = (
    "unexpectedeof", "close_notify", "dispatchfailure", "response stream",
    "connection", "timed out", "timeout", "throttl", "too many requests",
    "temporarily", "502", "503", "504", "reset by peer", "broken pipe", "eof",
)

_AUTH_MARKERS = (
    "bearer token", "token included in the request is invalid",
    "authentication failed", "authentication required", "session may have expired",
    "session has expired", "unauthorized", "invalid_grant", "invalid token",
    "expired token", "not logged in", "please run /login", "re-authenticate",
    "403 forbidden", "401",
)


def _is_auth_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(marker in m for marker in _AUTH_MARKERS)


def _is_transient(msg: str) -> bool:
    m = (msg or "").lower()
    if _is_auth_error(m):
        return False
    return any(marker in m for marker in _TRANSIENT_MARKERS)


def _tool_icon(kind: str) -> str:
    return {
        "read": "📖", "edit": "✏️", "delete": "🗑", "move": "🚚",
        "search": "🔎", "execute": "⚙️", "think": "💭", "fetch": "🌐",
        "other": "🔧",
    }.get(kind or "", "🔧")


async def fetch_models() -> list[dict]:
    if AGENT_BACKEND == "codex":
        try:
            return await fetch_codex_models()
        except Exception:
            log.exception("fetch_codex_models failed")
            return []
    if AGENT_BACKEND == "claude":
        try:
            return await fetch_claude_models()
        except Exception:
            log.exception("fetch_claude_models failed")
            return []
    try:
        proc = await asyncio.create_subprocess_exec(
            "kiro-cli", "chat", "--list-models", "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        data = json.loads(out.decode("utf-8"))
        models = data.get("models") or []
        return [
            {
                "id": m.get("model_id"),
                "name": m.get("model_name") or m.get("model_id"),
                "desc": m.get("description", ""),
                "rate": m.get("rate_multiplier"),
            }
            for m in models if m.get("model_id")
        ]
    except Exception:
        log.exception("fetch_models failed")
        return []


# ---------------------------------------------------------------------------
# Telegram wrapper
# ---------------------------------------------------------------------------
class TG:
    def __init__(self, http: aiohttp.ClientSession) -> None:
        self.http = http

    async def call(self, method: str, **params: Any) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            async with self.http.post(API + method, json=params) as r:
                data = await r.json()
        except Exception as exc:
            log.warning("tg %s network error: %s", method, exc)
            return {"ok": False, "error": str(exc)}
        if not data.get("ok"):
            desc = str(data.get("description", ""))
            if "not modified" not in desc:
                log.warning("tg %s -> %s", method, data)
        return data

    async def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        thread_id: Optional[int] = None,
    ) -> Optional[int]:
        r = await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        return r["result"]["message_id"] if r.get("ok") else None

    async def edit(self, chat_id: int, message_id: int, text: str) -> bool:
        r = await self.call(
            "editMessageText",
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return r.get("ok", False)

    async def edit_markup(self, chat_id: int, message_id: int, reply_markup: Any) -> None:
        await self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id, message_id=message_id, reply_markup=reply_markup,
        )

    async def answer_cb(self, cb_id: str, text: str = "", alert: bool = False) -> None:
        await self.call(
            "answerCallbackQuery", callback_query_id=cb_id, text=text, show_alert=alert
        )

    async def send_rich(
        self,
        chat_id: int,
        markdown: str,
        thread_id: Optional[int] = None,
        reply_markup: Any = None,
    ) -> Optional[int]:
        """Send a Rich Message (Bot API 10.1+). Falls back to plain text."""
        r = await self.call(
            "sendRichMessage",
            chat_id=chat_id,
            rich_message={"markdown": markdown},
            message_thread_id=thread_id,
            reply_markup=reply_markup,
        )
        if r.get("ok"):
            return r["result"]["message_id"]
        # fallback to plain HTML text if rich isn't accepted
        r2 = await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=esc(markdown),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        return r2["result"]["message_id"] if r2.get("ok") else None

    async def edit_rich(self, chat_id: int, message_id: int, markdown: str) -> bool:
        """Edit a message in place with rich markdown."""
        r = await self.call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            rich_message={"markdown": markdown},
        )
        return r.get("ok", False)

    async def send_rich_html(
        self, chat_id: int, html_text: str, thread_id: Optional[int] = None,
        reply_markup: Any = None,
    ) -> Optional[int]:
        """Send a Rich Message built from HTML (supports <blockquote expandable>,
        <tg-emoji>, <code>, <pre>, <b>, <i>, <s>, <tg-spoiler>, links)."""
        r = await self.call(
            "sendRichMessage", chat_id=chat_id, rich_message={"html": html_text},
            message_thread_id=thread_id, reply_markup=reply_markup,
        )
        if r.get("ok"):
            return r["result"]["message_id"]
        r2 = await self.call(
            "sendMessage", chat_id=chat_id, text=html_text, parse_mode="HTML",
            disable_web_page_preview=True, reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        return r2["result"]["message_id"] if r2.get("ok") else None

    async def edit_rich_html(self, chat_id: int, message_id: int, html_text: str) -> bool:
        """Edit a message in place with a Rich Message built from HTML."""
        r = await self.call(
            "editMessageText", chat_id=chat_id, message_id=message_id,
            rich_message={"html": html_text},
        )
        if r.get("ok"):
            return True
        desc = ""
        # 'message is not modified' counts as success (nothing to do)
        return False

    async def create_forum_topic(
        self, chat_id: int, name: str, icon_color: Optional[int] = None
    ) -> Optional[int]:
        r = await self.call(
            "createForumTopic", chat_id=chat_id, name=name[:128], icon_color=icon_color
        )
        if r.get("ok"):
            return r["result"]["message_thread_id"]
        return None

    async def delete_forum_topic(self, chat_id: int, thread_id: int) -> None:
        await self.call("deleteForumTopic", chat_id=chat_id, message_thread_id=thread_id)

    async def close_forum_topic(self, chat_id: int, thread_id: int) -> None:
        await self.call("closeForumTopic", chat_id=chat_id, message_thread_id=thread_id)

    async def set_my_commands(self, commands: list[dict]) -> None:
        await self.call("setMyCommands", commands=commands)


# icon colors for forum topics (Telegram palette)
_TOPIC_COLORS = [7322096, 16766590, 13338331, 9367192, 16749490, 16478047]


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class Bot:
    def __init__(self, http: aiohttp.ClientSession) -> None:
        self.tg = TG(http)
        self.sessions: list[KiroSession | CodexSession] = []
        self.current_idx: int = -1
        self.default_cwd: str = DEFAULT_CWD
        self.default_model: Optional[str] = DEFAULT_MODEL
        self.trust_all_next: bool = os.environ.get("TRUST_ALL", "1").lower() not in (
            "0", "false", "no", "off", ""
        )
        # forum supergroup used for threads mode (None = plain DM mode).
        # Also set to OWNER_ID to run per-session topics directly in the DM
        # (Bot API 9.4+, when the bot has forum topic mode enabled in BotFather).
        self.host_chat_id: Optional[int] = None
        # persistent opt-out from auto-enabling per-session topics in the DM
        self.dm_topics_off: bool = False
        # thread_id -> session
        self.thread_session: dict[int, KiroSession] = {}
        self.pending_perms: dict[str, tuple[asyncio.Future, dict[str, dict]]] = {}
        self.stream: dict[str, dict] = {}
        self.busy: dict[str, asyncio.Task] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.models: list[dict] = []
        self._load_state()

    # ------------- state persistence -------------
    def _load_state(self) -> None:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                self.host_chat_id = data.get("host_chat_id")
                self.dm_topics_off = bool(data.get("dm_topics_off", False))
                self.default_model = data.get("default_model", self.default_model)
                self.default_cwd = data.get("default_cwd", self.default_cwd)
                if "trust_all" in data:
                    self.trust_all_next = bool(data["trust_all"])
        except Exception:
            log.exception("load_state failed")

    def _save_state(self) -> None:
        try:
            log.info("save_state: host_chat_id=%s model=%s trust=%s",
                     self.host_chat_id, self.default_model, self.trust_all_next)
            STATE_PATH.write_text(
                json.dumps(
                    {
                        "host_chat_id": self.host_chat_id,
                        "dm_topics_off": self.dm_topics_off,
                        "default_model": self.default_model,
                        "default_cwd": self.default_cwd,
                        "trust_all": self.trust_all_next,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.exception("save_state failed")

    def current(self) -> Optional[KiroSession | CodexSession]:
        if 0 <= self.current_idx < len(self.sessions):
            return self.sessions[self.current_idx]
        return None

    def _dest(self, s: KiroSession | CodexSession) -> tuple[int, Optional[int]]:
        return getattr(s, "tg_chat", OWNER_ID), getattr(s, "tg_thread", None)

    # ------------- session lifecycle -------------
    async def make_session(
        self, name: str, cwd: str, chat_id: int, thread_id: Optional[int]
    ) -> KiroSession | CodexSession:
        session_cls = {
            "codex": CodexSession,
            "claude": ClaudeSession,
        }.get(AGENT_BACKEND, KiroSession)
        s = session_cls(name=name, cwd=cwd, model=self.default_model, trust_all=self.trust_all_next)
        s.tg_chat = chat_id
        s.tg_thread = thread_id
        s.on_update = lambda p, s=s: self._on_update(s, p)
        s.on_permission = lambda p, s=s: self._on_permission(s, p)
        await s.start()
        return s

    # ------------- ACP callbacks (post to the session's own topic) -------------
    async def _on_update(self, s: KiroSession | CodexSession, params: dict) -> None:
        upd = params.get("update") or {}
        kind = upd.get("sessionUpdate")
        chat_id, thread_id = self._dest(s)

        if kind == "agent_message_chunk":
            content = upd.get("content") or {}
            if content.get("type") == "text":
                await self._stream_text(s, content.get("text", ""))
            return

        if kind == "agent_thought_chunk":
            return

        if kind == "tool_call":
            # feed into the live canvas as an expandable "Running" card
            await self._tool_started(s, upd)
            return

        if kind == "tool_call_update":
            status = upd.get("status")
            if status not in ("completed", "failed"):
                return
            await self._tool_finished(s, upd)
            return

        if kind == "plan":
            entries = upd.get("entries") or []
            if entries:
                marks = {"pending": "⚪", "in_progress": "🟡", "completed": "🟢"}
                lines = [
                    f"{marks.get(e.get('status', ''), '•')} {esc(e.get('content', ''))}"
                    for e in entries
                ]
                await self.tg.send(
                    chat_id, f"{pe('plan')} <b>План</b>\n" + "\n".join(lines),
                    thread_id=thread_id,
                )
            return

        if kind == "current_mode_update":
            s.current_mode = upd.get("currentModeId")
            return

    async def _on_permission(self, s: KiroSession | CodexSession, params: dict) -> str:
        await self._close_stream(s)
        chat_id, thread_id = self._dest(s)

        tool = params.get("toolCall") or {}
        title = tool.get("title") or "tool call"
        kind = tool.get("kind") or ""
        options = params.get("options") or []

        token = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        opt_map = {o["optionId"]: o for o in options if "optionId" in o}
        self.pending_perms[token] = (fut, opt_map)

        order = {"allow_once": 0, "allow_always": 1, "reject_once": 2, "reject_always": 3}
        opts_sorted = sorted(options, key=lambda o: order.get(o.get("kind", ""), 99))
        buttons: list[list[dict]] = []
        row: list[dict] = []
        for o in opts_sorted:
            oid = o.get("optionId", "")
            name = o.get("name") or o.get("kind") or "?"
            k = o.get("kind", "")
            icon = "✅" if k.startswith("allow") else "❌"
            row.append({"text": f"{icon} {name}", "callback_data": f"perm:{token}:{oid[:50]}"})
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        text = f"{_tool_pe(kind)} <b>Разрешить?</b> <code>{esc(title)}</code>"
        raw = tool.get("rawInput")
        if raw:
            try:
                raw_str = json.dumps(raw, ensure_ascii=False, indent=2)
            except Exception:
                raw_str = str(raw)
            # command / args in an expandable (arrow) blockquote
            text += f"\n<blockquote expandable>{esc(raw_str[:1500])}</blockquote>"

        await self.tg.send_rich_html(
            chat_id, text, reply_markup={"inline_keyboard": buttons}, thread_id=thread_id
        )
        try:
            return await fut
        finally:
            self.pending_perms.pop(token, None)

    # ------------- streaming: single live "canvas" per turn -------------
    # The whole turn (assistant text + tool "Running" cards) lives in ONE
    # message that is edited in place. Tool commands go into expandable
    # blockquotes (the arrow-collapsible ones). When a page exceeds the
    # Telegram limit, a new message is opened and edited from then on.
    CANVAS_LIMIT = 3600

    def _canvas(self, s: KiroSession) -> dict:
        sid = s.session_id or s.name
        st = self.stream.get(sid)
        if st is None:
            chat_id, thread_id = self._dest(s)
            st = {
                "segments": [],          # ordered: {"type":"ai","md":..} / {"type":"tool",..}
                "tools": {},             # toolCallId -> tool segment
                "pages": [],             # [{"id":msg_id,"html":str}]
                "chat": chat_id, "thread": thread_id,
                "last_edit": 0.0, "lock": asyncio.Lock(),
            }
            self.stream[sid] = st
        return st

    def _seg_ai(self, st: dict, text: str) -> None:
        segs = st["segments"]
        if segs and segs[-1]["type"] == "ai":
            segs[-1]["md"] += text
        else:
            segs.append({"type": "ai", "md": text})

    @staticmethod
    def _tool_seg_html(seg: dict) -> str:
        icon = seg.get("icon") or "🔧"
        head = f"{icon} {esc(seg.get('cmd') or 'tool')}"
        out = (seg.get("output") or "").strip()
        inner = head + ("\n" + esc(out[:2500]) if out else "")
        return f"<blockquote expandable>{inner}</blockquote>"

    def _seg_units(self, st: dict) -> list[str]:
        """Render segments into a flat list of safe HTML units (each ≤ limit)."""
        units: list[str] = []
        for seg in st["segments"]:
            if seg["type"] == "ai":
                md = seg["md"]
                if not md.strip():
                    continue
                for piece in self._split_para(md, self.CANVAS_LIMIT - 200):
                    html = md_to_html(piece)
                    if html.strip():
                        units.append(html)
            else:
                units.append(self._tool_seg_html(seg))
        return units

    @staticmethod
    def _split_para(md: str, limit: int) -> list[str]:
        """Split markdown into pieces ≤ limit on blank-line/newline boundaries."""
        md = md.strip("\n")
        if len(md) <= limit:
            return [md] if md.strip() else []
        pieces: list[str] = []
        cur = ""
        for para in md.split("\n\n"):
            add = ("\n\n" + para) if cur else para
            if len(cur) + len(add) > limit and cur:
                pieces.append(cur)
                cur = para
            else:
                cur += add
            while len(cur) > limit:
                pieces.append(cur[:limit])
                cur = cur[limit:]
        if cur.strip():
            pieces.append(cur)
        return pieces

    def _paginate(self, st: dict) -> list[str]:
        pages: list[str] = []
        cur = ""
        for unit in self._seg_units(st):
            add = ("\n\n" + unit) if cur else unit
            if len(cur) + len(add) > self.CANVAS_LIMIT and cur:
                pages.append(cur)
                cur = unit
            else:
                cur += add
        if cur.strip():
            pages.append(cur)
        return pages

    async def _render_canvas(self, st: dict, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - st["last_edit"] < EDIT_INTERVAL:
            return
        st["last_edit"] = now
        pages = self._paginate(st)
        chat_id, thread_id = st["chat"], st["thread"]
        for i, html in enumerate(pages):
            if i < len(st["pages"]):
                pg = st["pages"][i]
                if pg["html"] != html:
                    ok = await self.tg.edit_rich_html(chat_id, pg["id"], html)
                    if ok:
                        pg["html"] = html
            else:
                mid = await self.tg.send_rich_html(chat_id, html, thread_id=thread_id)
                if mid is not None:
                    st["pages"].append({"id": mid, "html": html})

    async def _stream_text(self, s: KiroSession, text: str) -> None:
        if not text:
            return
        st = self._canvas(s)
        async with st["lock"]:
            self._seg_ai(st, text)
            await self._render_canvas(st, force=False)

    async def _tool_started(self, s: KiroSession, upd: dict) -> None:
        st = self._canvas(s)
        async with st["lock"]:
            cmd = upd.get("title") or upd.get("kind") or "tool"
            raw = upd.get("rawInput")
            if raw and not upd.get("title"):
                try:
                    cmd = json.dumps(raw, ensure_ascii=False)
                except Exception:
                    pass
            seg = {"type": "tool", "icon": "🔧", "cmd": cmd, "output": ""}
            tcid = upd.get("toolCallId")
            if tcid:
                st["tools"][tcid] = seg
            st["segments"].append(seg)
            await self._render_canvas(st, force=True)

    async def _tool_finished(self, s: KiroSession, upd: dict) -> None:
        st = self._canvas(s)
        async with st["lock"]:
            tcid = upd.get("toolCallId")
            seg = st["tools"].get(tcid) if tcid else None
            status = upd.get("status")
            snippet = ""
            for block in upd.get("content") or []:
                if isinstance(block, dict):
                    inner = block.get("content")
                    if isinstance(inner, dict) and inner.get("type") == "text":
                        snippet = inner.get("text") or ""
                        break
            if seg is not None:
                seg["icon"] = "✅" if status == "completed" else "⚠️"
                if snippet:
                    seg["output"] = snippet
                await self._render_canvas(st, force=True)

    async def _close_stream(self, s: KiroSession) -> None:
        sid = s.session_id or s.name
        st = self.stream.get(sid)
        if not st:
            return
        async with st["lock"]:
            if st["segments"]:
                await self._render_canvas(st, force=True)
        self.stream.pop(sid, None)

    # ------------- UI: menus -------------
    def _main_menu_kb(self) -> dict:
        trust = "ON ✅" if self.trust_all_next else "OFF ⭕"
        mode = "🧵 Threads" if self.host_chat_id else "💬 DM"
        return {
            "inline_keyboard": [
                [{"text": "🆕 Новая сессия", "callback_data": "m:new"}],
                [
                    {"text": "📋 Сессии", "callback_data": "m:sessions"},
                    {"text": "🧠 Модель", "callback_data": "m:model"},
                ],
                [
                    {"text": "🛑 Стоп", "callback_data": "m:cancel"},
                    {"text": f"⚙️ Trust: {trust}", "callback_data": "m:trust"},
                ],
                [
                    {"text": "📊 Статус", "callback_data": "m:status"},
                    {"text": f"Режим: {mode}", "callback_data": "m:mode"},
                ],
            ]
        }

    async def show_menu(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        mode = f"{pe('chat')} Threads" if self.host_chat_id else "💬 DM"
        text = (
            f"{pe('robot')} <b>{esc(AGENT_TITLE)}-bot</b> — панель управления\n\n"
            f"Режим: <b>{mode}</b>\n"
            f"{pe('sessions')} Сессий: <b>{len(self.sessions)}</b>\n"
            f"{pe('brain')} Модель: <code>{esc(self.default_model or 'auto')}</code>\n"
            f"{pe('folder')} cwd: <code>{esc(self.default_cwd)}</code>\n\n"
            "Жми кнопки или пиши сообщение — оно уйдёт в активную сессию."
        )
        await self.tg.send(chat_id, text, reply_markup=self._main_menu_kb(), thread_id=thread_id)

    def _sessions_kb(self) -> dict:
        rows: list[list[dict]] = []
        for i, s in enumerate(self.sessions):
            mark = "▶️ " if i == self.current_idx else ""
            state = "" if s.alive else " 💀"
            rows.append([
                {"text": f"{mark}{i+1}. {s.name}{state}", "callback_data": f"s:sw:{i}"},
                {"text": "❌", "callback_data": f"s:end:{i}"},
            ])
        rows.append([{"text": "🆕 Новая", "callback_data": "m:new"},
                     {"text": "⬅️ Меню", "callback_data": "m:menu"}])
        return {"inline_keyboard": rows}

    # ------------- commands -------------
    async def cmd_start(self, dest: tuple[int, Optional[int]]) -> None:
        await self.show_menu(dest)

    async def cmd_new(self, dest: tuple[int, Optional[int]], name: str) -> None:
        name = name.strip() or f"session-{len(self.sessions)+1}"
        chat_id, thread_id = dest

        # In a forum group create a dedicated topic for the session.
        sess_chat = chat_id
        sess_thread = thread_id
        in_forum = self.host_chat_id is not None and chat_id == self.host_chat_id
        if in_forum:
            color = _TOPIC_COLORS[len(self.sessions) % len(_TOPIC_COLORS)]
            new_thread = await self.tg.create_forum_topic(
                self.host_chat_id, f"🧠 {name}", icon_color=color
            )
            if new_thread is None:
                await self.tg.send(
                    chat_id,
                    "❌ Не удалось создать тему. Проверь, что группа — форум (Topics ON) "
                    "и бот админ с правом «Управление темами». Работаю в этом чате.",
                    thread_id=thread_id,
                )
            else:
                sess_chat = self.host_chat_id
                sess_thread = new_thread

        await self.tg.send(sess_chat, f"{pe('zap')} Стартую сессию <b>{esc(name)}</b>…", thread_id=sess_thread)
        try:
            s = await self.make_session(name, self.default_cwd, sess_chat, sess_thread)
        except (ACPError, CodexError) as exc:
            await self.tg.send(sess_chat, f"❌ Ошибка старта: <code>{esc(exc)}</code>", thread_id=sess_thread)
            return
        except FileNotFoundError:
            await self.tg.send(sess_chat, f"❌ <code>{esc(AGENT_CLI)}</code> не найден в PATH.", thread_id=sess_thread)
            return
        except Exception as exc:
            log.exception("session start failed")
            await self.tg.send(sess_chat, f"❌ {esc(exc)}", thread_id=sess_thread)
            return

        self.sessions.append(s)
        self.current_idx = len(self.sessions) - 1
        if sess_thread is not None:
            self.thread_session[sess_thread] = s
        trust = " • trust-all" if s.trust_all else ""
        await self.tg.send(
            sess_chat,
            f"{pe('ok')} <b>{esc(name)}</b>{trust}\n"
            f"{pe('brain')} model: <code>{esc(self.default_model or 'auto')}</code>\n"
            f"{pe('folder')} cwd: <code>{esc(s.cwd)}</code>\n\n"
            + ("Пиши прямо в эту тему — запросы идут в эту сессию."
               if sess_thread is not None else "Пиши сюда — запросы идут в эту сессию."),
            thread_id=sess_thread,
        )
        if self.host_chat_id and sess_thread is not None and (chat_id, thread_id) != (sess_chat, sess_thread):
            await self.tg.send(chat_id, f"{pe('new')} Создана тема для <b>{esc(name)}</b> 👆", thread_id=thread_id)

    async def cmd_sessions(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        if not self.sessions:
            await self.tg.send(chat_id, "Сессий нет.", reply_markup={
                "inline_keyboard": [[{"text": "🆕 Новая сессия", "callback_data": "m:new"}]]
            }, thread_id=thread_id)
            return
        await self.tg.send(chat_id, "📋 <b>Сессии</b>:", reply_markup=self._sessions_kb(), thread_id=thread_id)

    async def cmd_switch(self, dest: tuple[int, Optional[int]], arg: str) -> None:
        chat_id, thread_id = dest
        try:
            idx = int(arg.strip()) - 1
        except ValueError:
            await self.tg.send(chat_id, "Использование: /switch N", thread_id=thread_id)
            return
        await self._switch(dest, idx)

    async def _switch(self, dest: tuple[int, Optional[int]], idx: int) -> None:
        chat_id, thread_id = dest
        if not 0 <= idx < len(self.sessions):
            await self.tg.send(chat_id, "Нет такой сессии", thread_id=thread_id)
            return
        self.current_idx = idx
        await self.tg.send(chat_id, f"✅ Активна: <b>{esc(self.sessions[idx].name)}</b>", thread_id=thread_id)

    async def cmd_end(self, dest: tuple[int, Optional[int]], arg: str) -> None:
        chat_id, thread_id = dest
        arg = arg.strip()
        if arg:
            try:
                idx = int(arg) - 1
            except ValueError:
                await self.tg.send(chat_id, "Использование: /end [N]", thread_id=thread_id)
                return
        else:
            # end the session bound to the current topic, else the active one
            idx = None
            if thread_id is not None and thread_id in self.thread_session:
                target = self.thread_session[thread_id]
                idx = self.sessions.index(target) if target in self.sessions else None
            if idx is None:
                idx = self.current_idx
        await self._end(dest, idx)

    async def _end(self, dest: tuple[int, Optional[int]], idx: int) -> None:
        chat_id, thread_id = dest
        if not 0 <= idx < len(self.sessions):
            await self.tg.send(chat_id, "Нет такой сессии", thread_id=thread_id)
            return
        s = self.sessions[idx]
        sid = s.session_id or s.name
        self._drain_queue(sid)
        worker = self.workers.pop(sid, None)
        if worker and not worker.done():
            worker.cancel()
        self.queues.pop(sid, None)
        self.busy.pop(sid, None)
        s_thread = getattr(s, "tg_thread", None)
        await s.close()
        del self.sessions[idx]
        if s_thread is not None:
            self.thread_session.pop(s_thread, None)
        if self.current_idx >= len(self.sessions):
            self.current_idx = len(self.sessions) - 1
        await self.tg.send(chat_id, f"🛑 Закрыта: <b>{esc(s.name)}</b>", thread_id=thread_id)
        # in threads mode, retire the topic (delete it in the DM, just close it
        # in a shared group so history stays visible to others)
        if self.host_chat_id and s_thread is not None:
            if self.host_chat_id == OWNER_ID:
                await self.tg.delete_forum_topic(self.host_chat_id, s_thread)
            else:
                await self.tg.close_forum_topic(self.host_chat_id, s_thread)

    async def cmd_cwd(self, dest: tuple[int, Optional[int]], arg: str) -> None:
        chat_id, thread_id = dest
        arg = arg.strip().strip('"').strip("'")
        if not arg:
            await self.tg.send(chat_id, f"cwd: <code>{esc(self.default_cwd)}</code>", thread_id=thread_id)
            return
        if not Path(arg).is_dir():
            await self.tg.send(chat_id, f"❌ Не директория: <code>{esc(arg)}</code>", thread_id=thread_id)
            return
        self.default_cwd = str(Path(arg).resolve())
        self._save_state()
        await self.tg.send(chat_id, f"✅ cwd: <code>{esc(self.default_cwd)}</code>", thread_id=thread_id)

    def _session_for(self, dest: tuple[int, Optional[int]]) -> Optional[KiroSession | CodexSession]:
        _, thread_id = dest
        if thread_id is not None and thread_id in self.thread_session:
            return self.thread_session[thread_id]
        return self.current()

    async def cmd_cancel(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        s = self._session_for(dest)
        if not s:
            await self.tg.send(chat_id, "Нет активной сессии", thread_id=thread_id)
            return
        sid = s.session_id or s.name
        dropped = self._drain_queue(sid)
        running = self.busy.get(sid)
        had_running = bool(running and not running.done())
        try:
            await s.cancel()
        except Exception as exc:
            await self.tg.send(chat_id, f"❌ {esc(exc)}", thread_id=thread_id)
            return
        parts = []
        if had_running:
            parts.append("текущий запрос прерван")
        if dropped:
            parts.append(f"из очереди убрано: {dropped}")
        if not parts:
            parts.append("нечего отменять")
        await self.tg.send(chat_id, "🛑 " + ", ".join(parts), thread_id=thread_id)

    async def cmd_queue(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        s = self._session_for(dest)
        if not s:
            await self.tg.send(chat_id, "Нет активной сессии", thread_id=thread_id)
            return
        sid = s.session_id or s.name
        q = self.queues.get(sid)
        pending = q.qsize() if q else 0
        running = self.busy.get(sid)
        busy_now = bool(running and not running.done())
        status = "выполняется 1 запрос" if busy_now else "простаивает"
        await self.tg.send(
            chat_id,
            f"📋 <b>{esc(s.name)}</b>: {status}, в очереди: <b>{pending}</b>.",
            thread_id=thread_id,
        )

    async def cmd_trust(self, dest: tuple[int, Optional[int]], arg: str) -> None:
        chat_id, thread_id = dest
        val = arg.strip().lower() in ("on", "true", "1", "yes", "y")
        self.trust_all_next = val
        self._save_state()
        await self.tg.send(
            chat_id, f"⚙️ trust_all для новых сессий: <b>{'ON' if val else 'OFF'}</b>",
            thread_id=thread_id,
        )

    async def cmd_model(self, dest: tuple[int, Optional[int]], arg: str) -> None:
        chat_id, thread_id = dest
        arg = arg.strip()
        if not self.models:
            self.models = await fetch_models()
        if not arg:
            rows: list[list[dict]] = []
            for m in self.models:
                mark = "✅ " if m["id"] == self.default_model else ""
                rate = f" ×{m['rate']}" if m.get("rate") is not None else ""
                rows.append([{
                    "text": f"{mark}{m['name']}{rate}", "callback_data": f"model:{m['id'][:55]}",
                }])
            cur = self.default_model or "auto (default)"
            await self.tg.send(
                chat_id, f"🧠 Модель для новых сессий: <b>{esc(cur)}</b>\nВыбери:",
                reply_markup={"inline_keyboard": rows}, thread_id=thread_id,
            )
            return
        await self._set_model(dest, arg)

    async def _set_model(self, dest: tuple[int, Optional[int]], model_id: str) -> None:
        chat_id, thread_id = dest
        if not self.models:
            self.models = await fetch_models()
        valid = {m["id"] for m in self.models}
        # Claude accepts any concrete model id via ANTHROPIC_MODEL, so allow
        # ids that aren't in the fetched picker list (e.g. a fresh snapshot).
        allow_freeform = AGENT_BACKEND == "claude" and (
            model_id == "auto" or model_id.startswith("claude-")
        )
        if valid and model_id not in valid and not allow_freeform:
            await self.tg.send(chat_id, f"❌ Нет такой модели: <code>{esc(model_id)}</code>", thread_id=thread_id)
            return
        self.default_model = None if model_id == "auto" else model_id
        self._save_state()
        await self.tg.send(
            chat_id, f"✅ Модель: <b>{esc(model_id)}</b>\nПрименится при следующем /new.",
            thread_id=thread_id,
        )

    async def cmd_models(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        if not self.models:
            self.models = await fetch_models()
        if not self.models:
            await self.tg.send(chat_id, "Не удалось получить список моделей", thread_id=thread_id)
            return
        lines = ["<b>Доступные модели</b> (кредиты ×rate):"]
        for m in self.models:
            mark = "▶️ " if m["id"] == self.default_model else "• "
            rate = f" <code>×{m['rate']}</code>" if m.get("rate") is not None else ""
            lines.append(f"{mark}<b>{esc(m['name'])}</b>{rate} — {esc(m['desc'])}")
        await self.tg.send(chat_id, "\n".join(lines), thread_id=thread_id)

    async def cmd_here(self, dest: tuple[int, Optional[int]], chat: dict) -> None:
        """Bind the current group as the threads host, verifying by a test topic."""
        chat_id, thread_id = dest
        ctype = chat.get("type")
        is_forum = chat.get("is_forum", False)
        log.info("cmd_here: chat_id=%s type=%s is_forum=%s", chat_id, ctype, is_forum)
        if ctype not in ("supergroup", "group"):
            await self.tg.send(
                chat_id,
                "❌ /here работает только в группе. Создай супергруппу, включи "
                "<b>Темы (Topics)</b>, добавь бота админом с правом «Управление темами», "
                "и снова напиши /here здесь.",
                thread_id=thread_id,
            )
            return

        # verify we can actually create topics (checks forum + admin rights)
        test_thread = await self.tg.create_forum_topic(chat_id, "✅ проверка", icon_color=_TOPIC_COLORS[0])
        if test_thread is None:
            await self.tg.send(
                chat_id,
                "❌ Не смог создать тему. Проверь:\n"
                "1. Группа — <b>супергруппа с включёнными Темами (Topics)</b>\n"
                "2. Бот — <b>админ</b> с правом <b>«Управление темами»</b>\n"
                "Исправь и повтори /here.",
                thread_id=thread_id,
            )
            return
        # success — clean up the test topic and bind
        await self.tg.delete_forum_topic(chat_id, test_thread)
        self.host_chat_id = chat_id
        self._save_state()
        await self.tg.send(
            chat_id,
            "✅ Группа привязана как <b>host тредов</b>.\n"
            "Каждая «🆕 Новая сессия» создаёт свою тему. Пиши запросы внутри темы.",
            reply_markup=self._main_menu_kb(),
            thread_id=thread_id,
        )

    async def cmd_mode_info(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        if self.host_chat_id:
            txt = (
                f"🧵 <b>Threads-режим</b> включён.\nHost-группа: <code>{self.host_chat_id}</code>\n"
                "Каждая /new — своя тема. Отвязать: /nothreads"
            )
        else:
            txt = (
                "💬 <b>Плоский DM-режим</b> — все сессии в одном чате.\n\n"
                "Включить темы (каждая сессия — своя тема прямо здесь, "
                "переключение как между диалогами): <code>/threads</code>\n"
                "Нужен включённый <b>Threaded mode</b> у бота в @BotFather.\n\n"
                "Вариант с отдельной группой: создай супергруппу с Темами, "
                "добавь бота админом с «Управление темами» и напиши там <code>/here</code>."
            )
        await self.tg.send(chat_id, txt, thread_id=thread_id)

    async def cmd_nothreads(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        self.host_chat_id = None
        self.dm_topics_off = True
        self._save_state()
        await self.tg.send(
            chat_id,
            "💬 Темы отключены, вернулся в плоский DM-режим. Включить снова: /threads",
            thread_id=thread_id,
        )

    async def cmd_threads(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        me = await self.tg.call("getMe")
        if not (me.get("ok") and me["result"].get("has_topics_enabled")):
            await self.tg.send(
                chat_id,
                "❌ У бота не включён режим тем. Включи <b>Threaded mode</b> в @BotFather "
                "(Bot Settings), потом снова /threads.",
                thread_id=thread_id,
            )
            return
        self.dm_topics_off = False
        self.host_chat_id = OWNER_ID
        self._save_state()
        await self.tg.send(
            chat_id,
            "🧵 Режим тем включён. Каждая «🆕 Новая сессия» открывает свою тему прямо здесь — "
            "переключайся между ними как между диалогами. Отключить: /nothreads",
            reply_markup=self._main_menu_kb(),
            thread_id=thread_id,
        )

    # ------------- prompt handling -------------
    async def handle_prompt(self, dest: tuple[int, Optional[int]], text: str) -> None:
        chat_id, thread_id = dest
        new_btn = {"inline_keyboard": [[{"text": "🆕 Новая сессия", "callback_data": "m:new"}]]}

        if self.host_chat_id is not None and chat_id == self.host_chat_id:
            # forum group: prompts accepted ONLY inside a session's topic.
            # никогда не создаём сессию/тему автоматически по тексту.
            s = self.thread_session.get(thread_id) if thread_id is not None else None
            if s is None:
                await self.tg.send(
                    chat_id,
                    "✍️ Это общий чат. Нажми «🆕 Новая сессия» — откроется тема, "
                    "и пиши запросы уже внутри неё.",
                    reply_markup=new_btn, thread_id=thread_id,
                )
                return
        else:
            # DM mode: single flat chat, one active session
            s = self.current()
            if s is None:
                await self.tg.send(
                    chat_id, "Нет активной сессии. Создай её кнопкой ниже 👇",
                    reply_markup=new_btn, thread_id=thread_id,
                )
                return

        if not s.alive:
            await self.tg.send(chat_id, "❌ Процесс сессии умер, сделай 🆕 /new", thread_id=thread_id)
            return
        sid = s.session_id or s.name

        q = self.queues.get(sid)
        if q is None:
            q = asyncio.Queue()
            self.queues[sid] = q
        q.put_nowait(text)

        worker = self.workers.get(sid)
        if worker is None or worker.done():
            self.workers[sid] = asyncio.create_task(self._session_worker(s))

        running = self.busy.get(sid)
        busy_now = bool(running and not running.done())
        pending = q.qsize()
        if busy_now or pending > 1:
            ahead = pending - 1 + (1 if busy_now else 0)
            await self.tg.send(chat_id, f"➕ В очередь (перед ним: {ahead}).", thread_id=thread_id)

    async def _session_worker(self, s: KiroSession | CodexSession) -> None:
        sid = s.session_id or s.name
        q = self.queues.get(sid)
        if q is None:
            return
        chat_id, thread_id = self._dest(s)
        while True:
            try:
                text = await q.get()
            except asyncio.CancelledError:
                return
            try:
                if not s.alive:
                    await self.tg.send(chat_id, "❌ Процесс сессии умер, сделай 🆕 /new", thread_id=thread_id)
                    continue
                self.stream.pop(sid, None)
                task = asyncio.create_task(self._run_prompt(s, text))
                self.busy[sid] = task
                try:
                    await task
                except asyncio.CancelledError:
                    await self._close_stream(s)
                    await self.tg.send(chat_id, "🛑 Запрос отменён.", thread_id=thread_id)
            except Exception:
                log.exception("session worker error")
            finally:
                q.task_done()
                self.busy.pop(sid, None)

    def _drain_queue(self, sid: str) -> int:
        q = self.queues.get(sid)
        if q is None:
            return 0
        dropped = 0
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        return dropped

    def _streamed_any(self, s: KiroSession | CodexSession) -> bool:
        sid = s.session_id or s.name
        st = self.stream.get(sid)
        if not st:
            return False
        return bool(st.get("buf") or st.get("msg_id") or st.get("last_sent"))

    async def _run_prompt(self, s: KiroSession | CodexSession, text: str) -> None:
        chat_id, thread_id = self._dest(s)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                stop = await s.prompt(text)
            except (ACPError, CodexError) as exc:
                if _is_auth_error(str(exc)):
                    log.warning("auth error: %s", exc)
                    await self._close_stream(s)
                    if AGENT_BACKEND == "codex":
                        auth_hint = "Проверь `codex login`, затем создай новую 🆕 /new."
                    elif AGENT_BACKEND == "claude":
                        auth_hint = (
                            "Залогинься: запусти `claude` и выполни /login "
                            "(или впиши ANTHROPIC_API_KEY в .env). Затем 🆕 /new."
                        )
                    else:
                        auth_hint = "Ключ в .env (KIRO_API_KEY) — проверь его. Затем 🆕 /new."
                    await self.tg.send(
                        chat_id,
                        f"🔑 <b>Сессия {esc(AGENT_TITLE)} истекла / токен невалиден.</b>\n"
                        f"{auth_hint}\n\n"
                        f"<code>{esc(str(exc)[:300])}</code>",
                        thread_id=thread_id,
                    )
                    return
                transient = _is_transient(str(exc))
                streamed = self._streamed_any(s)
                if transient and s.alive and not streamed and attempt < max_attempts:
                    delay = 2 * attempt
                    log.warning("transient prompt error (%d/%d), retry in %ss: %s",
                                attempt, max_attempts, delay, str(exc)[:200])
                    await self.tg.send(chat_id, f"⚠️ Сбой связи, повтор через {delay}с…", thread_id=thread_id)
                    await asyncio.sleep(delay)
                    continue
                log.warning("prompt error: %s", exc)
                await self._close_stream(s)
                hint = ""
                if transient:
                    hint = "\n(сетевой сбой — попробуй ещё раз)"
                elif not s.alive:
                    hint = "\n(процесс умер — сделай 🆕 /new)"
                await self.tg.send(chat_id, f"❌ {esc(AGENT_TITLE)} error: <code>{esc(exc)}</code>{hint}", thread_id=thread_id)
                if not transient and s.stderr_tail:
                    tail = "\n".join(s.stderr_tail[-15:])
                    await self.tg.send_rich_html(
                        chat_id, f"<blockquote expandable>{esc(tail[:1800])}</blockquote>",
                        thread_id=thread_id,
                    )
                return
            except Exception as exc:
                log.exception("prompt crashed")
                await self._close_stream(s)
                await self.tg.send(chat_id, f"❌ {esc(exc)}", thread_id=thread_id)
                return
            else:
                await self._close_stream(s)
                if stop != "end_turn":
                    await self.tg.send(chat_id, f"⏹ stop: <code>{esc(stop)}</code>", thread_id=thread_id)
                return

    # ------------- callbacks -------------
    async def on_callback(self, cb: dict) -> None:
        data = cb.get("data") or ""
        cb_id = cb["id"]
        msg = cb.get("message") or {}
        chat_id = msg.get("chat", {}).get("id", OWNER_ID)
        thread_id = msg.get("message_thread_id")
        dest = (chat_id, thread_id)

        if data == "noop":
            await self.tg.answer_cb(cb_id)
            return

        if data.startswith("m:"):
            action = data.split(":", 1)[1]
            await self.tg.answer_cb(cb_id)
            if action == "menu":
                await self.show_menu(dest)
            elif action == "new":
                await self.cmd_new(dest, "")
            elif action == "sessions":
                await self.cmd_sessions(dest)
            elif action == "model":
                await self.cmd_model(dest, "")
            elif action == "cancel":
                await self.cmd_cancel(dest)
            elif action == "trust":
                self.trust_all_next = not self.trust_all_next
                self._save_state()
                await self.show_menu(dest)
            elif action == "status":
                await self.cmd_status(dest)
            elif action == "mode":
                await self.cmd_mode_info(dest)
            return

        if data.startswith("s:sw:"):
            idx = int(data.split(":")[2])
            await self.tg.answer_cb(cb_id)
            await self._switch(dest, idx)
            return

        if data.startswith("s:end:"):
            idx = int(data.split(":")[2])
            await self.tg.answer_cb(cb_id, "закрываю")
            await self._end(dest, idx)
            return

        if data.startswith("model:"):
            model_prefix = data.split(":", 1)[1]
            if not self.models:
                self.models = await fetch_models()
            model_id = next(
                (m["id"] for m in self.models if m["id"].startswith(model_prefix)), model_prefix
            )
            mid = msg.get("message_id")
            if mid:
                await self.tg.edit_markup(
                    chat_id, mid,
                    {"inline_keyboard": [[{"text": f"→ {model_id}", "callback_data": "noop"}]]},
                )
            await self._set_model(dest, model_id)
            await self.tg.answer_cb(cb_id, model_id)
            return

        if data.startswith("perm:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                await self.tg.answer_cb(cb_id, "bad data")
                return
            _, token, option_prefix = parts
            entry = self.pending_perms.get(token)
            if not entry:
                await self.tg.answer_cb(cb_id, "устарело")
                return
            fut, opts = entry
            option_id = next((oid for oid in opts if oid.startswith(option_prefix)), None)
            if option_id is None:
                await self.tg.answer_cb(cb_id, "нет опции")
                return
            if not fut.done():
                fut.set_result(option_id)
            picked = opts.get(option_id, {})
            picked_name = picked.get("name") or option_id
            mid = msg.get("message_id")
            if mid:
                await self.tg.edit_markup(
                    chat_id, mid,
                    {"inline_keyboard": [[{"text": f"→ {picked_name}", "callback_data": "noop"}]]},
                )
            await self.tg.answer_cb(cb_id, picked_name)
            return

        await self.tg.answer_cb(cb_id)

    async def cmd_status(self, dest: tuple[int, Optional[int]]) -> None:
        chat_id, thread_id = dest
        lines = [f"{pe('status')} <b>Статус</b>",
                 f"Режим: {pe('chat') + ' Threads' if self.host_chat_id else '💬 DM'}",
                 f"{pe('sessions')} Сессий: {len(self.sessions)}",
                 f"{pe('brain')} Модель: <code>{esc(self.default_model or 'auto')}</code>",
                 f"{pe('gear')} Trust: {'ON' if self.trust_all_next else 'OFF'}"]
        for i, s in enumerate(self.sessions):
            sid = s.session_id or s.name
            q = self.queues.get(sid)
            pending = q.qsize() if q else 0
            running = self.busy.get(sid)
            busy_now = bool(running and not running.done())
            st = "▶️" if i == self.current_idx else "•"
            alive = "" if s.alive else " 💀"
            lines.append(f"{st} <b>{i+1}. {esc(s.name)}</b>{alive} — "
                         f"{'занят' if busy_now else 'idle'}, очередь: {pending}")
        await self.tg.send(chat_id, "\n".join(lines), reply_markup=self._main_menu_kb(), thread_id=thread_id)


# ---------------------------------------------------------------------------
# Update dispatch
# ---------------------------------------------------------------------------
COMMANDS_MENU = [
    {"command": "menu", "description": "Панель управления"},
    {"command": "new", "description": "Новая сессия"},
    {"command": "sessions", "description": "Список сессий"},
    {"command": "model", "description": "Выбрать модель"},
    {"command": "cancel", "description": "Прервать запрос"},
    {"command": "status", "description": "Статус"},
    {"command": "threads", "description": "Сессии в отдельных темах (вкл)"},
    {"command": "nothreads", "description": "Плоский DM-режим (выкл темы)"},
    {"command": "here", "description": "Привязать группу как host тредов"},
    {"command": "trust", "description": "trust on|off"},
]


async def _handle_update(bot: Bot, upd: dict) -> None:
    if "callback_query" in upd:
        cb = upd["callback_query"]
        if cb.get("from", {}).get("id") != OWNER_ID:
            await bot.tg.answer_cb(cb["id"], "not allowed")
            return
        await bot.on_callback(cb)
        return

    msg = upd.get("message")
    if not msg:
        return
    if msg.get("from", {}).get("id") != OWNER_ID:
        return
    chat = msg.get("chat", {})
    chat_id = chat["id"]
    thread_id = msg.get("message_thread_id")
    dest = (chat_id, thread_id)

    # Auto-bind a forum supergroup as the threads host (no manual /here needed).
    if (
        chat.get("type") == "supergroup"
        and chat.get("is_forum")
        and bot.host_chat_id != chat_id
    ):
        bot.host_chat_id = chat_id
        bot._save_state()
        log.info("auto-bound forum host: %s (%s)", chat_id, chat.get("title"))

    text = (msg.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/"):
        head, _, rest = text.partition(" ")
        cmd = head.split("@", 1)[0].lower()
        rest = rest.strip()
        handlers = {
            "/start": lambda: bot.cmd_start(dest),
            "/menu": lambda: bot.show_menu(dest),
            "/help": lambda: bot.cmd_start(dest),
            "/new": lambda: bot.cmd_new(dest, rest),
            "/sessions": lambda: bot.cmd_sessions(dest),
            "/switch": lambda: bot.cmd_switch(dest, rest),
            "/end": lambda: bot.cmd_end(dest, rest),
            "/cwd": lambda: bot.cmd_cwd(dest, rest),
            "/cancel": lambda: bot.cmd_cancel(dest),
            "/queue": lambda: bot.cmd_queue(dest),
            "/trust": lambda: bot.cmd_trust(dest, rest),
            "/model": lambda: bot.cmd_model(dest, rest),
            "/models": lambda: bot.cmd_models(dest),
            "/status": lambda: bot.cmd_status(dest),
            "/here": lambda: bot.cmd_here(dest, chat),
            "/mode": lambda: bot.cmd_mode_info(dest),
            "/threads": lambda: bot.cmd_threads(dest),
            "/nothreads": lambda: bot.cmd_nothreads(dest),
        }
        handler = handlers.get(cmd)
        if handler is None:
            await bot.tg.send(chat_id, f"Неизвестная команда: <code>{esc(cmd)}</code>", thread_id=thread_id)
            return
        await handler()
        return

    await bot.handle_prompt(dest, text)


async def _poll_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    offset = 0
    while not stop_event.is_set():
        try:
            r = await bot.tg.call(
                "getUpdates", offset=offset, timeout=25,
                allowed_updates=["message", "callback_query"],
            )
            if not r.get("ok"):
                await asyncio.sleep(2)
                continue
            for upd in r.get("result") or []:
                offset = upd["update_id"] + 1
                asyncio.create_task(_handle_update(bot, upd))
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("poll loop error")
            await asyncio.sleep(2)


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        bot = Bot(http)
        me = await bot.tg.call("getMe")
        if not me.get("ok"):
            log.error("getMe failed: %s", me)
            return
        u = me["result"]
        # Bot API 9.4+: if the bot has forum topic mode enabled (BotFather),
        # run per-session topics right inside the owner's DM unless opted out.
        if u.get("has_topics_enabled") and bot.host_chat_id is None and not bot.dm_topics_off:
            bot.host_chat_id = OWNER_ID
            bot._save_state()
        log.info("%s-bot @%s id=%s owner=%s threads_host=%s dm_topics=%s",
                 AGENT_BACKEND,
                 u.get("username"), u.get("id"), OWNER_ID, bot.host_chat_id,
                 u.get("has_topics_enabled"))
        bot.models = await fetch_models()
        log.info("models: %s", ", ".join(m["id"] for m in bot.models) or "none")
        await bot.tg.call("deleteWebhook", drop_pending_updates=False)
        await bot.tg.set_my_commands(COMMANDS_MENU)

        stop_event = asyncio.Event()
        poll_task = asyncio.create_task(_poll_loop(bot, stop_event))
        try:
            await poll_task
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            poll_task.cancel()
            for s in list(bot.sessions):
                try:
                    await s.close()
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
