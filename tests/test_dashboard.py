from __future__ import annotations

import os
import tempfile
import time
import unittest

import aiosqlite
from starlette.requests import Request

from bot.services.storage import Position, Storage
from dashboard.main import create_app
from dashboard.queries import (
    read_backtest, read_funnel, read_open_positions, read_recent_decisions, read_stats,
)


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_database_renders_every_endpoint(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = handle.name
        handle.close()
        try:
            app = create_app(path)
            async with app.router.lifespan_context(app):
                for route_path in ("/", "/positions", "/api/stats", "/metrics"):
                    route = next(route for route in app.routes if route.path == route_path)
                    request = Request({
                        "type": "http", "app": app, "method": "GET", "path": route_path,
                        "headers": [], "query_string": b"",
                    })
                    response = (
                        await route.endpoint(request, "24h")
                        if route_path == "/" else await route.endpoint(request)
                    )
                    if hasattr(response, "body"):
                        self.assertTrue(response.body)
                    if route_path == "/metrics":
                        self.assertIn(b"pumpguard_open_positions 0.0", response.body)
                self.assertEqual((await read_stats(app.state.storage))["screened_24h"], 0)
                self.assertEqual(await read_funnel(app.state.storage, 3600), [])
                self.assertEqual((await read_backtest(app.state.storage, 3600))["total_signals"], 0)
                self.assertEqual(await read_open_positions(app.state.storage), [])
                with self.assertRaises(aiosqlite.OperationalError):
                    await app.state.storage.db.execute("CREATE TABLE forbidden (id INTEGER)")
        finally:
            os.unlink(path)

    async def test_positions_use_bot_written_price_snapshot(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = handle.name
        handle.close()
        try:
            writer = Storage(path)
            await writer.connect()
            await writer.open_position(
                Position("mint", "TKN", 1.0, 0.1, 0.8, "creator", int(time.time()), "open")
            )
            await writer.record_price_snapshot("mint", 1.25)
            await writer.log_signal("mint", "TKN", 0.8, "executor", "bought")
            await writer.set_runtime_health("circuit_breaker", "open", 1.0)
            await writer.set_runtime_health("price_feed:robinhood", "healthy", 1.0)
            await writer.close()

            reader = Storage(path)
            await reader.connect_readonly()
            positions = await read_open_positions(reader)
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["chain"], "robinhood")
            self.assertEqual(positions[0]["current_price"], 1.25)
            self.assertEqual((await read_stats(reader))["bought_24h"], 1)
            from dashboard.metrics import render_metrics
            metrics = await render_metrics(reader)
            self.assertIn(b'pumpguard_signals_screened_total{stage="executor"} 1.0', metrics)
            self.assertIn(b'pumpguard_signals_bought_total{stage="executor"} 1.0', metrics)
            self.assertIn(b'pumpguard_circuit_breaker_state{state="open"} 1.0', metrics)
            self.assertIn(b'pumpguard_price_feed_healthy{chain="robinhood"} 1.0', metrics)
            await reader.close()
        finally:
            os.unlink(path)

    async def test_decision_records_are_served_read_only_and_survive_old_databases(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = handle.name
        handle.close()
        try:
            writer = Storage(path)
            await writer.connect()
            await writer.save_decision(
                "robinhood", "0xmint", "TKN", "abstain", "abstain", None,
                '{"outcome": "abstain", "detail": "auditor:insufficient_trade_data"}',
            )
            await writer.close()

            app = create_app(path)
            async with app.router.lifespan_context(app):
                route = next(route for route in app.routes if route.path == "/api/decisions")
                rows = await route.endpoint(
                    Request({"type": "http", "app": app, "method": "GET", "path": "/api/decisions",
                             "headers": [], "query_string": b""}),
                    20, None,
                )
                self.assertEqual(rows[0]["outcome"], "abstain")
                self.assertEqual(rows[0]["payload"]["detail"], "auditor:insufficient_trade_data")
                self.assertEqual(len(await read_recent_decisions(app.state.storage, 5, "0xother")), 0)
        finally:
            os.unlink(path)

        # A database written by v1.0 has no decision table: the route must return [], not 500.
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        old_path = handle.name
        handle.close()
        try:
            reader = Storage(old_path)
            await reader.connect_readonly()
            self.assertEqual(await read_recent_decisions(reader), [])
            await reader.close()
        finally:
            os.unlink(old_path)


if __name__ == "__main__":
    unittest.main()
