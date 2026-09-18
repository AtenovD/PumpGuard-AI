from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.chains.robinhood import RobinhoodAdapter
from bot.services.nft.adapter import RobinhoodNftAdapter
from bot.services.storage import Position, Storage

ADDRESS = "0xE1F7D1f4f2dA02cb295f526f1Fd88e1b3cEb600D"

# Shaped like a real /api/board response (fields and units copied from the live payload).
BOARD_ITEM = {
    "address": ADDRESS,
    "curve": {
        "virtualEth": str(2_810_000_000_000_000_000),
        "virtualTokens": str(1_145_000_000 * 10**18),
        "realEth": str(50_000_000_000_000_000),
        "tradeFeeBps": 100,
        "graduated": False,
        "migrated": False,
    },
}
ACTIVITY = {
    "byToken": {
        ADDRESS.lower(): {
            "volumeWei": str(1_400_000_000_000_000_000), "vol4hWei": "0", "vol24hWei": "0",
            "tradeCount": 12, "lastTradeBlock": "12287617", "lastBuyBlock": "12270315",
        }
    },
    "recent": [
        {"token": ADDRESS, "trader": "0xA", "isBuy": True, "ethAmount": str(3 * 10**16), "blockNumber": "1"},
        {"token": ADDRESS, "trader": "0xa", "isBuy": True, "ethAmount": str(10**16), "blockNumber": "2"},
        {"token": ADDRESS, "trader": "0xB", "isBuy": False, "ethAmount": str(10**16), "blockNumber": "3"},
        {"token": "0xother", "trader": "0xC", "isBuy": True, "ethAmount": str(10**18), "blockNumber": "4"},
    ],
}


class AdapterParsingTests(unittest.TestCase):
    def test_curve_units_and_fee(self) -> None:
        curve = RobinhoodAdapter._curve(BOARD_ITEM)
        self.assertAlmostEqual(curve.virtual_eth, 2.81)
        self.assertAlmostEqual(curve.virtual_tokens, 1_145_000_000.0)
        self.assertAlmostEqual(curve.real_eth, 0.05)
        self.assertEqual(curve.fee_bps, 100)

    def test_graduated_or_missing_curves_have_no_curve_model(self) -> None:
        self.assertIsNone(RobinhoodAdapter._curve({**BOARD_ITEM, "pairPriceWei": "123456789"}))
        graduated = {**BOARD_ITEM, "curve": {**BOARD_ITEM["curve"], "graduated": True}}
        self.assertIsNone(RobinhoodAdapter._curve(graduated))
        self.assertIsNone(RobinhoodAdapter._curve({"address": ADDRESS}))

    def test_trade_stats_from_the_activity_block(self) -> None:
        stats = RobinhoodAdapter._trade_stats(ADDRESS, ACTIVITY)
        self.assertEqual(stats["trade_count"], 12)
        self.assertAlmostEqual(stats["volume_eth"], 1.4)
        sample = stats["recent_sample"]
        self.assertEqual((sample["trades"], sample["buys"], sample["sells"]), (3, 2, 1))
        # Trader addresses differ only in case, so they are one wallet.
        self.assertEqual(sample["unique_traders"], 2)
        self.assertAlmostEqual(sample["top_trader_share"], 0.8)

    def test_no_activity_for_a_token_means_no_stats_not_fake_zeros(self) -> None:
        self.assertIsNone(RobinhoodAdapter._trade_stats(ADDRESS, None))
        self.assertIsNone(RobinhoodAdapter._trade_stats("0xunknown", ACTIVITY))

    def test_malformed_activity_is_ignored(self) -> None:
        bad = {"byToken": {ADDRESS.lower(): {"tradeCount": "many", "volumeWei": "x"}}}
        self.assertIsNone(RobinhoodAdapter._trade_stats(ADDRESS, bad))

    def test_watched_token_curve_is_kept_for_exit_pricing(self) -> None:
        adapter = RobinhoodAdapter("https://example.test", "https://rpc.test")
        adapter.watch(ADDRESS)
        adapter._update_prices([{**BOARD_ITEM, "pairPriceWei": None}])
        self.assertIsNotNone(adapter.get_curve(ADDRESS))
        adapter.unwatch(ADDRESS)
        self.assertIsNone(adapter.get_curve(ADDRESS))


