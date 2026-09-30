"""Join groups on Telegram accounts — parallel, 3 at a time per account, 5-min cooldown."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Awaitable

from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UserBannedInChannelError,
    ChatWriteForbiddenError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    ChannelsTooMuchError,
    UserChannelsTooMuchError,
    InviteHashExpiredError,
    InviteHashInvalidError,
)

# Permanent join failures — drop the link from the account's assignment.
_DEAD_JOIN_ERRORS = (
    ChannelPrivateError,
    UserBannedInChannelError,
    ChatWriteForbiddenError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InviteHashExpiredError,
    InviteHashInvalidError,
)
# Account can't join anything more right now — stop this account's run.
_ACCOUNT_FULL_ERRORS = (ChannelsTooMuchError, UserChannelsTooMuchError)

# All join workers share one event loop — serialise + throttle state writes so
# 100+ accounts don't stampede state.json after each batch.
_save_lock = asyncio.Lock()
_last_save = 0.0
_SAVE_MIN_INTERVAL = 15


async def _persist_state(accounts: list[Account] | None, force: bool = False) -> None:
    global _last_save
    if accounts is None:
        return
    async with _save_lock:
        now = time.monotonic()
        if not force and now - _last_save < _SAVE_MIN_INTERVAL:
            return
        try:
            save_state(accounts)
            _last_save = now
        except Exception as e:
            log.debug(f"state save skipped: {e}")

from account_manager import Account, save_state, GROUPS_PER_ACCOUNT

log = logging.getLogger(__name__)

BATCH_SIZE = 3          # join 3 groups at a time
BATCH_COOLDOWN = 300    # 5 minutes between batches
MAX_GROUPS = GROUPS_PER_ACCOUNT   # max groups per account (kept in sync with distribution)


async def join_groups_for_account(
    client: TelegramClient,
    account: Account,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    all_accounts: list[Account] | None = None,
) -> list[str]:
    """Join assigned groups in batches of 3 with 5-min cooldown.

    Each account runs independently in its own coroutine. Progress is persisted
    after every batch so an interrupt / restart never loses joined chats.
    Returns list of successfully joined group links.
    """
    # Pending (join-request) groups are retried too — the request may have been
    # approved since last run; if not, InviteRequestSentError just keeps them pending.
    joined_set = set(account.joined_groups)
    to_join = [g for g in account.assigned_groups if g not in joined_set]
    try:
        from group_sizes import prioritized
        to_join = prioritized(to_join)  # big groups (от 10к пдп) joined first
    except Exception:
        pass
    if not to_join:
        if progress_cb:
            await progress_cb(f"ℹ️ [{account.item_id}] Все группы уже вступлены")
        return account.joined_groups

    joined: list[str] = []

    def _mark_joined(link: str) -> None:
        if link not in account.joined_groups:
            account.joined_groups.append(link)
        if link in account.pending_groups:
            account.pending_groups.remove(link)
        joined.append(link)

    def _drop(link: str) -> None:
        """Permanently unassign a link that will never work for this account."""
        for lst in (account.assigned_groups, account.pending_groups):
            if link in lst:
                lst.remove(link)

    async def _try_join(link: str) -> str:
        """Returns: 'ok' | 'pending' | 'dead' | 'retry' | 'stop'."""
        try:
            await client(JoinChannelRequest(link))
            _mark_joined(link)
            log.info(f"✅ [{account.item_id}] Вступил в {link}")
            if progress_cb:
                await progress_cb(f"✅ [{account.item_id}] Вступил в {link}")
            return "ok"
        except UserAlreadyParticipantError:
            _mark_joined(link)
            if progress_cb:
                await progress_cb(f"✅ [{account.item_id}] Уже участник {link}")
            return "ok"
        except InviteRequestSentError:
            if link not in account.pending_groups:
                account.pending_groups.append(link)
            log.info(f"📨 [{account.item_id}] Заявка на вступление отправлена: {link}")
            if progress_cb:
                await progress_cb(f"📨 [{account.item_id}] Заявка отправлена (ждёт одобрения): {link}")
            return "pending"
        except FloodWaitError as e:
            log.warning(f"⏳ [{account.item_id}] FloodWait {e.seconds}с для {link}")
            if progress_cb:
                await progress_cb(f"⏳ [{account.item_id}] FloodWait {e.seconds}с для {link} — жду")
            await asyncio.sleep(e.seconds)
            return "retry"
        except _ACCOUNT_FULL_ERRORS:
            log.warning(f"🚫 [{account.item_id}] Аккаунт в слишком многих каналах — стоп")
            if progress_cb:
                await progress_cb(f"🚫 [{account.item_id}] Лимит каналов на аккаунте — останавливаю вступление")
            return "stop"
        except _DEAD_JOIN_ERRORS as e:
            _drop(link)
            log.warning(f"⚠️ [{account.item_id}] Битая/недоступная ссылка {link}: {e}")
            if progress_cb:
                await progress_cb(f"⚠️ [{account.item_id}] {link} недоступна — убрал из списка")
            return "dead"
        except ValueError as e:
            # Telethon raises ValueError('No user has "x" as username') for dead @links.
            _drop(link)
            log.warning(f"⚠️ [{account.item_id}] Нет такой группы {link}: {e}")
            if progress_cb:
                await progress_cb(f"⚠️ [{account.item_id}] {link} не существует — убрал из списка")
            return "dead"
        except Exception as e:
            log.error(f"❌ [{account.item_id}] Ошибка при вступлении в {link}: {e}")
            if progress_cb:
                await progress_cb(f"❌ [{account.item_id}] {link}: {e}")
            return "retry"

    for i in range(0, len(to_join), BATCH_SIZE):
        batch = to_join[i : i + BATCH_SIZE]

        for link in batch:
            result = await _try_join(link)
            if result == "stop":
                await _persist_state(all_accounts, force=True)
                return joined
            if result == "retry":
                # one more attempt (FloodWaitError already slept the wait out)
                if await _try_join(link) == "stop":
                    await _persist_state(all_accounts, force=True)
                    return joined

        await _persist_state(all_accounts)  # save state after every batch (throttled)

        remaining = to_join[i + BATCH_SIZE :]
        if remaining:
            log.info(f"⏳ [{account.item_id}] Кулдаун 5 мин перед следующей партией...")
            if progress_cb:
                await progress_cb(f"⏳ [{account.item_id}] Кулдаун 5 мин перед следующей партией...")
            await asyncio.sleep(BATCH_COOLDOWN)

    return joined


async def join_groups_all_accounts(
    clients: dict[int, TelegramClient],
    accounts: list[Account],
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Run join_groups_for_account for every active account IN PARALLEL."""
    active = [a for a in accounts if a.active and a.item_id in clients]

    log.info(f"join_groups_all_accounts: {len(accounts)} total, {len(active)} active, clients={list(clients.keys())}")
    for a in active:
        log.info(f"  acc {a.item_id}: assigned={len(a.assigned_groups)} joined={len(a.joined_groups)}")

    if not active:
        if progress_cb:
            await progress_cb("⚠️ Нет активных аккаунтов с подключёнными клиентами")
        return

    if progress_cb:
        await progress_cb(f"🚀 Запускаю вступление параллельно на {len(active)} аккаунтах...")

    async def _worker(acc: Account):
        try:
            client = clients[acc.item_id]
            await join_groups_for_account(client, acc, progress_cb, accounts)
        except Exception as e:
            log.error(f"❌ [{acc.item_id}] Ошибка в worker: {e}")
            if progress_cb:
                await progress_cb(f"❌ [{acc.item_id}] Ошибка: {e}")

    await asyncio.gather(*[_worker(acc) for acc in active])
    save_state(accounts)

    if progress_cb:
        total_joined = sum(len(a.joined_groups) for a in active)
        total_pending = sum(len(a.pending_groups) for a in active)
        tail = f" | заявок в ожидании: {total_pending}" if total_pending else ""
        await progress_cb(
            f"✅ Все аккаунты завершили вступление — вступлено: {total_joined}{tail}"
        )
