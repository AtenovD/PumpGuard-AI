from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import aiohttp

from bot.services.models import NftCollection

logger = logging.getLogger(__name__)


class RobinhoodNftAdapter:
    """Polls a Robinhood Chain NFT marketplace indexer for new collections and
    their floor price.

    hood.fun does not (yet) document a public NFT marketplace API the way it
    documents `/api/board` for token launches, so this assumes the same
    shape as the token board — a `collections` list under `{data_url}/api/nft/collections`,
    each item carrying `address`, `name`, `symbol`, `creator`, `supply`,
    `uniqueMinters`, `timestamp`, and a floor price in wei as `floorPriceWei`.
    Confirm and adjust `_parse_collection`/`_floor_price` against the real
    endpoint once a Robinhood Chain NFT marketplace publishes one; nothing
    else in the pipeline needs to change since `NftCollection` is chain-agnostic.
    """

    chain_id = "robinhood-nft"

    def __init__(self, data_url: str, poll_interval: float = 10.0) -> None:
        self.data_url = data_url.rstrip("/")
        self.poll_interval = poll_interval
        self._watched: set[str] = set()
        self._latest_floor: dict[str, float] = {}
        self._baseline: set[str] | None = None
        self._emitted: set[str] = set()
        self.price_feed_healthy = False

    async def probe(self, session: aiohttp.ClientSession) -> str | None:
        """Check the assumed endpoint really exists before the screener starts.

        Returns None when the endpoint serves a usable collections list, or a
        human-readable reason when it does not. hood.fun answered 404 for this
        path at the time v1.1 was written, so without this check the screener
        would poll a page that does not exist forever and never say so.
        """
        try:
            async with session.get(
                f"{self.data_url}/api/nft/collections", timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status != 200:
                    return f"endpoint returned HTTP {response.status}"
                if "json" not in response.headers.get("Content-Type", ""):
                    return "endpoint did not return JSON"
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return f"endpoint unreachable: {exc}"
        if not isinstance(payload, dict) or "collections" not in payload:
            return "response has no 'collections' list"
        return None

    async def _fetch(self, session: aiohttp.ClientSession) -> list[dict]:
        async with session.get(
            f"{self.data_url}/api/nft/collections", timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        return payload.get("collections", [])

    @staticmethod
    def _floor_price(item: dict) -> float | None:
        floor_wei = item.get("floorPriceWei")
        if floor_wei and int(floor_wei) > 0:
            return int(floor_wei) / 10**18
        return None

    def _key(self, address: str) -> str:
        return address.lower()

    def _update_floors(self, items: list[dict]) -> None:
        for item in items:
            address = item.get("address")
            floor = self._floor_price(item)
            if address and self._key(address) in self._watched and floor is not None:
                self._latest_floor[self._key(address)] = floor

    @staticmethod
    def _parse_collection(item: dict) -> NftCollection:
        return NftCollection(
            address=str(item["address"]).lower(),
            name=item.get("name"),
            symbol=item.get("symbol"),
            creator=item.get("creator"),
            supply=item.get("supply"),
            unique_minters=item.get("uniqueMinters"),
            created_at=float(item.get("timestamp") or 0),
            floor_price=RobinhoodNftAdapter._floor_price(item),
        )

    async def stream_new_collections(self) -> AsyncIterator[NftCollection]:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    items = await self._fetch(session)
                    self._update_floors(items)
                    current = {self._key(x["address"]) for x in items if x.get("address")}
                    if self._baseline is None:
                        self._baseline = current
                        await asyncio.sleep(self.poll_interval)
                        continue
                    for item in reversed(items):
                        address = item.get("address")
                        key = self._key(address) if address else ""
                        if not key or key in self._baseline or key in self._emitted:
                            continue
                        self._emitted.add(key)
                        yield self._parse_collection(item)
                except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as exc:
                    logger.warning("hood.fun NFT data source failed: %s", exc)
                await asyncio.sleep(self.poll_interval)

    def watch(self, address: str) -> None:
        self._watched.add(self._key(address))

    def unwatch(self, address: str) -> None:
        key = self._key(address)
        self._watched.discard(key)
        self._latest_floor.pop(key, None)

    def get_floor_price(self, address: str) -> float | None:
        return self._latest_floor.get(self._key(address))

    def watched_addresses(self) -> set[str]:
        return set(self._watched)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    self._update_floors(await self._fetch(session))
                    self.price_feed_healthy = True
                except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as exc:
                    self.price_feed_healthy = False
                    logger.warning("hood.fun NFT floor refresh failed: %s", exc)
                await asyncio.sleep(self.poll_interval)
