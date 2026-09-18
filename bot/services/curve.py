"""Constant-product bonding-curve maths for the dry-run executor.

Neither of the projects this design borrows from models price impact (both fill
at the reference price), so this is the piece we have to get right ourselves.
hood.fun publishes the exact virtual reserves for every token, which means the
fill price a real trade would have received can be computed, not guessed.

Assumptions, deliberately explicit (see docs/simulation-model.md):

* the curve is x * y = k over the *virtual* reserves;
* the trade fee (`fee_bps`) is taken from the ETH leg: off the input on a buy,
  off the proceeds on a sell;
* a seller can never be paid more than the ETH the curve actually holds;
* a simulated buy never touches the real chain, so when we price an exit the
  position is layered onto the *currently observed* reserves (`with_position`):
  our deposit is added and our tokens are removed, exactly as if the buy had
  happened. Without that, selling into reserves that never saw our buy would
  charge us the price impact twice and every position would look underwater
  the moment it opened.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.models import CurveState


@dataclass(frozen=True)
class BuyQuote:
    tokens_out: float
    effective_price: float  # ETH paid per token, all costs included
    spot_price: float
    fee_eth: float
    impact_pct: float  # how much worse than spot, before the fee


@dataclass(frozen=True)
class SellQuote:
    eth_out: float  # net proceeds after fee and the liquidity cap
    effective_price: float
    spot_price: float
    fee_eth: float
    impact_pct: float
    liquidity_capped: bool


def spot_price(curve: CurveState) -> float:
    return curve.virtual_eth / curve.virtual_tokens


def quote_buy(curve: CurveState, eth_in: float) -> BuyQuote:
    if eth_in <= 0:
        raise ValueError("eth_in must be positive")
    fee = eth_in * curve.fee_bps / 10_000
    eth_net = eth_in - fee
    k = curve.virtual_eth * curve.virtual_tokens
    tokens_out = curve.virtual_tokens - k / (curve.virtual_eth + eth_net)
    spot = spot_price(curve)
    # Price paid per token including the fee; impact is the move along the curve.
    effective = eth_in / tokens_out
    impact = ((eth_net / tokens_out) / spot - 1) * 100
    return BuyQuote(tokens_out, effective, spot, fee, impact)


def with_position(curve: CurveState, eth_spent: float, tokens_held: float) -> CurveState | None:
    """The observed reserves as they would look with our simulated buy included.

    Returns None when the position is too large for the curve to have supplied,
    in which case the caller falls back to the flat cost model.
    """
    if tokens_held >= curve.virtual_tokens:
        return None
    eth_net = eth_spent * (1 - curve.fee_bps / 10_000)
    return CurveState(
        virtual_eth=curve.virtual_eth + eth_net,
        virtual_tokens=curve.virtual_tokens - tokens_held,
        real_eth=curve.real_eth + eth_net,
        fee_bps=curve.fee_bps,
    )


def quote_sell(curve: CurveState, tokens_in: float) -> SellQuote:
    if tokens_in <= 0:
        raise ValueError("tokens_in must be positive")
    k = curve.virtual_eth * curve.virtual_tokens
    gross = curve.virtual_eth - k / (curve.virtual_tokens + tokens_in)
    capped = gross > curve.real_eth
    gross = min(gross, curve.real_eth)
    fee = gross * curve.fee_bps / 10_000
    eth_out = gross - fee
    spot = spot_price(curve)
    effective = eth_out / tokens_in
    impact = (1 - (gross / tokens_in) / spot) * 100
    return SellQuote(eth_out, effective, spot, fee, impact, capped)


def flat_buy(price: float, eth_in: float, cost_bps: int) -> tuple[float, float]:
    """Fallback when no curve is available (e.g. a graduated token on a DEX pool).

    Charges a flat cost only, so it understates impact for large sizes; the
    simulation docs say so. Returns (tokens_out, effective_price).
    """
    tokens = eth_in * (1 - cost_bps / 10_000) / price
    return tokens, eth_in / tokens


def flat_sell(price: float, tokens_in: float, cost_bps: int) -> float:
    """Net ETH proceeds for selling `tokens_in` at `price` under a flat cost."""
    return tokens_in * price * (1 - cost_bps / 10_000)
