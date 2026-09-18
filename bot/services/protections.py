"""Protections: locks that stop new entries no matter what the agents say.

Modelled on the idea behind Freqtrade's protection plugins (a StoplossGuard
that pauses trading after a cluster of stop-outs) and written from scratch.
The point of a protection layer is that it keeps working when every agent is
wrong in the same direction - it looks only at what actually happened.

A lock carries a scope, a reason and an expiry, so the decision record can say
exactly why an entry was refused and when it will be allowed again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from bot.config import config
from bot.services.storage import Storage


@dataclass(frozen=True)
class ProtectionLock:
    name: str
    scope: str  # "chain" or "global"
    reason: str
    until: int | None  # unix seconds, None while the condition holds


async def active_locks(
    storage: Storage, feed_healthy: bool | None = None, now: int | None = None
) -> list[ProtectionLock]:
    """Every lock currently in force. An empty list means entries are allowed."""
    now = int(time.time()) if now is None else now
    locks: list[ProtectionLock] = []

    if feed_healthy is False:
        # A dry-run entry priced off a stale or missing feed is fiction.
        locks.append(ProtectionLock(
            "feed_unhealthy", "chain", "price feed is not healthy, refusing new entries", None
        ))

    window = config.protect_window_minutes * 60
    stops = await storage.closes_since("stop_loss", now - window)
    if config.protect_stoploss_limit > 0 and len(stops) >= config.protect_stoploss_limit:
        until = max(stops) + config.protect_lock_minutes * 60
        if until > now:
            locks.append(ProtectionLock(
                "stoploss_guard", "global",
                f"{len(stops)} stop-losses within {config.protect_window_minutes} min",
                until,
            ))
    return locks
