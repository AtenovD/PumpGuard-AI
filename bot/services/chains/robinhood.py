from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

import aiohttp

from bot.services.models import CurveState, Token

logger = logging.getLogger(__name__)


class RobinhoodAdapter:
    """Polls hood.fun's public board indexer and uses its exact curve/pool price."""

    chain_id = "robinhood"

    def __init__(
        self, data_url: str, rpc_url: str, poll_interval: float = 5.0,
        max_launch_age_seconds: float | None = None,
    ) -> None:
        self.data_url = data_url.rstrip("/")
        self.rpc_url = rpc_url
        self.poll_interval = poll_interval
        # None disables the guard (used by tests that feed synthetic timestamps).
        self.max_launch_age_seconds = max_launch_age_seconds
        self._watched: set[str] = set()
        self._latest_price: dict[str, float] = {}
        self._latest_curve: dict[str, CurveState] = {}
        self._baseline: set[str] | None = None
        self._emitted: set[str] = set()
        self.price_feed_healthy = False

    async def _fetch(self, session: aiohttp.ClientSession) -> dict:
        async with session.get(
            f"{self.data_url}/api/board", timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            return await response.json()

    @staticmethod
    def _price(token: dict) -> float | None:
        pair_price = token.get("pairPriceWei")
        if pair_price and int(pair_price) > 0:
            return int(pair_price) / 10**18
        curve = token.get("curve") or {}
        virtual_eth = int(curve.get("virtualEth") or 0)
        virtual_tokens = int(curve.get("virtualTokens") or 0)
        if virtual_eth > 0 and virtual_tokens > 0:
            return virtual_eth / virtual_tokens
        return None

    @staticmethod
    def _curve(token: dict) -> CurveState | None:
        """Bonding-curve reserves, or None once the token has graduated to a pool."""
        curve = token.get("curve") or {}
        if curve.get("graduated") or curve.get("migrated"):
            return None
        if token.get("pairPriceWei") and int(token["pairPriceWei"]) > 0:
            return None
        virtual_eth = int(curve.get("virtualEth") or 0)
        virtual_tokens = int(curve.get("virtualTokens") or 0)
        if virtual_eth <= 0 or virtual_tokens <= 0:
            return None
        return CurveState(
            virtual_eth=virtual_eth / 10**18,
            virtual_tokens=virtual_tokens / 10**18,
            real_eth=int(curve.get("realEth") or 0) / 10**18,
            fee_bps=int(curve.get("tradeFeeBps") or 100),
        )

    @staticmethod
    def _trade_stats(address: str, activity: dict | None) -> dict | None:
        """Per-token trade statistics from the board's `activity` block.

        `byToken` covers the token's whole life (its trade count includes the
        creator's opening purchase). `recent` is only the platform-wide last
        few dozen trades, so the window figures are a sample, not a census -
        the auditor prompt says so.
        """
        if not activity:
            return None
        key = address.lower()
        by_token = activity.get("byToken") or {}
        row = by_token.get(key) or by_token.get(address)
        if not row:
            return None
        try:
            def eth(value: object) -> float:
                return int(value or 0) / 10**18

            stats: dict = {
                "trade_count": int(row.get("tradeCount") or 0),
                "volume_eth": eth(row.get("volumeWei")),
                "volume_4h_eth": eth(row.get("vol4hWei")),
                "volume_24h_eth": eth(row.get("vol24hWei")),
                "last_trade_block": row.get("lastTradeBlock"),
                "last_buy_block": row.get("lastBuyBlock"),
            }
            recent = [t for t in (activity.get("recent") or []) if str(t.get("token", "")).lower() == key]
            if recent:
                volume: dict[str, float] = {}
                for trade in recent:
                    trader = str(trade.get("trader", "")).lower()
                    volume[trader] = volume.get(trader, 0.0) + eth(trade.get("ethAmount"))
                total = sum(volume.values())
                buys = [t for t in recent if t.get("isBuy")]
                stats["recent_sample"] = {
                    "trades": len(recent),
                    "unique_traders": len(volume),
                    "buys": len(buys),
                    "sells": len(recent) - len(buys),
                    "buy_eth": sum(eth(t.get("ethAmount")) for t in buys),
                    "sell_eth": sum(eth(t.get("ethAmount")) for t in recent if not t.get("isBuy")),
                    "top_trader_share": round(max(volume.values()) / total, 4) if total else 0.0,
                }
            return stats
        except (TypeError, ValueError):
            return None

    def _too_old(self, item: dict) -> bool:
        if self.max_launch_age_seconds is None:
            return False
        try:
            launched = float(item.get("timestamp") or 0)
        except (TypeError, ValueError):
            return True
        # A record with no timestamp cannot be proven fresh, so it is not a launch.
        return launched <= 0 or time.time() - launched > self.max_launch_age_seconds

    def _update_prices(self, tokens: list[dict]) -> None:
        for token in tokens:
            mint = token.get("address")
            price = self._price(token)
            key = str(mint).lower()
            if key in self._watched and price is not None:
                self._latest_price[key] = price
                curve = self._curve(token)
                if curve is not None:
                    self._latest_curve[key] = curve
                else:
                    self._latest_curve.pop(key, None)

    async def stream_new_tokens(self) -> AsyncIterator[Token]:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    payload = await self._fetch(session)
                    tokens = payload.get("tokens", [])
                    activity = payload.get("activity")
                    self._update_prices(tokens)
                    addresses = {
                        str(item["address"]).lower() for item in tokens if item.get("address")
                    }
                    if self._baseline is None:
                        self._baseline = addresses
                        await asyncio.sleep(self.poll_interval)
                        continue
                    for item in reversed(tokens):
                        mint = item.get("address")
                        price = self._price(item)
                        key = str(mint).lower()
                        if (
                            not mint or key in self._baseline or key in self._emitted
                            or price is None
                        ):
                            continue
                        if self._too_old(item):
                            # Not a new launch, whatever the indexer says. Remember it so
                            # it is not re-examined on every poll.
                            self._baseline.add(key)
                            continue
                        self._emitted.add(key)
                        yield Token(
                            mint=mint, symbol=item.get("symbol"), name=item.get("name"),
                            creator=item.get("creator"), native_in_curve=float((item.get("curve") or {}).get("realEth") or 0) / 10**18,
                            unique_buyers=None,
                            created_at=float(item.get("timestamp") or 0), chain=self.chain_id,
                            reference_price=price,
                            curve=self._curve(item),
                            trades=self._trade_stats(mint, activity),
                        )
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                    logger.warning("hood.fun data source failed: %s", exc)
                await asyncio.sleep(self.poll_interval)

    def watch(self, mint: str) -> None:
        self._watched.add(mint.lower())

    def unwatch(self, mint: str) -> None:
        self._watched.discard(mint.lower())
        self._latest_price.pop(mint.lower(), None)
        self._latest_curve.pop(mint.lower(), None)

    def get_price(self, mint: str) -> float | None:
        return self._latest_price.get(mint.lower())

    def get_curve(self, mint: str) -> CurveState | None:
        return self._latest_curve.get(mint.lower())

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    payload = await self._fetch(session)
                    self._update_prices(payload.get("tokens", []))
                    self.price_feed_healthy = True
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                    self.price_feed_healthy = False
                    logger.warning("hood.fun price refresh failed: %s", exc)
                await asyncio.sleep(self.poll_interval)