class MigrationV11Tests(unittest.IsolatedAsyncioTestCase):
    async def test_a_v10_database_gains_the_new_columns_and_keeps_its_rows(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = handle.name
        handle.close()
        try:
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE positions (
                    chain TEXT NOT NULL DEFAULT 'robinhood', mint TEXT NOT NULL, symbol TEXT,
                    entry_price REAL NOT NULL, sol_spent REAL NOT NULL, score REAL NOT NULL,
                    creator TEXT, opened_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                    exit_price REAL, closed_at INTEGER, close_reason TEXT, PRIMARY KEY (chain, mint)
                );
                INSERT INTO positions (mint, symbol, entry_price, sol_spent, score, creator, opened_at)
                    VALUES ('legacy', 'OLD', 2.0, 0.1, 0.8, 'c', 5);
                """
            )
            connection.commit()
            connection.close()

            storage = Storage(path)
            await storage.connect()
            columns = await storage._columns("positions")
            self.assertTrue({"token_amount", "peak_pnl_pct", "exit_proceeds"} <= columns)
            (position,) = await storage.open_positions()
            self.assertEqual(position.mint, "legacy")
            self.assertIsNone(position.token_amount)
            self.assertEqual(position.peak_pnl_pct, 0.0)
            # Reconnecting must be a no-op, not a duplicate-column error.
            await storage.close()
            again = Storage(path)
            await again.connect()
            await again.close()
        finally:
            os.unlink(path)

    async def test_position_peak_and_exposure_helpers(self) -> None:
        storage = Storage(":memory:")
        await storage.connect()
        await storage.open_position(Position("a", "A", 1.0, 0.25, 0.8, "c", 1, "open"))
        await storage.open_position(Position("b", "B", 1.0, 0.5, 0.8, "c", 1, "open"))
        self.assertAlmostEqual(await storage.open_exposure(), 0.75)
        await storage.update_position_peak("a", 42.5)
        positions = {p.mint: p for p in await storage.open_positions()}
        self.assertEqual(positions["a"].peak_pnl_pct, 42.5)
        await storage.close_position("a", 1.2, "roi", exit_proceeds=0.3)
        self.assertAlmostEqual(await storage.open_exposure(), 0.5)
        await storage.close()


class _FakeResponse:
    def __init__(self, status: int, content_type: str, payload=None) -> None:
        self.status, self.headers, self._payload = status, {"Content-Type": content_type}, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response) -> None:
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


class NftProbeTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, response) -> str | None:
        return await RobinhoodNftAdapter("https://example.test").probe(_FakeSession(response))

    async def test_a_404_is_reported_instead_of_polled_forever(self) -> None:
        # hood.fun answers exactly this for /api/nft/collections.
        self.assertIn("404", await self.probe(_FakeResponse(404, "text/html; charset=utf-8")))

    async def test_an_html_page_with_a_200_is_not_an_api(self) -> None:
        self.assertIn("JSON", await self.probe(_FakeResponse(200, "text/html")))

    async def test_json_without_a_collections_list_is_rejected(self) -> None:
        self.assertIn("collections", await self.probe(_FakeResponse(200, "application/json", {"nope": []})))

    async def test_a_working_endpoint_passes(self) -> None:
        self.assertIsNone(await self.probe(_FakeResponse(200, "application/json", {"collections": []})))


class ConfigCompatTests(unittest.TestCase):
    def test_pre_v11_env_names_still_set_the_limits(self) -> None:
        from bot.config import Config

        keys = ("MAX_ETH_PER_TRADE", "MAX_SOL_PER_TRADE", "DAILY_LOSS_LIMIT_ETH", "DAILY_LOSS_LIMIT_SOL")
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in keys}, clear=True):
            os.environ["MAX_SOL_PER_TRADE"] = "0.7"
            os.environ["DAILY_LOSS_LIMIT_SOL"] = "3.5"
            cfg = Config()
            self.assertEqual((cfg.max_eth_per_trade, cfg.daily_loss_limit_eth), (0.7, 3.5))
            os.environ["MAX_ETH_PER_TRADE"] = "0.2"
            self.assertEqual(Config().max_eth_per_trade, 0.2)  # the new name wins


if __name__ == "__main__":
    unittest.main()
