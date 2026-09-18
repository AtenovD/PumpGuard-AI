from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CurveState:
    """Constant-product bonding-curve reserves as published by the launchpad.

    `virtual_*` are the pricing reserves, `real_eth` is the ETH actually held
    by the curve, i.e. the most a seller can ever be paid out.
    """

    virtual_eth: float
    virtual_tokens: float
    real_eth: float = 0.0
    fee_bps: int = 100


@dataclass
class Token:
    mint: str
    symbol: str | None
    name: str | None
    creator: str | None
    native_in_curve: float
    unique_buyers: int | None
    created_at: float
    chain: str = "robinhood"
    reference_price: float | None = None
    curve: CurveState | None = None
    # Observed trade statistics from the launchpad indexer; None when the
    # indexer published nothing for this token (the auditor then abstains).
    trades: dict | None = None


@dataclass
class AgentVerdict:
    name: str
    score: float  # 0.0 (reject) .. 1.0 (strong pass)
    summary: str
    flags: list[str] = field(default_factory=list)
    approve: bool = True
    fallback: bool = False  # True if this is a pessimistic fallback, not a real model answer
    # An abstention is "no opinion", which is different from a rejection: the
    # model was unreachable, its answer was unparseable, or there was not
    # enough data to judge. It never buys, but it is counted separately so an
    # outage cannot masquerade as a screening decision. `fallback` is kept in
    # sync with it so webhook v1 consumers see the same flag as before.
    abstained: bool = False
    abstain_reason: str | None = None


@dataclass
class ClampEvent:
    """One hard limit firing: which one, and what it cut the size from and to."""

    limit: str
    before: float
    after: float


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    # Position size in the chain's native currency (ETH on Robinhood Chain).
    # The field keeps its historical name because it is part of the v1 webhook
    # payload contract.
    size_sol: float = 0.0
    clamps: list[ClampEvent] = field(default_factory=list)


@dataclass
class TokenAnalysis:
    token: Token
    researcher: AgentVerdict | None = None
    auditor: AgentVerdict | None = None
    narrative: AgentVerdict | None = None
    timing: AgentVerdict | None = None
    checker: AgentVerdict | None = None
    total_score: float = 0.0
    risk: RiskDecision | None = None
    # Simulated entry: effective price, tokens, fee, price impact and which
    # cost model priced it. None until a dry-run buy happens.
    fill: dict | None = None


@dataclass
class NftCollection:
    """A newly observed NFT collection on the Robinhood Chain marketplace.

    Deliberately not a `Token`: NFT collections have no bonding-curve entry
    price to dry-run buy/sell against, so this is screened and alerted on,
    never fed to `DryRunExecutor`.
    """

    address: str
    name: str | None
    symbol: str | None
    creator: str | None
    supply: int | None
    unique_minters: int | None
    created_at: float
    chain: str = "robinhood-nft"
    floor_price: float | None = None


@dataclass
class NftAnalysis:
    collection: NftCollection
    researcher: AgentVerdict | None = None
    auditor: AgentVerdict | None = None
    narrative: AgentVerdict | None = None
    timing: AgentVerdict | None = None
    total_score: float = 0.0
