from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.backtest import (
    MIN_SAMPLE, bootstrap_mean_ci, position_pnl_pct, run_backtest, sample_verdict,
)
from bot.services.storage import Position, Storage


class BacktestV11Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = Storage(":memory:")
        await self.storage.connect()

    async def asyncTearDown(self) -> None:
        await self.storage.close()

    async def trade(self, mint: str, spent: float, proceeds: float, reason: str) -> None:
        await self.storage.log_signal(mint, mint, 0.8, "executor", "bought")
        await self.storage.open_position(Position(mint, mint, 1.0, spent, 0.8, "c", 1, "open", token_amount=100))
        await self.storage.close_position(mint, 0.9, reason, exit_proceeds=proceeds)

    async def test_abstentions_are_counted_apart_from_rejections(self) -> None:
        await self.storage.log_signal("a", "A", None, "abstain", "abstain", "auditor:insufficient_trade_data")
        await self.storage.log_signal("b", "B", None, "abstain", "abstain", "auditor:insufficient_trade_data")
        await self.storage.log_signal("c", "C", None, "abstain", "abstain", "checker:grok call failed")
        await self.storage.log_signal("d", "D", 0.3, "scoring", "skip", "below_threshold")
        report = await run_backtest(self.storage)
        self.assertEqual(report.total_abstained, 3)
        self.assertEqual(report.total_skipped, 1)  # an outage no longer inflates the rejection funnel
        self.assertEqual(report.skip_by_stage, {"scoring": 1})
        self.assertEqual(report.abstain_by_reason["auditor:insufficient_trade_data"], 2)

    async def test_pnl_uses_net_proceeds_when_they_were_recorded(self) -> None:
        # The price ratio says +50%, but fees and impact turned the sale into a loss.
        await self.storage.open_position(Position("m", "M", 1.0, 0.3, 0.8, "c", 1, "open", token_amount=100))
        await self.storage.log_signal("m", "M", 0.8, "executor", "bought")
        await self.storage.close_position("m", 1.5, "roi", exit_proceeds=0.27)
        report = await run_backtest(self.storage)
        self.assertAlmostEqual(report.avg_pnl_pct, -10.0)
        self.assertAlmostEqual(report.net_pnl_eth, -0.03)

    async def test_legacy_rows_without_proceeds_fall_back_to_the_price_ratio(self) -> None:
        position = Position("m", "M", 2.0, 0.1, 0.8, "c", 1, "closed", exit_price=3.0)
        self.assertAlmostEqual(position_pnl_pct(position), 50.0)

    async def test_exit_reasons_are_broken_down(self) -> None:
        await self.trade("w1", 0.1, 0.16, "roi")
        await self.trade("w2", 0.1, 0.14, "trailing_stop")
        await self.trade("l1", 0.1, 0.06, "stop_loss")
        report = await run_backtest(self.storage)
        self.assertEqual(report.exit_by_reason, {"roi": 1, "trailing_stop": 1, "stop_loss": 1})
        self.assertAlmostEqual(report.win_rate, 2 / 3)  # the report can finally show winners

    async def test_a_small_sample_says_so_instead_of_reporting_a_confident_number(self) -> None:
        for i in range(5):
            await self.trade(f"t{i}", 0.1, 0.13, "roi")
        report = await run_backtest(self.storage)
        self.assertEqual(report.sample_size, 5)
        self.assertIn("too few trades", report.sample_verdict)

    async def test_open_winners_are_shown_rather_than_hidden(self) -> None:
        await self.storage.open_position(Position("open", "OPEN", 1.0, 0.1, 0.8, "c", 1, "open"))
        await self.storage.record_price_snapshot("open", 1.4)
        report = await run_backtest(self.storage)
        self.assertEqual(report.open_positions, 1)
        self.assertAlmostEqual(report.open_spot_avg_pct, 40.0)


class BootstrapTests(unittest.TestCase):
    def test_interval_brackets_the_mean_and_is_reproducible(self) -> None:
        values = [10.0, -5.0, 20.0, 3.0, -8.0, 15.0, 7.0, -2.0]
        low, high = bootstrap_mean_ci(values)
        self.assertLess(low, sum(values) / len(values))
        self.assertGreater(high, sum(values) / len(values))
        self.assertEqual(bootstrap_mean_ci(values), (low, high))

    def test_needs_at_least_two_points(self) -> None:
        self.assertIsNone(bootstrap_mean_ci([]))
        self.assertIsNone(bootstrap_mean_ci([5.0]))

    def test_verdict_wording(self) -> None:
        self.assertIn("no closed", sample_verdict(0, None))
        self.assertIn("too few", sample_verdict(MIN_SAMPLE - 1, (1.0, 2.0)))
        self.assertIn("positive", sample_verdict(MIN_SAMPLE, (1.0, 9.0)))
        self.assertIn("negative", sample_verdict(MIN_SAMPLE, (-9.0, -1.0)))
        self.assertIn("noise", sample_verdict(MIN_SAMPLE, (-4.0, 6.0)))


if __name__ == "__main__":
    unittest.main()
