from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

import aiohttp

from bot.services import agents
from bot.services.executor import DryRunExecutor
from bot.services.models import AgentVerdict, CurveState, Token
from bot.services.pipeline import screen_token
from bot.services.reputation import ReputationBook
from bot.services.risk import RiskManager
from bot.services.storage import Storage

CURVE = CurveState(2.81, 1_145_000_000.0, 0.0, 100)
TRADES = {"trade_count": 12, "volume_eth": 1.4, "volume_24h_eth": 0.0}


def make_token(**overrides) -> Token:
    values = dict(
        mint="0xabc", symbol="ABC", name="Abc", creator="0xcreator", native_in_curve=0.1,
        unique_buyers=5, created_at=time.time() - 300, chain="robinhood",
        reference_price=2.81 / 1_145_000_000.0, curve=CURVE, trades=TRADES,
    )
    values.update(overrides)
    return Token(**values)


PASS = AgentVerdict("mock", 0.9, "ok", approve=True)
ABSTAIN = AgentVerdict("mock", 0.0, "abstained: grok call failed", approve=False,
                       fallback=True, abstained=True, abstain_reason="grok call failed")


class VerdictParsingTests(unittest.TestCase):
    def test_score_is_bounded_before_anything_trusts_it(self) -> None:
        self.assertEqual(agents._parse("a", {"score": 5, "approve": True}, "x").score, 1.0)
        self.assertEqual(agents._parse("a", {"score": -3, "approve": True}, "x").score, 0.0)

    def test_non_finite_or_malformed_answers_abstain(self) -> None:
        for bad in ({"score": "nan"}, {"score": "abc"}, {"score": None}):
            verdict = agents._parse("a", bad, "x")
            self.assertTrue(verdict.abstained, bad)
            self.assertFalse(verdict.approve)

    def test_an_unreachable_model_is_an_abstention_not_a_rejection(self) -> None:
        verdict = agents._parse("auditor", None, "grok call failed")
        self.assertTrue(verdict.abstained)
        self.assertTrue(verdict.fallback)  # kept in sync for webhook v1 consumers
        self.assertEqual(verdict.abstain_reason, "grok call failed")

    def test_a_real_rejection_is_not_an_abstention(self) -> None:
        verdict = agents._parse("auditor", {"score": 0.1, "approve": False, "summary": "wash"}, "x")
        self.assertFalse(verdict.abstained)
        self.assertFalse(verdict.approve)


class AuditorDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_trade_data_means_abstain_without_spending_a_model_call(self) -> None:
        with patch("bot.services.agents.ask_grok", AsyncMock()) as ask:
            async with aiohttp.ClientSession() as session:
                for trades in (None, {}, {"trade_count": 1}):
                    verdict = await agents.run_auditor(session, make_token(trades=trades))
                    self.assertTrue(verdict.abstained)
                    self.assertEqual(verdict.abstain_reason, "insufficient_trade_data")
        ask.assert_not_awaited()

    async def test_auditor_is_shown_the_real_trade_statistics(self) -> None:
        ask = AsyncMock(return_value={"score": 0.8, "summary": "fine", "flags": [], "approve": True})
        with patch("bot.services.agents.ask_grok", ask):
            async with aiohttp.ClientSession() as session:
                verdict = await agents.run_auditor(session, make_token())
        self.assertFalse(verdict.abstained)
        sent = json.loads(ask.await_args.args[2])
        self.assertEqual(sent["trades"]["trade_count"], 12)
        self.assertNotIn("curve", sent["token"])  # internals are not sent to the model


class PipelineDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = Storage(":memory:")
        await self.storage.connect()

    async def asyncTearDown(self) -> None:
        await self.storage.close()

    async def run_pipeline(self, token: Token, **verdicts):
        defaults = dict(auditor=PASS, narrative=PASS, timing=PASS, checker=PASS)
        defaults.update(verdicts)
        patches = [
            patch(f"bot.services.pipeline.agents.run_{name}", AsyncMock(return_value=verdict))
            for name, verdict in defaults.items()
        ]
        for p in patches:
            p.start()
        try:
            async with aiohttp.ClientSession() as session:
                return await screen_token(
                    session, self.storage, RiskManager(self.storage), ReputationBook(self.storage),
                    DryRunExecutor(), token, {},
                    feed_healthy=verdicts.pop("feed_healthy", True),
                )
        finally:
            for p in patches:
                p.stop()

    async def signal_rows(self):
        return await self.storage.signals_since()

    async def test_abstention_is_logged_as_abstain_not_as_a_scoring_rejection(self) -> None:
        result = await self.run_pipeline(make_token(), auditor=ABSTAIN)
        self.assertIsNone(result)
        row = (await self.signal_rows())[-1]
        self.assertEqual((row["stage"], row["outcome"]), ("abstain", "abstain"))
        self.assertIn("auditor", row["detail"])
        self.assertEqual(await self.storage.open_position_count(), 0)

    async def test_an_abstention_stops_before_the_remaining_agents_and_checker(self) -> None:
        checker = AsyncMock(return_value=PASS)
        narrative = AsyncMock(return_value=PASS)
        with patch("bot.services.pipeline.agents.run_auditor", AsyncMock(return_value=ABSTAIN)), \
             patch("bot.services.pipeline.agents.run_narrative", narrative), \
             patch("bot.services.pipeline.agents.run_timing", AsyncMock(return_value=PASS)), \
             patch("bot.services.pipeline.agents.run_checker", checker):
            async with aiohttp.ClientSession() as session:
                await screen_token(session, self.storage, RiskManager(self.storage),
                                   ReputationBook(self.storage), DryRunExecutor(), make_token(), {})
        narrative.assert_not_awaited()
        checker.assert_not_awaited()

    async def test_a_genuine_rejection_is_still_a_scoring_skip(self) -> None:
        weak = AgentVerdict("mock", 0.2, "meh", approve=False)
        await self.run_pipeline(make_token(), narrative=weak)
        row = (await self.signal_rows())[-1]
        self.assertEqual((row["stage"], row["outcome"], row["detail"]), ("scoring", "skip", "below_threshold"))

    async def test_every_outcome_leaves_a_decision_record_including_rejections(self) -> None:
        await self.run_pipeline(make_token(mint="0xskip"), narrative=AgentVerdict("m", 0.1, "no", approve=False))
        await self.run_pipeline(make_token(mint="0xbuy"))
        skipped = (await self.storage.recent_decisions(subject="0xskip"))[0]
        bought = (await self.storage.recent_decisions(subject="0xbuy"))[0]
        self.assertEqual(skipped["outcome"], "skip")
        self.assertEqual(bought["outcome"], "bought")
        payload = json.loads(str(skipped["payload"]))
        self.assertIn("narrative", payload["verdicts"])
        self.assertEqual(payload["inputs"]["token"]["trades"]["trade_count"], 12)

    async def test_the_bought_record_carries_the_fill_and_the_risk_journal(self) -> None:
        result = await self.run_pipeline(make_token(mint="0xbuy"))
        self.assertIsNotNone(result)
        self.assertEqual(result.fill["model"], "curve")
        payload = json.loads(str((await self.storage.recent_decisions(subject="0xbuy"))[0]["payload"]))
        self.assertGreater(payload["fill"]["impact_pct"], 0)
        self.assertTrue(payload["risk"]["approved"])
        position = (await self.storage.open_positions())[0]
        self.assertAlmostEqual(position.token_amount, result.fill["tokens"])

    async def test_model_calls_are_captured_in_the_record(self) -> None:
        reply = {"score": 0.9, "summary": "ok", "flags": [], "approve": True}
        with patch("bot.services.grok_client._ask_grok", AsyncMock(return_value=reply)):
            async with aiohttp.ClientSession() as session:
                await screen_token(
                    session, self.storage, RiskManager(self.storage), ReputationBook(self.storage),
                    DryRunExecutor(), make_token(mint="0xcalls"), {"launches_per_minute": 0.1},
                )
        payload = json.loads(str((await self.storage.recent_decisions(subject="0xcalls"))[0]["payload"]))
        agents_called = {call["agent"] for call in payload["model_calls"]}
        self.assertEqual(agents_called, {"auditor", "narrative", "timing", "checker"})
        self.assertTrue(all(len(call["prompt_sha256"]) == 16 for call in payload["model_calls"]))

    async def test_a_crash_is_recorded_and_still_propagates(self) -> None:
        with patch("bot.services.pipeline.agents.run_researcher", AsyncMock(side_effect=RuntimeError("boom"))):
            async with aiohttp.ClientSession() as session:
                with self.assertRaises(RuntimeError):
                    await screen_token(session, self.storage, RiskManager(self.storage),
                                       ReputationBook(self.storage), DryRunExecutor(), make_token(mint="0xcrash"), {})
        record = (await self.storage.recent_decisions(subject="0xcrash"))[0]
        self.assertEqual(record["outcome"], "error")

    async def test_an_unhealthy_feed_blocks_entry_before_any_agent_runs(self) -> None:
        auditor = AsyncMock(return_value=PASS)
        with patch("bot.services.pipeline.agents.run_auditor", auditor):
            async with aiohttp.ClientSession() as session:
                result = await screen_token(
                    session, self.storage, RiskManager(self.storage), ReputationBook(self.storage),
                    DryRunExecutor(), make_token(), {}, feed_healthy=False,
                )
        self.assertIsNone(result)
        auditor.assert_not_awaited()
        row = (await self.signal_rows())[-1]
        self.assertEqual((row["stage"], row["detail"]), ("protection", "feed_unhealthy"))
        payload = json.loads(str((await self.storage.recent_decisions())[0]["payload"]))
        self.assertEqual(payload["locks"][0]["name"], "feed_unhealthy")


if __name__ == "__main__":
    unittest.main()
