"""Cyclic message sending — 1 chat per 400-500s, auto-subscribe on gate."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
from typing import Callable, Awaitable

from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.types import KeyboardButtonCallback
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    SlowModeWaitError,
    ChannelPrivateError,
    InviteRequestSentError,
    RPCError,
)

from account_manager import Account, save_state
import group_sizes

log = logging.getLogger(__name__)

DELAY_MIN = 400   # min seconds between chats
DELAY_MAX = 500   # max seconds between chats
SUB_CHECK_DELAY = 4  # wait after sending to check for gate

RESOLVE_CONCURRENCY = 3   # cap simultaneous username resolutions across the fleet
MAX_FLOOD_SLEEP = 600     # never block one account longer than this on a FloodWait
ACCOUNT_START_STAGGER = 4  # seconds between each account's first send

_resolve_sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)

# Rotation weighting: per super-cycle, hit every large group (>=10к пдп)
# PRIORITY_ROUNDS times, then every normal group NORMAL_ROUNDS times.
PRIORITY_ROUNDS = 5
NORMAL_ROUNDS = 1

# Keywords for subscription gate
SUB_KEYWORDS = [
    "подписаться", "подпишись", "подпишитесь", "необходимо подписат",
    "нужно подписат", "subscribe", "вступить", "вступите",
    "присоединиться", "присоединись", "join channel", "join the channel",
]

_USERNAME_RE = re.compile(r"@([a-zA-Z_]\w{3,30})")
_TME_LINK_RE = re.compile(r"(?:https?://)?t\.me/([a-zA-Z_]\w{3,30})")

_stop_event = asyncio.Event()


def request_stop():
    _stop_event.set()


def reset_stop():
    _stop_event.clear()


def is_running() -> bool:
    return not _stop_event.is_set()


# ─── Gate detection & handling ───────────────────────────────────────────────

def _is_sub_gate(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SUB_KEYWORDS)


def _extract_channels(text: str) -> list[str]:
    channels: list[str] = []
    for m in _TME_LINK_RE.finditer(text):
        uname = m.group(1).lower()
        if uname not in ("joinchat", "addstickers", "share", "proxy", "socks"):
            ch = f"@{uname}"
            if ch not in channels:
                channels.append(ch)
    for m in _USERNAME_RE.finditer(text):
        ch = f"@{m.group(1).lower()}"
        if ch not in channels:
            channels.append(ch)
    return channels


def _extract_channels_from_buttons(reply_markup) -> list[str]:
    channels: list[str] = []
    rows = getattr(reply_markup, "rows", None)
    if not rows:
        return channels
    for row in rows:
        for btn in row.buttons:
            url = getattr(btn, "url", None)
            if url:
                for ch in _extract_channels(url):
                    if ch not in channels:
                        channels.append(ch)
            btn_text = getattr(btn, "text", "")
            if btn_text:
                for ch in _extract_channels(btn_text):
                    if ch not in channels:
                        channels.append(ch)
    return channels


async def _handle_gate(
    client: TelegramClient,
    account: Account,
    gate_msg,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> bool:
    """Subscribe to channels from gate message. Returns True if subscribed to anything."""
    text = gate_msg.text or ""
    channels = _extract_channels(text)
    channels.extend(ch for ch in _extract_channels_from_buttons(gate_msg.reply_markup) if ch not in channels)

    if not channels:
        return False

    joined_any = False
    for ch in channels:
        try:
            await client(JoinChannelRequest(ch))
            joined_any = True
            log.info(f"🔔 [{account.item_id}] Подписался на {ch}")
            if progress_cb:
                await progress_cb(f"🔔 [{account.item_id}] Подписался → {ch}")
        except (ChannelPrivateError, UserBannedInChannelError):
            pass
        except InviteRequestSentError:
            log.info(f"📨 [{account.item_id}] Заявка на вступление в gate-канал {ch} отправлена")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            try:
                await client(JoinChannelRequest(ch))
                joined_any = True
            except Exception:
                pass
        except Exception as e:
            log.warning(f"❌ [{account.item_id}] Подписка {ch}: {e}")

    # Press "check" button if exists
    if gate_msg.reply_markup and hasattr(gate_msg.reply_markup, "rows"):
        for row in gate_msg.reply_markup.rows:
            for btn in row.buttons:
                if isinstance(btn, KeyboardButtonCallback):
                    btn_lower = (btn.text or "").lower()
                    if any(w in btn_lower for w in ["подписа", "проверить", "check", "вступил"]):
                        try:
                            await gate_msg.click(data=btn.data)
                            if progress_cb:
                                await progress_cb(f"🔘 [{account.item_id}] Нажал '{btn.text}'")
                        except Exception:
                            pass

    return joined_any


# ─── Send to one chat with gate handling ─────────────────────────────────────

async def _join_linked_channel(
    client: TelegramClient,
    account: Account,
    target,
    group: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> bool:
    """Discussion groups often require a subscription to their linked channel
    before a member may post. Resolve that channel and join it. Returns True
    if we joined something."""
    try:
        full = await client(GetFullChannelRequest(target))
    except Exception:
        return False
    linked_id = getattr(full.full_chat, "linked_chat_id", None)
    if not linked_id:
        return False
    for ch in getattr(full, "chats", []):
        if getattr(ch, "id", None) == linked_id:
            try:
                await client(JoinChannelRequest(ch))
                if progress_cb:
                    await progress_cb(f"🔔 [{account.item_id}] Подписался на канал группы {group}")
                return True
            except FloodWaitError as e:
                await asyncio.sleep(min(e.seconds, MAX_FLOOD_SLEEP))
                try:
                    await client(JoinChannelRequest(ch))
                    return True
                except Exception:
                    return False
            except Exception:
                return False
    return False


async def _msg_alive(client: TelegramClient, target, msg_id: int) -> bool:
    """True unless our just-sent message was deleted (silent anti-spam gate)."""
    try:
        m = await client.get_messages(target, ids=msg_id)
        return m is not None
    except Exception:
        return True


async def _send_to_chat(
    client: TelegramClient,
    account: Account,
    group: str,
    message: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Send message to one chat. Handle gate / silent deletion → subscribe → resend.

    Returns "ok" or "retry". A chat is NEVER dropped from the rotation here —
    a chat that blocks us today (needs a channel sub, slow mode, temporary
    restriction) is retried on the next cycle.
    """
    # Resolve the peer. Joins done in a separate process leave the peer unknown
    # to this session; resolving 100+ usernames at once floods Telegram, so the
    # lookup is throttled by a fleet-wide semaphore. If it can't resolve now,
    # the chat simply waits for the next cycle — it is never dropped.
    target = None
    try:
        async with _resolve_sem:
            target = await client.get_input_entity(group)
    except FloodWaitError:
        if progress_cb:
            await progress_cb(f"⏳ [{account.item_id}] {group}: резолв flood — позже")
        return "retry"
    except Exception as e:
        log.warning(f"❓ [{account.item_id}] Не резолвится {group}: {e}")
        return "retry"

    try:
        try:
            sent_msg = await client.send_message(target, message)
        except (ChatWriteForbiddenError, UserBannedInChannelError):
            # Maybe posting just needs a subscription to the linked channel.
            if await _join_linked_channel(client, account, target, group, progress_cb):
                await asyncio.sleep(3)
                try:
                    sent_msg = await client.send_message(target, message)
                except Exception:
                    if progress_cb:
                        await progress_cb(f"🚫 [{account.item_id}] {group}: нет прав на отправку — позже")
                    return "retry"
            else:
                if progress_cb:
                    await progress_cb(f"🚫 [{account.item_id}] {group}: нет прав на отправку — позже")
                return "retry"

        log.info(f"📤 [{account.item_id}] → {group}")
        if progress_cb:
            await progress_cb(f"📤 [{account.item_id}] → {group}")

        sent_msg_id = getattr(sent_msg, "id", None)
        if sent_msg_id is None:
            log.warning(
                f"⚠️ [{account.item_id}] {group}: send_message returned no message id; "
                "skipping gate check for this send"
            )
            if progress_cb:
                await progress_cb(
                    f"⚠️ [{account.item_id}] {group}: Telegram не вернул id сообщения, "
                    "проверку gate пропускаю"
                )
            return "ok"

        # Wait, then look for a gate message or a silent deletion.
        await asyncio.sleep(SUB_CHECK_DELAY)

        gate_msg = None
        async for msg in client.iter_messages(target, limit=5):
            if msg.id <= sent_msg_id:
                break
            if _is_sub_gate(msg.text or ""):
                gate_msg = msg
                break

        deleted = not await _msg_alive(client, target, sent_msg_id)

        if gate_msg or deleted:
            if progress_cb:
                reason = "gate" if gate_msg else "сообщение удалено"
                await progress_cb(f"🚧 [{account.item_id}] {group}: {reason} — проверяю подписку")

            subscribed = False
            if gate_msg is not None:
                subscribed = await _handle_gate(client, account, gate_msg, progress_cb)
            if not subscribed:
                subscribed = await _join_linked_channel(client, account, target, group, progress_cb)

            if subscribed:
                await asyncio.sleep(3)
                try:
                    await client.send_message(target, message)
                    if progress_cb:
                        await progress_cb(f"📤 [{account.item_id}] → {group} (повтор после подписки)")
                except Exception as e:
                    log.warning(f"❌ [{account.item_id}] Повтор {group}: {e}")

        return "ok"

    except FloodWaitError as e:
        secs = min(e.seconds, MAX_FLOOD_SLEEP)
        log.warning(f"⏳ [{account.item_id}] FloodWait {e.seconds}с (жду {secs})")
        if progress_cb:
            await progress_cb(f"⏳ [{account.item_id}] FloodWait {e.seconds}с")
        await asyncio.sleep(secs)
        return "retry"
    except SlowModeWaitError as e:
        if progress_cb:
            await progress_cb(f"⏳ [{account.item_id}] SlowMode {e.seconds}с в {group}")
        return "retry"
    except ChannelPrivateError:
        if progress_cb:
            await progress_cb(f"🚫 [{account.item_id}] {group}: приватный/бан — пропускаю, попробую позже")
        return "retry"
    except RPCError as e:
        msg_txt = (getattr(e, "message", "") or str(e))
        log.error(f"❌ [{account.item_id}] RPCError {group}: {e}")
        if "invalid peer" in msg_txt.lower():
            if progress_cb:
                await progress_cb(f"🚫 [{account.item_id}] {group}: невалидный чат/peer")
            return "dead"
        if progress_cb:
            await progress_cb(f"❌ [{account.item_id}] {group}: {msg_txt}")
        return "retry"
    except Exception as e:
        log.error(f"❌ [{account.item_id}] Ошибка {group}: {e}")
        if progress_cb:
            await progress_cb(f"❌ [{account.item_id}] {group}: {e}")
        return "retry"


