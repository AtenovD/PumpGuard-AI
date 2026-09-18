from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.executor import DryRunExecutor
from bot.services.models import CurveState, Token
from bot.services.reputation import ReputationBook
from bot.services.scheduler import run_position_watcher
from bot.services.storage import Position, Storage

START = CurveState(2.81, 1_145_000_000.0, 0.0, 100)
MINT = "0xwatched"


class FakeAdapter:
    chain_id = "robinhood"

    def __init__(self, curve: CurveState) -> None:
        self.curve = curve
        self.watched: set[str] = set()

    def watch(self, mint: str) -> None:
        self.watched.add(mint)

    def unwatch(self, mint: str) -> None:
        self.watched.discard(mint)

    def get_price(self, mint: str) -> float | None:
        return self.curve.virtual_eth / self.curve.virtual_tokens

    def get_curve(self, mint: str) -> CurveState | None:
        return self.curve


def market_after_others_buy(eth: float) -> CurveState:
    """The observed reserves after other traders put `eth` more into the curve."""
    k = START.virtual_eth * START.virtual_tokens
    net = eth * 0.99
    return CurveState(START.virtual_eth + net, k / (START.virtual_eth + net), net, 100)


class PositionWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = Storage(":memory:")
        await self.storage.connect()
        buy = await DryRunExecutor().buy(
            Token(MINT, "W", "Watched", "0xcreator", 0, 5, 0, "robinhood", 2.81 / 1_145_000_000.0, curve=START), 0.3
        )
        await self.storage.open_position(Position(
            MINT, "W", buy.price, 0.3, 0.8, "0xcreator", int(time.time()) - 600, "open",
            token_amount=buy.tokens,
        ))
        await self.storage.log_signal(MINT, "W", 0.8, "executor", "bought")
        patcher = patch("bot.services.scheduler.config")
        cfg = patcher.start()
        self.addCleanup(patcher.stop)
        cfg.stop_loss_pct = 35
        cfg.roi_table = "0:100,30:40,120:15,360:0"
        cfg.trailing_activate_pct = 25
        cfg.trailing_stop_pct = 12
        cfg.max_hold_minutes = 720

    async def asyncTearDown(self) -> None:
        await self.storage.close()

    async def one_cycle(self, adapter: FakeAdapter) -> None:
        calls = 0

        async def fake_sleep(_: float) -> None:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise asyncio.CancelledError

        with patch("bot.services.scheduler.asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await run_position_watcher(self.storage, ReputationBook(self.storage), adapter, 0)

    async def test_a_flat_market_keeps_the_position_open_and_does_not_invent_a_loss(self) -> None:
        await self.one_cycle(FakeAdapter(START))
        (position,) = await self.storage.open_positions()
        # Costs alone (~-2%) are nowhere near any exit threshold.
        self.assertEqual(position.status, "open")

    async def test_a_rally_closes_by_roi_with_net_proceeds_recorded(self) -> None:
        # Held 10 minutes, so the table asks for +100%; a big rally provides it.
        await self.one_cycle(FakeAdapter(market_after_others_buy(6.0)))
        self.assertEqual(await self.storage.open_positions(), [])
        (closed,) = await self.storage.closed_positions_since()
        self.assertEqual(closed.close_reason, "roi")
        self.assertGreater(closed.exit_proceeds, 0.3 * 2)
        _, pnl_today = await self.storage.get_daily_counters()
        self.assertAlmostEqual(pnl_today, closed.exit_proceeds - 0.3)

    async def test_a_collapse_closes_by_stop_loss_on_net_value(self) -> None:
        k = START.virtual_eth * START.virtual_tokens
        crashed = CurveState(1.2, k / 1.2, 0.05, 100)  # price down more than half
        await self.one_cycle(FakeAdapter(crashed))
        (closed,) = await self.storage.closed_positions_since()
        self.assertEqual(closed.close_reason, "stop_loss")
        self.assertLess(closed.exit_proceeds, 0.3 * 0.65)

    async def test_the_best_profit_seen_is_persisted_for_the_trailing_stop(self) -> None:
        await self.one_cycle(FakeAdapter(market_after_others_buy(1.0)))
        (position,) = await self.storage.open_positions()  # +~25% is below the +100% target
        self.assertGreater(position.peak_pnl_pct, 0)


if __name__ == "__main__":
    unittest.main()
