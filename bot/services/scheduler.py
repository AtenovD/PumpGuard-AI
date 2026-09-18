from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp
from aiogram import Bot

from bot.config import config
from bot.services import pipeline
from bot.services.executor import DryRunExecutor
from bot.services.exits import ExitRules, evaluate_exit, parse_roi_table
from bot.services.chains import ChainAdapter
from bot.services.reputation import ReputationBook
from bot.services.risk import RiskManager
from bot.services.storage import Storage

logger = logging.getLogger(__name__)

# How many recent launches/outcomes the timing agent's market snapshot is built from.
_WINDOW_SECONDS = 900.0
_OUTCOME_MEMORY = 50


class MarketPulse:
    """Rolling window of what this process has actually observed — no external
    price feeds, so the timing agent only ever judges its own vantage point."""

    def __init__(self) -> None:
        self._launches: deque[float] = deque()
        self._outcomes: deque[float] = deque(maxlen=_OUTCOME_MEMORY)

    def record_launch(self) -> None:
        now = time.time()
        self._launches.append(now)
        cutoff = now - _WINDOW_SECONDS
        while self._launches and self._launches[0] < cutoff:
            self._launches.popleft()

    def record_outcome(self, pnl_pct: float) -> None:
        self._outcomes.append(pnl_pct)

    def snapshot(self) -> dict:
        minutes = _WINDOW_SECONDS / 60.0
        data = {
            "launches_per_minute": round(len(self._launches) / minutes, 2) if minutes else 0.0,
            "window_minutes": minutes,
        }
        if self._outcomes:
            wins = [p for p in self._outcomes if p > 0]
            data["win_rate"] = round(len(wins) / len(self._outcomes), 3)
            data["sample_size"] = len(self._outcomes)
        return data


async def _run_chain_monitor(
    bot: Bot, storage: Storage, adapter: ChainAdapter, pulse: MarketPulse
) -> None:
    risk = RiskManager(storage)
    reputation = ReputationBook(storage)
    executor = DryRunExecutor()

    async with aiohttp.ClientSession() as session:
        async for token in adapter.stream_new_tokens():
            pulse.record_launch()
            if await storage.is_seen(token.mint, token.chain):
                continue
            await storage.mark_seen(token.mint, token.symbol, token.name, token.chain, token.creator)

            try:
                analysis = await pipeline.screen_token(
                    session, storage, risk, reputation, executor, token, pulse.snapshot(),
                    feed_healthy=adapter.price_feed_healthy,
                )
            except Exception:
                logger.exception("screening %s crashed", token.mint)
                continue

            if analysis is not None:
                adapter.watch(token.mint)
                await pipeline.broadcast_signal(bot, session, storage, analysis)


async def run_monitor_loop(
    bot: Bot, storage: Storage, adapters: list[ChainAdapter] | ChainAdapter
) -> None:
    """Run one shared screening pipeline consumer per enabled chain adapter."""
    adapter_list = adapters if isinstance(adapters, list) else [adapters]
    pulse = MarketPulse()
    await asyncio.gather(
        *(_run_chain_monitor(bot, storage, adapter, pulse) for adapter in adapter_list)
    )


async def run_position_watcher(
    storage: Storage,
    reputation: ReputationBook,
    adapters: dict[str, ChainAdapter] | ChainAdapter,
    check_interval_sec: int = 15,
) -> None:
    """Watches real prices for open positions and closes them by the exit scheme.

    Every exit is decided on what a sale would actually return - net of the
    trade fee and the price impact of the position's size - not on the spot
    price, so the stop-loss, take-profit and trailing stop all see the same
    number a live executor would have realised.
    """
    executor = DryRunExecutor()
    rules = ExitRules(
        stop_loss_pct=config.stop_loss_pct,
        roi_table=parse_roi_table(config.roi_table),
        trailing_activate_pct=config.trailing_activate_pct,
        trailing_stop_pct=config.trailing_stop_pct,
        max_hold_minutes=config.max_hold_minutes,
    )
    adapter_map = (
        adapters if isinstance(adapters, dict) else {adapters.chain_id: adapters}
    )

    # Positions may already be open from a previous run (state persists in sqlite) -
    # make sure the price feed is watching all of them, not just newly opened ones.
    for position in await storage.open_positions():
        adapter = adapter_map.get(position.chain)
        if adapter is not None:
            adapter.watch(position.mint)

    while True:
        await asyncio.sleep(check_interval_sec)
        for position in await storage.open_positions():
            adapter = adapter_map.get(position.chain)
            if adapter is None:
                continue
            price = adapter.get_price(position.mint)
            if price is None:
                continue  # no trade observed yet for this mint - nothing to act on
            await storage.record_price_snapshot(position.mint, price, position.chain)

            curve = adapter.get_curve(position.mint)
            quote = executor.quote_exit(position, price, curve)
            if not quote.ok or position.sol_spent <= 0:
                continue
            profit_pct = (quote.proceeds - position.sol_spent) / position.sol_spent * 100
            peak = max(position.peak_pnl_pct, profit_pct)
            if peak > position.peak_pnl_pct:
                await storage.update_position_peak(position.mint, peak, position.chain)

            held_minutes = (time.time() - position.opened_at) / 60
            reason = evaluate_exit(rules, profit_pct, peak, held_minutes)
            if reason is None:
                continue  # still within tolerance, keep watching

            result = await executor.sell(position, price, curve)
            pnl_eth = result.proceeds - position.sol_spent
            pnl_pct = pnl_eth / position.sol_spent * 100

            await storage.close_position(
                position.mint, result.price, reason, chain=position.chain,
                exit_proceeds=result.proceeds,
            )
            await storage.record_pnl_only(pnl_eth)
            await reputation.record_outcome(position.creator, pnl_pct, position.chain)
            adapter.unwatch(position.mint)
            logger.info(
                "%s closed %s:%s: cost=%.4f proceeds=%.4f pnl=%.4f ETH (%.1f%%, peak %.1f%%, held %.0f min)",
                reason, position.chain, position.mint[:8], position.sol_spent, result.proceeds,
                pnl_eth, pnl_pct, peak, held_minutes,
            )
