from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot.services.curve import flat_buy, flat_sell, quote_buy, quote_sell, spot_price, with_position
from bot.services.models import CurveState

# The curve of a fresh hood.fun token, taken from the live board.
FRESH = CurveState(virtual_eth=2.81, virtual_tokens=1_145_000_000.0, real_eth=0.0, fee_bps=100)


class BuyQuoteTests(unittest.TestCase):
    def test_price_impact_grows_with_size(self) -> None:
        small, mid, large = (quote_buy(FRESH, size) for size in (0.01, 0.1, 0.5))
        self.assertLess(small.impact_pct, mid.impact_pct)
        self.assertLess(mid.impact_pct, large.impact_pct)
        # The numbers the design was based on: 0.5 ETH into a fresh curve is
        # roughly 17-19% worse than spot.
        self.assertAlmostEqual(large.impact_pct, 17.6, delta=0.5)
        self.assertAlmostEqual(large.effective_price / large.spot_price, 1.188, delta=0.01)

    def test_fee_is_charged_on_the_eth_leg(self) -> None:
        quote = quote_buy(FRESH, 0.3)
        self.assertAlmostEqual(quote.fee_eth, 0.003)
        self.assertGreater(quote.tokens_out, 0)

    def test_rejects_non_positive_size(self) -> None:
        with self.assertRaises(ValueError):
            quote_buy(FRESH, 0)


class SellQuoteTests(unittest.TestCase):
    def test_round_trip_on_unchanged_reserves_only_costs_fees(self) -> None:
        buy = quote_buy(FRESH, 0.3)
        layered = with_position(FRESH, 0.3, buy.tokens_out)
        sale = quote_sell(layered, buy.tokens_out)
        loss_pct = (sale.eth_out / 0.3 - 1) * 100
        # Two 1% fees, nothing else: the position must not look underwater
        # merely because it was opened.
        self.assertAlmostEqual(loss_pct, -2.0, delta=0.1)

    def test_price_move_flows_through_to_proceeds(self) -> None:
        buy = quote_buy(FRESH, 0.3)
        k = FRESH.virtual_eth * FRESH.virtual_tokens
        after = FRESH.virtual_eth + 0.495  # others buy 0.5 ETH (net of fee) after us
        moved = CurveState(after, k / after, real_eth=0.495, fee_bps=100)
        sale = quote_sell(with_position(moved, 0.3, buy.tokens_out), buy.tokens_out)
        self.assertGreater(sale.eth_out, 0.3)

    def test_payout_is_capped_by_ethereum_actually_in_the_curve(self) -> None:
        curve = CurveState(10.0, 1_000_000.0, real_eth=0.05, fee_bps=100)
        sale = quote_sell(curve, 500_000.0)
        self.assertTrue(sale.liquidity_capped)
        self.assertLessEqual(sale.eth_out, 0.05)

    def test_position_larger_than_the_curve_cannot_be_layered(self) -> None:
        self.assertIsNone(with_position(FRESH, 1.0, FRESH.virtual_tokens))


class FlatFallbackTests(unittest.TestCase):
    def test_flat_costs(self) -> None:
        tokens, effective = flat_buy(0.001, 1.0, 100)
        self.assertAlmostEqual(tokens, 990.0)
        self.assertAlmostEqual(effective, 1.0 / 990.0)
        self.assertAlmostEqual(flat_sell(0.001, 1000.0, 100), 0.99)

    def test_spot_price(self) -> None:
        self.assertAlmostEqual(spot_price(FRESH), 2.81 / 1_145_000_000.0)


if __name__ == "__main__":
    unittest.main()
