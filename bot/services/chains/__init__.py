from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from bot.services.models import CurveState, Token

CHAIN_LABELS = {
    "robinhood": "🟢 Robinhood",
    "robinhood-nft": "🟢 Robinhood NFT",
}


class ChainAdapter(Protocol):
    chain_id: str
    price_feed_healthy: bool

    def stream_new_tokens(self) -> AsyncIterator[Token]: ...
    def watch(self, mint: str) -> None: ...
    def unwatch(self, mint: str) -> None: ...
    def get_price(self, mint: str) -> float | None: ...
    def get_curve(self, mint: str) -> CurveState | None: ...
    async def run(self) -> None: ...


def chain_label(chain: str) -> str:
    return CHAIN_LABELS.get(chain, chain.title())


def token_url(chain: str, mint: str) -> str:
    return f"https://hood.fun/coin/{mint}"


def build_adapters() -> list[ChainAdapter]:
    from bot.config import config
    from bot.services.chains.robinhood import RobinhoodAdapter

    factories = {
        "robinhood": lambda: RobinhoodAdapter(
            config.robinhood_data_url, config.robinhood_rpc_url,
            poll_interval=config.robinhood_poll_seconds,
            max_launch_age_seconds=config.max_launch_age_seconds,
        ),
    }
    unknown = set(config.enabled_chains) - factories.keys()
    if unknown:
        raise ValueError(f"unsupported chain(s) in ENABLED_CHAINS: {', '.join(sorted(unknown))}")
    return [factories[chain]() for chain in config.enabled_chains]
