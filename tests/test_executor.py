from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.executor import DryRunExecutor
from bot.services.models import CurveState, Token
from bot.services.storage import Position

CURVE = CurveState(2.81, 1_145_000_000.0, 0.0, 100)


def token(curve: CurveState | None = CURVE, price: float | None = 2.81 / 1_145_000_000.0) -> Token:
    return Token("0xmint", "T", "Token", "creator", 0.0, 5, 0.0, "robinhood", price, curve=curve)


class DryRunExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_buy_pays_price_impact_and_fee(self) -> None:
        result = await DryRunExecutor().buy(token(), 0.3)
        self.assertEqual(result.model, "curve")
        self.assertGreater(result.impact_pct, 10)
        self.assertGreater(result.price, CURVE.virtual_eth / CURVE.virtual_tokens)
        self.assertAlmostEqual(result.fee_eth, 0.003)

    async def test_immediate_exit_costs_about_two_fees_not_the_impact_twice(self) -> None:
        executor = DryRunExecutor()
        buy = await executor.buy(token(), 0.3)
        position = Position("0xmint", "T", buy.price, 0.3, 0.7, "c", 0, "open", token_amount=buy.tokens)
        exit_ = executor.quote_exit(position, CURVE.virtual_eth / CURVE.virtual_tokens, CURVE)
        self.assertTrue(exit_.ok)
        self.assertAlmostEqual((exit_.proceeds / 0.3 - 1) * 100, -2.0, delta=0.15)

    async def test_pre_v11_position_without_token_amount_still_prices(self) -> None:
        position = Position("m", "T", 2e-9, 0.3, 0.7, "c", 0, "open")  # token_amount is None
        exit_ = DryRunExecutor().quote_exit(position, 2e-9, CURVE)
        self.assertTrue(exit_.ok)
        self.assertGreater(exit_.proceeds, 0)

    async def test_graduated_token_falls_back_to_a_flat_cost(self) -> None:
        with patch("bot.services.executor.config") as cfg:
            cfg.fallback_cost_bps = 100
            buy = await DryRunExecutor().buy(token(curve=None, price=0.001), 1.0)
        self.assertEqual(buy.model, "flat")
        self.assertAlmostEqual(buy.tokens, 990.0)

    async def test_sell_returns_a_tx_and_net_proceeds(self) -> None:
        executor = DryRunExecutor()
        buy = await executor.buy(token(), 0.2)
        position = Position("0xmint", "T", buy.price, 0.2, 0.7, "c", 0, "open", token_amount=buy.tokens)
        sale = await executor.sell(position, CURVE.virtual_eth / CURVE.virtual_tokens, CURVE)
        self.assertTrue(sale.tx_hash.startswith("dryrun-sell-"))
        self.assertGreater(sale.proceeds, 0)


if __name__ == "__main__":
    unittest.main()