# ─── Account loop: chats one by one, 400-500s between each ──────────────────

async def _account_loop(
    client: TelegramClient,
    account: Account,
    message: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    all_accounts: list[Account] | None = None,
    start_delay: float = 0.0,
) -> None:
    """Infinite loop for ONE account.

    Chats are weighted by size: in each super-cycle every large group
    (>= group_sizes.PRIORITY_THRESHOLD members, «от 10к пдп») is messaged
    PRIORITY_ROUNDS times, then every normal group NORMAL_ROUNDS times — so
    the software makes 5 passes over the big groups per 1 pass over the rest.
    400-500s pause between every send is unchanged. Sizes are unknown until
    they are resolved, so a cold cache degrades to a single plain round.
    """
    if not account.joined_groups:
        if progress_cb:
            await progress_cb(f"⚠️ [{account.item_id}] Нет групп для рассылки")
        return

    # Stagger the fleet so 100+ accounts don't resolve/send in one burst.
    for _ in range(int(start_delay) // 5):
        if _stop_event.is_set():
            return
        await asyncio.sleep(5)
    if not _stop_event.is_set():
        await asyncio.sleep(start_delay % 5)

    async def _wait_between_chats() -> bool:
        """Sleep DELAY_MIN..DELAY_MAX, checking the stop flag. False = stop."""
        delay = random.randint(DELAY_MIN, DELAY_MAX)
        if progress_cb:
            await progress_cb(f"⏳ [{account.item_id}] Жду {delay}с до следующего чата...")
        for _ in range(delay // 5):
            if _stop_event.is_set():
                return False
            await asyncio.sleep(5)
        remaining = delay % 5
        if remaining and not _stop_event.is_set():
            await asyncio.sleep(remaining)
        return not _stop_event.is_set()

    async def _send_pass(groups: list[str], tag: str) -> bool:
        """One pass over *groups*. Returns False if we should stop."""
        for group in groups:
            if _stop_event.is_set():
                return False
            # Chat may have been dropped as "dead" in an earlier pass.
            if group not in account.joined_groups:
                continue

            status = await _send_to_chat(client, account, group, message, progress_cb)

            if status == "dead":
                try:
                    account.joined_groups.remove(group)
                except ValueError:
                    pass
                if group in account.assigned_groups:
                    account.assigned_groups.remove(group)
                # Persist the FULL list so we don't wipe other accounts' state.
                save_state(all_accounts if all_accounts is not None else [account])
                continue

            # Cheaply learn this group's size for future prioritisation.
            try:
                await group_sizes.resolve_size(client, group)
            except FloodWaitError:
                pass
            except Exception:
                pass

            if not await _wait_between_chats():
                return False
        return True

    cycle = 0
    while not _stop_event.is_set():
        if not account.joined_groups:
            if progress_cb:
                await progress_cb(f"⚠️ [{account.item_id}] Не осталось доступных чатов — стоп")
            return

        cycle += 1
        big, normal = group_sizes.split_priority(list(account.joined_groups))
        thr = group_sizes.PRIORITY_THRESHOLD // 1000
        if progress_cb:
            if big:
                await progress_cb(
                    f"🔄 [{account.item_id}] Цикл #{cycle}: {len(big)} крупных "
                    f"(≥{thr}к) ×{PRIORITY_ROUNDS} кругов, затем {len(normal)} обычных ×{NORMAL_ROUNDS}"
                )
            else:
                await progress_cb(
                    f"🔄 [{account.item_id}] Цикл #{cycle}: {len(normal)} чатов "
                    f"(крупных ≥{thr}к пока нет — обычный круг)"
                )

        for r in range(PRIORITY_ROUNDS):
            if not big:
                break
            if progress_cb:
                await progress_cb(f"⭐ [{account.item_id}] Крупные группы, круг {r + 1}/{PRIORITY_ROUNDS}")
            if not await _send_pass(big, "big"):
                return
            # refresh in case a big group died mid-cycle
            big = [g for g in big if g in account.joined_groups]

        for r in range(NORMAL_ROUNDS):
            if not normal:
                break
            if progress_cb and NORMAL_ROUNDS > 1:
                await progress_cb(f"• [{account.item_id}] Обычные группы, круг {r + 1}/{NORMAL_ROUNDS}")
            if not await _send_pass(normal, "normal"):
                return
            normal = [g for g in normal if g in account.joined_groups]


# ─── Entry point ─────────────────────────────────────────────────────────────

async def send_messages_loop(
    clients: dict[int, TelegramClient],
    accounts: list[Account],
    message: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """All accounts work in parallel and newly connected clients are picked up live."""
    reset_stop()
    announced = False
    started_ids: set[int] = set()
    tasks: dict[int, asyncio.Task] = {}

    def _eligible_accounts() -> list[Account]:
        return [
            a for a in accounts
            if a.active and a.joined_groups and a.item_id in clients
        ]

    try:
        while not _stop_event.is_set():
            active = _eligible_accounts()

            if active and not announced:
                total_chats = sum(len(a.joined_groups) for a in active)
                if progress_cb:
                    thr = group_sizes.PRIORITY_THRESHOLD // 1000
                    await progress_cb(
                        f"🚀 Рассылка: {len(active)} аккаунтов × {total_chats} чатов\n"
                        f"Режим: крупные группы (≥{thr}к пдп) ×{PRIORITY_ROUNDS} круга → "
                        f"обычные ×{NORMAL_ROUNDS}, пауза 400-500с между чатами\n"
                        f"Авто-подписка на gate-каналы"
                    )
                announced = True

            for item_id, task in list(tasks.items()):
                if task.done():
                    tasks.pop(item_id, None)
                    with contextlib.suppress(Exception):
                        task.result()

            new_accounts = [a for a in active if a.item_id not in started_ids]
            start_index = len(started_ids)
            for offset, acc in enumerate(new_accounts):
                started_ids.add(acc.item_id)
                tasks[acc.item_id] = asyncio.create_task(
                    _account_loop(
                        clients[acc.item_id],
                        acc,
                        message,
                        progress_cb,
                        accounts,
                        start_delay=(start_index + offset) * ACCOUNT_START_STAGGER,
                    )
                )
                if progress_cb and announced:
                    await progress_cb(
                        f"➕ [{acc.item_id}] Подключился к активной рассылке "
                        f"({len(acc.joined_groups)} чатов)"
                    )

            if announced and tasks:
                await asyncio.sleep(5)
                continue

            if announced and not tasks:
                break

            await asyncio.sleep(2)
    finally:
        for task in tasks.values():
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        if progress_cb:
            await progress_cb("🛑 Рассылка остановлена.")
