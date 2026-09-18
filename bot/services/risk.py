from __future__ import annotations

from bot.config import config
from bot.services.models import ClampEvent, RiskDecision
from bot.services.storage import Storage

# Fraction of the remaining daily loss budget a single trade may risk.
MAX_SHARE_OF_REMAINING_BUDGET = 0.30
MIN_TRADE_ETH = 0.01


class RiskManager:
    """Hard limits checked before any dry-run trade is opened.

    This never calls Grok: it is plain arithmetic and the last gate, so a good
    score can still be blocked by the day's numbers. Conviction (the agents'
    score) may only ask for a size; every limit below can cut it, and a cut is
    never redistributed anywhere else. Each cut is recorded as a `ClampEvent`
    so the decision record can show which limit fired and by how much.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def evaluate(self, score: float) -> RiskDecision:
        trades_today, pnl_today = await self.storage.get_daily_counters()
        open_positions = await self.storage.open_position_count()
        exposure = await self.storage.open_exposure()

        if -pnl_today >= config.daily_loss_limit_eth:
            return RiskDecision(approved=False, reason="daily_loss_limit_hit")

        if trades_today >= config.max_trades_per_day:
            return RiskDecision(approved=False, reason="max_trades_per_day_hit")

        if open_positions >= config.max_open_positions:
            return RiskDecision(approved=False, reason="max_open_positions_hit")

        clamps: list[ClampEvent] = []
        size = config.max_eth_per_trade * max(score, 0.0)

        if size > config.max_eth_per_trade:
            clamps.append(ClampEvent("max_eth_per_trade", size, config.max_eth_per_trade))
            size = config.max_eth_per_trade

        remaining_budget = config.daily_loss_limit_eth - max(0.0, -pnl_today)
        budget_cap = remaining_budget * MAX_SHARE_OF_REMAINING_BUDGET
        if budget_cap < size:
            clamps.append(ClampEvent("daily_budget_share", size, budget_cap))
            size = budget_cap

        exposure_room = max(config.max_total_exposure_eth - exposure, 0.0)
        if exposure_room < size:
            clamps.append(ClampEvent("max_total_exposure", size, exposure_room))
            size = exposure_room

        if size < MIN_TRADE_ETH:
            return RiskDecision(approved=False, reason="position_size_too_small", clamps=clamps)

        return RiskDecision(approved=True, reason="ok", size_sol=round(size, 4), clamps=clamps)
