from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.risk import RiskManager
from bot.services.storage import Position, Storage


def limits(**overrides):
    values = dict(
        max_eth_per_trade=0.5, daily_loss_limit_eth=2.0, max_total_exposure_eth=1.5,
        max_trades_per_day=10, max_open_positions=5,
    )
    values.update(overrides)
    return values


class RiskClampTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = Storage(":memory:")
        await self.storage.connect()
        self.risk = RiskManager(self.storage)

    async def asyncTearDown(self) -> None:
        await self.storage.close()

    async def evaluate(self, score: float, **overrides):
        with patch("bot.services.risk.config") as cfg:
            for key, value in limits(**overrides).items():
                setattr(cfg, key, value)
            return await self.risk.evaluate(score)

    async def test_plain_conviction_sized_trade_has_no_clamps(self) -> None:
        decision = await self.evaluate(0.6)
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.size_sol, 0.3)
        self.assertEqual(decision.clamps, [])

    async def test_a_score_above_one_cannot_exceed_the_per_trade_cap(self) -> None:
        decision = await self.evaluate(5.0)
        self.assertLessEqual(decision.size_sol, 0.5)
        self.assertEqual(decision.clamps[0].limit, "max_eth_per_trade")
        self.assertAlmostEqual(decision.clamps[0].before, 2.5)

    async def test_daily_budget_clamp_is_journaled(self) -> None:
        await self.storage.record_pnl_only(-1.9)  # only 0.1 ETH of daily budget left
        decision = await self.evaluate(1.0)
        self.assertEqual([c.limit for c in decision.clamps], ["daily_budget_share"])
        self.assertAlmostEqual(decision.clamps[0].after, 0.03, places=4)

    async def test_total_exposure_is_capped_and_not_redistributed(self) -> None:
        for i in range(3):
            await self.storage.open_position(Position(f"m{i}", "T", 1.0, 0.45, 0.8, "c", 1, "open"))
        decision = await self.evaluate(1.0)
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.size_sol, 0.15)  # 1.5 cap - 1.35 committed
        self.assertIn("max_total_exposure", [c.limit for c in decision.clamps])

    async def test_a_size_cut_below_the_minimum_is_a_refusal_with_its_reason(self) -> None:
        await self.storage.open_position(Position("m", "T", 1.0, 1.495, 0.8, "c", 1, "open"))
        decision = await self.evaluate(1.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "position_size_too_small")
        self.assertIn("max_total_exposure", [c.limit for c in decision.clamps])

    async def test_daily_loss_limit_still_refuses_outright(self) -> None:
        await self.storage.record_pnl_only(-2.0)
        decision = await self.evaluate(0.9)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "daily_loss_limit_hit")


if __name__ == "__main__":
    unittest.main()
