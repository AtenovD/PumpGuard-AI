from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.exits import ExitRules, evaluate_exit, parse_roi_table, roi_threshold

RULES = ExitRules(
    stop_loss_pct=35,
    roi_table=parse_roi_table("0:100,30:40,120:15,360:0"),
    trailing_activate_pct=25,
    trailing_stop_pct=12,
    max_hold_minutes=720,
)


class RoiTableTests(unittest.TestCase):
    def test_parses_and_sorts(self) -> None:
        self.assertEqual(parse_roi_table("120:15, 0:100,30:40"), ((0, 100.0), (30, 40.0), (120, 15.0)))

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        # A typo in an env var must never be able to stop the position watcher.
        self.assertEqual(parse_roi_table("0:100,oops,30:"), ((0, 100.0),))
        self.assertEqual(parse_roi_table(""), ())

    def test_threshold_decays_with_time(self) -> None:
        table = RULES.roi_table
        self.assertEqual(roi_threshold(table, 5), 100.0)
        self.assertEqual(roi_threshold(table, 45), 40.0)
        self.assertEqual(roi_threshold(table, 200), 15.0)
        self.assertEqual(roi_threshold(table, 400), 0.0)
        self.assertIsNone(roi_threshold((), 10))


class ExitRuleTests(unittest.TestCase):
    def test_stop_loss(self) -> None:
        self.assertEqual(evaluate_exit(RULES, -40, 0, 10), "stop_loss")
        self.assertIsNone(evaluate_exit(RULES, -20, 0, 10))

    def test_take_profit_follows_the_time_decaying_table(self) -> None:
        self.assertIsNone(evaluate_exit(RULES, 60, 60, 5))  # needs +100% this early
        self.assertEqual(evaluate_exit(RULES, 105, 105, 5), "roi")
        self.assertEqual(evaluate_exit(RULES, 45, 45, 31), "roi")  # needs only +40% now

    def test_trailing_stop_needs_a_run_up_first(self) -> None:
        self.assertEqual(evaluate_exit(RULES, 20, 60, 50), "trailing_stop")
        # Never ran up to the activation level, so a dip is not a trailing exit.
        self.assertIsNone(evaluate_exit(RULES, 2, 10, 10))

    def test_time_stop_closes_what_is_left(self) -> None:
        self.assertEqual(evaluate_exit(RULES, -5, 3, 721), "time_stop")

    def test_capital_is_protected_before_profit_is_taken(self) -> None:
        # Both conditions hold on paper; the stop-loss must win.
        rules = ExitRules(10, ((0, -50.0),), 0, 0, 0)
        self.assertEqual(evaluate_exit(rules, -20, 0, 1), "stop_loss")

    def test_disabled_exits_never_fire(self) -> None:
        rules = ExitRules(35, (), 25, 0, 0)
        self.assertIsNone(evaluate_exit(rules, 500, 500, 99999))


if __name__ == "__main__":
    unittest.main()
