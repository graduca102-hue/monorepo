"""Manage Telegram accounts — load .session files, track assigned groups."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCOUNTS_DIR = Path("accounts")
SESSIONS_DIR = Path("sessions")
STATE_FILE = Path("state.json")
CHATS_FILE = Path("chats.txt")


@dataclass
class Account:
    """Represents one purchased Telegram account."""
    item_id: int
    phone: str | None = None
    session_file: str | None = None  # path to .session file
    proxy: str | None = None
    assigned_groups: list[str] = field(default_factory=list)
    joined_groups: list[str] = field(default_factory=list)
    pending_groups: list[str] = field(default_factory=list)  # join-request sent, awaiting approval
    active: bool = True


def _extract_phone(purchase_data: dict[str, Any]) -> str | None:
    """Try to pull phone from LZT purchase JSON."""
    for key in ("loginData", "login_data", "account"):
        block = purchase_data.get(key)
        if isinstance(block, dict):
            for field_name in ("login", "phone", "number"):
                val = block.get(field_name)
                if val:
                    return str(val)
    item = purchase_data.get("item", {})
    if isinstance(item, dict):
        for field_name in ("login", "phone", "emailLoginData"):
            val = item.get(field_name)
            if val:
                return str(val)
    return None


def load_accounts() -> list[Account]:
    """Load accounts from state.json and merge with any new purchases in accounts/.

    state.json stores session/group/proxy state. accounts/ stores raw LZT
    purchase JSONs. On every load we merge so newly bought accounts are picked
    up even if state.json already exists.
    """
    accounts: list[Account] = []
    known_ids: set[int] = set()

    # 1. Load existing state (preserves groups, sessions, proxies)
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text("utf-8"))
            for entry in data.get("accounts", []):
                acc = Account(
                    item_id=entry["item_id"],
                    phone=entry.get("phone"),
                    session_file=entry.get("session_file"),
                    proxy=entry.get("proxy"),
                    assigned_groups=entry.get("assigned_groups", []),
                    joined_groups=entry.get("joined_groups", []),
                    pending_groups=entry.get("pending_groups", []),
                    active=entry.get("active", True),
                )
                accounts.append(acc)
                known_ids.add(acc.item_id)
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. Scan accounts/ directory and add any accounts not yet in state
    if ACCOUNTS_DIR.exists():
        for fp in sorted(ACCOUNTS_DIR.glob("*.json")):
            try:
                item_id = int(fp.stem)
            except ValueError:
                continue
            if item_id in known_ids:
                continue
            try:
                raw = json.loads(fp.read_text("utf-8"))
                phone = _extract_phone(raw.get("purchase", {}))
                session_path = SESSIONS_DIR / f"{item_id}.session"
                accounts.append(Account(
                    item_id=item_id,
                    phone=phone,
                    session_file=str(session_path) if session_path.exists() else None,
                ))
                known_ids.add(item_id)
            except (json.JSONDecodeError,):
                continue

    return accounts


def save_state(accounts: list[Account]) -> None:
    """Persist account state to state.json."""
    data = {
        "accounts": [
            {
                "item_id": a.item_id,
                "phone": a.phone,
                "session_file": a.session_file,
                "proxy": a.proxy,
                "assigned_groups": a.assigned_groups,
                "joined_groups": a.joined_groups,
                "pending_groups": a.pending_groups,
                "active": a.active,
            }
            for a in accounts
        ]
    }
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


GROUPS_PER_ACCOUNT = 20   # chats assigned to one session
REPEAT_MIN_SIZE = 5_000   # only groups above this may be assigned to > 1 session


def assign_groups_to_accounts(accounts: list[Account], groups: list[str]) -> None:
    """Distribute groups across accounts, ``GROUPS_PER_ACCOUNT`` per account max.

    Groups are handed out largest-first (see ``group_sizes``) so the limited
    per-account slots go to big groups (от 10к пдп) before small ones.

    If there are fewer unique groups than total slots
    (``len(active) * GROUPS_PER_ACCOUNT``), the spare slots are filled by
    **repeating** groups onto extra sessions — but only groups larger than
    ``REPEAT_MIN_SIZE`` members, biggest first. A group is never assigned to
    the same session twice.
    """
    active = [a for a in accounts if a.active]
    if not active:
        return

    try:
        from group_sizes import prioritized, cached_size
    except Exception:
        prioritized = lambda gs: list(gs)          # noqa: E731
        cached_size = lambda _g: None              # noqa: E731

    # de-dup, keep largest-first order
    uniq: list[str] = []
    seen: set[str] = set()
    for g in prioritized(list(groups)):
        if g not in seen:
            seen.add(g)
            uniq.append(g)

    cap = GROUPS_PER_ACCOUNT
    n = len(active)

    def _next_free(start: int) -> int | None:
        for k in range(n):
            acc = active[(start + k) % n]
            if len(acc.assigned_groups) < cap:
                return (start + k) % n
        return None

    # ── pass 1: every unique group onto exactly one session ─────────────
    idx = 0
    for group in uniq:
        pos = _next_free(idx)
        if pos is None:
            break  # everyone is full
        acc = active[pos]
        if group not in acc.assigned_groups:
            acc.assigned_groups.append(group)
        idx = pos + 1

    # ── pass 2: repeat big groups into the leftover slots ───────────────
    total_slots = n * cap
    assigned = sum(len(a.assigned_groups) for a in active)
    if assigned < total_slots:
        repeatable = sorted(
            (g for g in uniq if (cached_size(g) or 0) > REPEAT_MIN_SIZE),
            key=lambda g: cached_size(g) or 0,
            reverse=True,
        )
        progressed = bool(repeatable)
        while assigned < total_slots and progressed:
            progressed = False
            for group in repeatable:
                if assigned >= total_slots:
                    break
                for acc in active:
                    if len(acc.assigned_groups) < cap and group not in acc.assigned_groups:
                        acc.assigned_groups.append(group)
                        assigned += 1
                        progressed = True
                        break

    save_state(accounts)


def get_accounts_summary(accounts: list[Account]) -> str:
    """Return a human-readable summary."""
    if not accounts:
        return "📭 Аккаунтов нет"

    lines = [f"📊 Всего аккаунтов: {len(accounts)}"]
    active = [a for a in accounts if a.active]
    lines.append(f"✅ Активных: {len(active)}")

    for i, acc in enumerate(accounts, 1):
        status = "✅" if acc.active else "❌"
        phone_str = acc.phone or "н/д"
        session_str = "📱" if acc.session_file else "⚠️ нет сессии"
        groups_str = f"{len(acc.joined_groups)}/{len(acc.assigned_groups)} групп"
        lines.append(f"  {status} {i}. ID {acc.item_id} | {phone_str} | {session_str} | {groups_str}")

    return "\n".join(lines)


# ─── chats.txt management ───────────────────────────────────────────────────

def load_chats() -> list[str]:
    """Load group links from chats.txt (one per line, skip blanks and comments)."""
    if not CHATS_FILE.exists():
        return []
    lines = CHATS_FILE.read_text("utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def save_chats(chats: list[str]) -> None:
    """Write group links back to chats.txt (deduped, preserving order)."""
    seen: set[str] = set()
    unique: list[str] = []
    for c in chats:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    CHATS_FILE.write_text("\n".join(unique) + "\n", encoding="utf-8")


def add_chats(new_links: list[str]) -> list[str]:
    """Add links to chats.txt, skip duplicates. Returns full list after merge."""
    existing = load_chats()
    existing_set = set(existing)
    added = []
    for link in new_links:
        link = link.strip()
        if link and link not in existing_set:
            existing.append(link)
            existing_set.add(link)
            added.append(link)
    save_chats(existing)
    return existing
