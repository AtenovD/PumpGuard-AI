from __future__ import annotations

import os
import time
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.chains.robinhood import RobinhoodAdapter


class _StopLoop(Exception):
    pass


def board_token(address: str, timestamp: float | None) -> dict:
    item = {
        "address": address, "symbol": address[-3:], "name": address, "creator": "0xc",
        "curve": {"virtualEth": str(3 * 10**18), "virtualTokens": str(10**27), "realEth": "0"},
    }
    if timestamp is not None:
        item["timestamp"] = timestamp
    return item


class LaunchAgeGuardTests(unittest.IsolatedAsyncioTestCase):
    """The board can serve a different, much larger dataset than the one the
    bot took its baseline from (hood.fun answered 436 tokens to one client and
    10,630 to another). Anything not in the baseline used to be treated as a new
    launch, so a switch would have flooded the paid pipeline with old tokens."""

    async def emitted(self, adapter: RobinhoodAdapter, second_poll: list[dict]) -> list[str]:
        polls = 0

        async def fake_sleep(_: float) -> None:
            nonlocal polls
            polls += 1
            if polls >= 2:
                raise _StopLoop

        collected: list[str] = []
        baseline = {"tokens": [board_token("0xbase", time.time() - 10)]}
        with patch.object(adapter, "_fetch", AsyncMock(side_effect=[baseline, {"tokens": second_poll}])), \
             patch("bot.services.chains.robinhood.asyncio.sleep", fake_sleep):
            with self.assertRaises(_StopLoop):
                async for token in adapter.stream_new_tokens():
                    collected.append(token.mint)
        return collected

    async def test_only_genuinely_fresh_tokens_are_emitted(self) -> None:
        now = time.time()
        adapter = RobinhoodAdapter("https://example.test", "https://rpc.test", 0, max_launch_age_seconds=3600)
        emitted = await self.emitted(adapter, [
            board_token("0xbase", now - 10),
            board_token("0xfresh", now - 30),
            board_token("0xold", now - 30 * 86400),  # a month old: not a launch
            board_token("0xnostamp", None),           # cannot be proven fresh
        ])
        self.assertEqual(emitted, ["0xfresh"])

    async def test_a_sudden_flood_of_old_tokens_emits_nothing(self) -> None:
        now = time.time()
        adapter = RobinhoodAdapter("https://example.test", "https://rpc.test", 0, max_launch_age_seconds=3600)
        flood = [board_token(f"0xold{i:05d}", now - 86400 * (2 + i % 60)) for i in range(2_000)]
        self.assertEqual(await self.emitted(adapter, flood), [])

    async def test_a_rejected_old_token_is_remembered_not_re_examined(self) -> None:
        adapter = RobinhoodAdapter("https://example.test", "https://rpc.test", 0, max_launch_age_seconds=3600)
        await self.emitted(adapter, [board_token("0xold", time.time() - 10 * 86400)])
        self.assertIn("0xold", adapter._baseline)

    async def test_the_guard_can_be_disabled(self) -> None:
        adapter = RobinhoodAdapter("https://example.test", "https://rpc.test", 0, max_launch_age_seconds=None)
        emitted = await self.emitted(adapter, [board_token("0xold", time.time() - 30 * 86400)])
        self.assertEqual(emitted, ["0xold"])

    def test_default_factory_wires_the_configured_limits(self) -> None:
        from bot.services.chains import build_adapters

        with patch("bot.config.config") as cfg:
            cfg.enabled_chains = ("robinhood",)
            cfg.robinhood_data_url = "https://example.test"
            cfg.robinhood_rpc_url = "https://rpc.test"
            cfg.robinhood_poll_seconds = 12.0
            cfg.max_launch_age_seconds = 900
            (adapter,) = build_adapters()
        self.assertEqual((adapter.poll_interval, adapter.max_launch_age_seconds), (12.0, 900))


if __name__ == "__main__":
    unittest.main()
