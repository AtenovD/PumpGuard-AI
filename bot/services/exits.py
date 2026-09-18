"""Exit rules for dry-run positions.

Until v1.1 the only way a position could close was the stop-loss, so every
closed trade was a loser by construction and the backtest report could never
show a win. This module adds the other exits a simulated trade needs, as a pure
function so each rule is unit-testable without a database or a price feed.

Evaluation order follows the same principle as Freqtrade's documented
sequence: protect capital first, then take profit, then the softer exits.

1. stop-loss      - net loss reached STOP_LOSS_PCT
2. roi            - a time-decaying take-profit table, "minutes:min-profit-%"
3. trailing_stop  - once a position has run up, give back at most X points
4. time_stop      - close whatever is left after MAX_HOLD_MINUTES

All percentages are net of the fees and price impact the executor quotes, so an
exit fires on what a real sale would actually have returned.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitRules:
    stop_loss_pct: float
    roi_table: tuple[tuple[int, float], ...]
    trailing_activate_pct: float
    trailing_stop_pct: float
    max_hold_minutes: int


def parse_roi_table(raw: str) -> tuple[tuple[int, float], ...]:
    """Parse "0:100,30:40,120:15,360:0" into ((minutes, min_profit_pct), ...).

    Entries are sorted by minutes. Malformed entries are skipped instead of
    raising so a typo in an env var cannot stop the position watcher.
    """
    pairs: list[tuple[int, float]] = []
    for part in raw.split(","):
        minutes, _, profit = part.partition(":")
        try:
            pairs.append((int(minutes.strip()), float(profit.strip())))
        except ValueError:
            continue
    return tuple(sorted(pairs))


def roi_threshold(table: tuple[tuple[int, float], ...], held_minutes: float) -> float | None:
    """The profit needed to exit now: the entry with the largest minutes <= held."""
    threshold: float | None = None
    for minutes, profit in table:
        if held_minutes >= minutes:
            threshold = profit
    return threshold


def evaluate_exit(
    rules: ExitRules,
    profit_pct: float,
    peak_profit_pct: float,
    held_minutes: float,
) -> str | None:
    """Return the exit reason, or None to keep holding."""
    if profit_pct <= -rules.stop_loss_pct:
        return "stop_loss"

    roi = roi_threshold(rules.roi_table, held_minutes)
    if roi is not None and profit_pct >= roi:
        return "roi"

    if (
        rules.trailing_stop_pct > 0
        and peak_profit_pct >= rules.trailing_activate_pct
        and profit_pct <= peak_profit_pct - rules.trailing_stop_pct
    ):
        return "trailing_stop"

    if rules.max_hold_minutes > 0 and held_minutes >= rules.max_hold_minutes:
        return "time_stop"

    return None
