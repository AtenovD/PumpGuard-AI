from __future__ import annotations

import time
from dataclasses import dataclass

from bot.config import config
from bot.services.curve import flat_buy, flat_sell, quote_buy, quote_sell, with_position
from bot.services.models import CurveState, Token
from bot.services.storage import Position


@dataclass
class TradeResult:
    ok: bool
    # Effective price per token with every cost included, so it is directly
    # comparable to what a real fill would have looked like.
    price: float = 0.0
    tx_hash: str = ""
    error: str = ""
    tokens: float = 0.0
    proceeds: float = 0.0  # net ETH returned by a sale
    fee_eth: float = 0.0
    impact_pct: float = 0.0
    model: str = "flat"  # "curve" when priced off the bonding curve
    liquidity_capped: bool = False


class DryRunExecutor:
    """Simulates a buy/sell - no wallet, no signing, no network call to any chain.

    This is intentionally the only executor in this project. A live executor
    that signs and broadcasts real Robinhood Chain transactions is a materially
    different, much higher-stakes piece of software and is out of scope here
    on purpose - this bot is a research/screening tool, not a trading bot.

    Since v1.1 fills are priced off the token's bonding curve, so they include
    the trade fee and the price impact of the trade's size. Where no curve is
    available (a graduated token) a flat cost (`FALLBACK_COST_BPS`) is charged
    instead. See docs/simulation-model.md for every assumption.
    """

    async def buy(self, token: Token, size_eth: float) -> TradeResult:
        tx = f"dryrun-buy-{token.mint[:8]}-{int(time.time())}"
        if token.curve is not None and token.curve.virtual_tokens > 0:
            quote = quote_buy(token.curve, size_eth)
            return TradeResult(
                ok=True, price=quote.effective_price, tx_hash=tx, tokens=quote.tokens_out,
                fee_eth=quote.fee_eth, impact_pct=quote.impact_pct, model="curve",
            )
        price = token.reference_price
        if price is None:
            price = max(token.native_in_curve, 0.0001) / max(token.unique_buyers or 1, 1)
        tokens, effective = flat_buy(price, size_eth, config.fallback_cost_bps)
        return TradeResult(
            ok=True, price=effective, tx_hash=tx, tokens=tokens,
            fee_eth=size_eth * config.fallback_cost_bps / 10_000,
        )

    def quote_exit(
        self, position: Position, spot_price: float, curve: CurveState | None
    ) -> TradeResult:
        """What selling this position right now would return, net of every cost."""
        tokens = position.token_amount
        if not tokens or tokens <= 0:
            # Positions opened before v1.1 have no recorded amount.
            tokens = position.sol_spent / position.entry_price if position.entry_price > 0 else 0.0
        if tokens <= 0:
            return TradeResult(ok=False, error="position has no token amount")
        layered = with_position(curve, position.sol_spent, tokens) if curve is not None else None
        if layered is not None:
            quote = quote_sell(layered, tokens)
            return TradeResult(
                ok=True, price=quote.effective_price, tokens=tokens, proceeds=quote.eth_out,
                fee_eth=quote.fee_eth, impact_pct=quote.impact_pct, model="curve",
                liquidity_capped=quote.liquidity_capped,
            )
        proceeds = flat_sell(spot_price, tokens, config.fallback_cost_bps)
        return TradeResult(
            ok=True, price=proceeds / tokens, tokens=tokens, proceeds=proceeds,
            fee_eth=tokens * spot_price * config.fallback_cost_bps / 10_000,
        )

    async def sell(
        self, position: Position, spot_price: float, curve: CurveState | None = None
    ) -> TradeResult:
        result = self.quote_exit(position, spot_price, curve)
        if result.ok:
            result.tx_hash = f"dryrun-sell-{position.mint[:8]}-{int(time.time())}"
        return result
