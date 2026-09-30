"""Randomised, human-like delays shared by the joiner and the sender.

Every Telegram action gets at least a short randomised beat so the fleet never
fires two requests back-to-back at a fixed interval. Joining a group or channel
gets a longer 20-50 s cooldown.
"""

from __future__ import annotations

import asyncio
import random

# Short pause around any single action (resolve a peer, read history, click a
# button, send a message, check for a gate).
ACTION_MIN = 1.5
ACTION_MAX = 5.0

# Cooldown after joining a group or a channel.
JOIN_MIN = 20.0
JOIN_MAX = 50.0


def rand_action() -> float:
    """A short, random, human-like interval in seconds."""
    return random.uniform(ACTION_MIN, ACTION_MAX)


def rand_join_cooldown() -> float:
    """A 20-50 s cooldown in seconds, to wait after joining a group/channel."""
    return random.uniform(JOIN_MIN, JOIN_MAX)


async def action_pause() -> None:
    """Sleep a short, random, human-like interval before/after an action."""
    await asyncio.sleep(rand_action())


async def join_cooldown() -> None:
    """Sleep the 20-50 s cooldown after joining a group/channel."""
    await asyncio.sleep(rand_join_cooldown())


async def interruptible_sleep(
    total: float,
    stop_event: "asyncio.Event | None" = None,
    step: float = 5.0,
) -> bool:
    """Sleep *total* seconds, waking every *step* to check *stop_event*.

    Returns False if the stop event fired during the wait, True otherwise.
    """
    if stop_event is None:
        await asyncio.sleep(total)
        return True
    remaining = float(total)
    while remaining > 0:
        if stop_event.is_set():
            return False
        chunk = step if remaining > step else remaining
        await asyncio.sleep(chunk)
        remaining -= chunk
    return not stop_event.is_set()
