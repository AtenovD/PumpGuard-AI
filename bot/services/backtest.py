from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass, field

from bot.services.storage import Position, Storage

# Below this many closed trades a win rate or mean PnL says almost nothing.
MIN_SAMPLE = 30
BOOTSTRAP_RESAMPLES = 2000


@dataclass
class BacktestReport:
    total_signals: int
    total_bought: int
    total_skipped: int
    skip_by_stage: dict[str, int]
    win_rate: float
    avg_pnl_pct: float
    median_pnl_pct: float
    best_position: dict | None
    worst_position: dict | None
    stop_loss_hit_rate: float
    period_start: int
    period_end: int
    # v1.1 - every field below has a default so older callers keep working.
    total_abstained: int = 0
    abstain_by_reason: dict[str, int] = field(default_factory=dict)
    exit_by_reason: dict[str, int] = field(default_factory=dict)
    net_pnl_eth: float = 0.0
    sample_size: int = 0
    pnl_ci95: tuple[float, float] | None = None
    sample_verdict: str = "no closed trades yet"
    open_positions: int = 0
    open_spot_avg_pct: float | None = None


def position_pnl_pct(position: Position) -> float | None:
    """Realised PnL % for a closed position, or None if it cannot be measured.

    Positions closed by v1.1 or later carry the net ETH a sale returned, which
    already includes the fee and price impact, so that is used whenever it
    exists. Older rows only have an exit price, so they fall back to the price
    ratio - which ignores costs and is why the two are never blended silently.
    """
    if position.exit_proceeds is not None and position.sol_spent > 0:
        return (position.exit_proceeds - position.sol_spent) / position.sol_spent * 100
    if position.exit_price is None or position.entry_price <= 0:
        return None
    return (position.exit_price - position.entry_price) / position.entry_price * 100


def bootstrap_mean_ci(
    values: list[float], resamples: int = BOOTSTRAP_RESAMPLES, confidence: float = 0.95, seed: int = 7
) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean. Seeded, so a report is reproducible."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(resamples))
    low = means[int((1 - confidence) / 2 * resamples)]
    high = means[min(int((1 + confidence) / 2 * resamples) - 1, resamples - 1)]
    return low, high


def sample_verdict(n: int, ci: tuple[float, float] | None) -> str:
    if n == 0:
        return "no closed trades yet"
    if n < MIN_SAMPLE:
        return f"too few trades to conclude anything (n={n}, need {MIN_SAMPLE}+)"
    if ci is not None and ci[0] > 0:
        return f"mean PnL is positive with 95% confidence (n={n})"
    if ci is not None and ci[1] < 0:
        return f"mean PnL is negative with 95% confidence (n={n})"
    return f"cannot tell edge from noise yet (n={n})"


async def run_backtest(storage: Storage, since_days: int | None = None) -> BacktestReport:
    """Aggregate recorded screening outcomes and closed dry-run positions.

    This is a performance report over what the bot recorded going forward, not
    a historical replay. It reports abstentions separately from rejections and
    says plainly when the sample is too small to mean anything.
    """
    period_end = int(time.time())
    cutoff = None if since_days is None else period_end - max(since_days, 0) * 86400
    signals = await storage.signals_since(cutoff)
    positions = await storage.closed_positions_since(cutoff)

    skipped = [signal for signal in signals if signal["outcome"] == "skip"]
    abstained = [signal for signal in signals if signal["outcome"] == "abstain"]
    bought = [signal for signal in signals if signal["outcome"] == "bought"]
    skip_by_stage: dict[str, int] = {}
    for signal in skipped:
        stage = str(signal["stage"])
        skip_by_stage[stage] = skip_by_stage.get(stage, 0) + 1
    abstain_by_reason: dict[str, int] = {}
    for signal in abstained:
        reason = str(signal["detail"] or "unknown")
        abstain_by_reason[reason] = abstain_by_reason.get(reason, 0) + 1

    measured: list[dict[str, object]] = []
    for position in positions:
        pnl_pct = position_pnl_pct(position)
        if pnl_pct is None:
            continue
        measured.append({"mint": position.mint, "symbol": position.symbol, "pnl_pct": pnl_pct})

    pnl_values = [float(position["pnl_pct"]) for position in measured]
    best = max(measured, key=lambda item: float(item["pnl_pct"]), default=None)
    worst = min(measured, key=lambda item: float(item["pnl_pct"]), default=None)
    stop_loss_hits = sum(position.close_reason == "stop_loss" for position in positions)
    exit_by_reason: dict[str, int] = {}
    for position in positions:
        reason = position.close_reason or "unknown"
        exit_by_reason[reason] = exit_by_reason.get(reason, 0) + 1
    net_pnl_eth = sum(
        position.exit_proceeds - position.sol_spent
        for position in positions
        if position.exit_proceeds is not None
    )
    ci = bootstrap_mean_ci(pnl_values)

    # Winners that are still open never appear in the closed-trade numbers, so
    # show them here rather than let the report look worse than the book is.
    marked = await storage.open_positions_marked()
    open_spot = [
        (price - position.entry_price) / position.entry_price * 100
        for position, price in marked
        if price is not None and position.entry_price > 0
    ]

    timestamps = [int(signal["created_at"]) for signal in signals]
    timestamps.extend(
        position.closed_at or position.opened_at for position in positions
    )
    period_start = min(timestamps) if timestamps else (cutoff if cutoff is not None else period_end)

    return BacktestReport(
        total_signals=len(signals),
        total_bought=len(bought),
        total_skipped=len(skipped),
        skip_by_stage=skip_by_stage,
        win_rate=(sum(value > 0 for value in pnl_values) / len(pnl_values)) if pnl_values else 0.0,
        avg_pnl_pct=statistics.fmean(pnl_values) if pnl_values else 0.0,
        median_pnl_pct=statistics.median(pnl_values) if pnl_values else 0.0,
        best_position=best,
        worst_position=worst,
        stop_loss_hit_rate=(stop_loss_hits / len(positions)) if positions else 0.0,
        period_start=period_start,
        period_end=period_end,
        total_abstained=len(abstained),
        abstain_by_reason=abstain_by_reason,
        exit_by_reason=exit_by_reason,
        net_pnl_eth=round(net_pnl_eth, 6),
        sample_size=len(pnl_values),
        pnl_ci95=ci,
        sample_verdict=sample_verdict(len(pnl_values), ci),
        open_positions=len(marked),
        open_spot_avg_pct=statistics.fmean(open_spot) if open_spot else None,
    )
