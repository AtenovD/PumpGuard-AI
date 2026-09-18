from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.protections import active_locks
from bot.services.storage import Position, Storage

NOW = 1_800_000_000


class ProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = Storage(":memory:")
        await self.storage.connect()
        patcher = patch("bot.services.protections.config")
        self.cfg = patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg.protect_stoploss_limit = 3
        self.cfg.protect_window_minutes = 240
        self.cfg.protect_lock_minutes = 120

    async def asyncTearDown(self) -> None:
        await self.storage.close()

    async def stop_out(self, mint: str, closed_at: int, reason: str = "stop_loss") -> None:
        await self.storage.open_position(Position(mint, "T", 1.0, 0.1, 0.8, "c", closed_at - 60, "open"))
        await self.storage.close_position(mint, 0.5, reason, closed_at=closed_at)

    async def test_no_history_no_locks(self) -> None:
        self.assertEqual(await active_locks(self.storage, feed_healthy=True, now=NOW), [])

    async def test_a_cluster_of_stop_losses_locks_new_entries(self) -> None:
        for i in range(3):
            await self.stop_out(f"m{i}", NOW - 600 + i)
        locks = await active_locks(self.storage, feed_healthy=True, now=NOW)
        self.assertEqual([lock.name for lock in locks], ["stoploss_guard"])
        self.assertEqual(locks[0].scope, "global")
        self.assertGreater(locks[0].until, NOW)

    async def test_the_lock_expires(self) -> None:
        for i in range(3):
            await self.stop_out(f"m{i}", NOW - 3 * 3600 + i)  # inside the window, lock long over
        self.assertEqual(await active_locks(self.storage, feed_healthy=True, now=NOW), [])

    async def test_stops_outside_the_window_do_not_count(self) -> None:
        for i in range(3):
            await self.stop_out(f"m{i}", NOW - 86400 + i)
        self.assertEqual(await active_locks(self.storage, feed_healthy=True, now=NOW), [])

    async def test_other_exit_reasons_do_not_trigger_the_guard(self) -> None:
        for i in range(3):
            await self.stop_out(f"m{i}", NOW - 600 + i, reason="roi")
        self.assertEqual(await active_locks(self.storage, feed_healthy=True, now=NOW), [])

    async def test_an_unhealthy_feed_locks_entries_until_it_recovers(self) -> None:
        locks = await active_locks(self.storage, feed_healthy=False, now=NOW)
        self.assertEqual([lock.name for lock in locks], ["feed_unhealthy"])
        self.assertIsNone(locks[0].until)

    async def test_unknown_feed_health_does_not_block(self) -> None:
        self.assertEqual(await active_locks(self.storage, feed_healthy=None, now=NOW), [])

    async def test_a_zero_limit_disables_the_guard(self) -> None:
        self.cfg.protect_stoploss_limit = 0
        for i in range(5):
            await self.stop_out(f"m{i}", NOW - 600 + i)
        self.assertEqual(await active_locks(self.storage, feed_healthy=True, now=NOW), [])


if __name__ == "__main__":
    unittest.main()
