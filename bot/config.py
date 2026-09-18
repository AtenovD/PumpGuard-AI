from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> set[int]:
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


def _parse_chains(raw: str) -> tuple[str, ...]:
    chains = tuple(dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip()))
    return chains or ("robinhood",)


def _parse_urls(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in raw.split(",") if value.strip()))


def _env_float(name: str, default: str, legacy: str | None = None) -> float:
    """Read a float env var, falling back to a pre-v1.1 name.

    The risk limits were named after SOL before the project moved to Robinhood
    Chain. Deployed instances still set the old names, and silently ignoring
    them would reset a live risk limit to its default, so the old name keeps
    working (the new one wins when both are set).
    """
    raw = os.getenv(name)
    if raw is None and legacy:
        raw = os.getenv(legacy)
    return float(raw if raw is not None else default)


@dataclass(frozen=True)
class Config:
    bot_token: str = field(default_factory=lambda: os.environ["BOT_TOKEN"])
    admin_ids: set[int] = field(default_factory=lambda: _parse_ids(os.getenv("ADMIN_IDS", "")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "pumpguard.db"))
    dashboard_port: int = field(default_factory=lambda: int(os.getenv("DASHBOARD_PORT", "8000")))

    # Grok (xAI) — used for the 4 screening agents. Never used to place trades.
    grok_api_key: str = field(default_factory=lambda: os.getenv("GROK_API_KEY", ""))
    grok_base_url: str = field(
        default_factory=lambda: os.getenv("GROK_BASE_URL", "https://api.x.ai/v1/chat/completions")
    )
    grok_fast_model: str = field(default_factory=lambda: os.getenv("GROK_FAST_MODEL", "grok-4-fast"))
    grok_checker_model: str = field(default_factory=lambda: os.getenv("GROK_CHECKER_MODEL", "grok-4"))
    grok_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("GROK_TIMEOUT_SECONDS", "20")))
    grok_max_retries: int = field(default_factory=lambda: int(os.getenv("GROK_MAX_RETRIES", "3")))
    grok_breaker_failure_threshold: int = field(
        default_factory=lambda: int(os.getenv("GROK_BREAKER_FAILURE_THRESHOLD", "5"))
    )
    grok_breaker_cooldown_seconds: float = field(
        default_factory=lambda: float(os.getenv("GROK_BREAKER_COOLDOWN_SECONDS", "60"))
    )

    enabled_chains: tuple[str, ...] = field(
        default_factory=lambda: _parse_chains(os.getenv("ENABLED_CHAINS", "robinhood"))
    )
    robinhood_data_url: str = field(
        default_factory=lambda: os.getenv("ROBINHOOD_DATA_URL", "https://hood.fun")
    )
    robinhood_rpc_url: str = field(
        default_factory=lambda: os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
    )
    # A "new launch" monitor must never treat an old token as new. If the indexer
    # ever serves a different or larger dataset, everything not seen before would
    # otherwise flood the paid pipeline. Older tokens are marked seen and skipped.
    max_launch_age_seconds: int = field(default_factory=lambda: int(os.getenv("MAX_LAUNCH_AGE_SECONDS", "3600")))
    robinhood_poll_seconds: float = field(default_factory=lambda: float(os.getenv("ROBINHOOD_POLL_SECONDS", "5")))
    min_launch_age_seconds: int = field(default_factory=lambda: int(os.getenv("MIN_LAUNCH_AGE_SECONDS", "60")))
    min_unique_buyers: int = field(default_factory=lambda: int(os.getenv("MIN_UNIQUE_BUYERS", "5")))
    copycat_similarity_threshold: float = field(
        default_factory=lambda: float(os.getenv("COPYCAT_SIMILARITY_THRESHOLD", "0.85"))
    )

    # Risk manager — six independent limits, checked before any signal is surfaced.
    # Amounts are in the chain's native currency (ETH on Robinhood Chain).
    max_eth_per_trade: float = field(
        default_factory=lambda: _env_float("MAX_ETH_PER_TRADE", "0.5", "MAX_SOL_PER_TRADE")
    )
    daily_loss_limit_eth: float = field(
        default_factory=lambda: _env_float("DAILY_LOSS_LIMIT_ETH", "2.0", "DAILY_LOSS_LIMIT_SOL")
    )
    max_total_exposure_eth: float = field(
        default_factory=lambda: float(os.getenv("MAX_TOTAL_EXPOSURE_ETH", "1.5"))
    )
    max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MAX_TRADES_PER_DAY", "10")))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "5")))
    stop_loss_pct: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "35")))

    # Exit scheme (see bot/services/exits.py). Percentages are net of costs.
    roi_table: str = field(default_factory=lambda: os.getenv("ROI_TABLE", "0:100,30:40,120:15,360:0"))
    trailing_activate_pct: float = field(
        default_factory=lambda: float(os.getenv("TRAILING_ACTIVATE_PCT", "25"))
    )
    trailing_stop_pct: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_PCT", "12")))
    max_hold_minutes: int = field(default_factory=lambda: int(os.getenv("MAX_HOLD_MINUTES", "720")))

    # Protections (see bot/services/protections.py).
    protect_stoploss_limit: int = field(default_factory=lambda: int(os.getenv("PROTECT_STOPLOSS_LIMIT", "3")))
    protect_window_minutes: int = field(default_factory=lambda: int(os.getenv("PROTECT_WINDOW_MINUTES", "240")))
    protect_lock_minutes: int = field(default_factory=lambda: int(os.getenv("PROTECT_LOCK_MINUTES", "120")))

    # Simulation cost when no bonding curve is available (e.g. graduated tokens).
    fallback_cost_bps: int = field(default_factory=lambda: int(os.getenv("FALLBACK_COST_BPS", "100")))

    # Reputation book — creators are blocked after this many rugs.
    rug_loss_pct: float = field(default_factory=lambda: float(os.getenv("RUG_LOSS_PCT", "60")))
    block_creator_after_rugs: int = field(default_factory=lambda: int(os.getenv("BLOCK_CREATOR_AFTER_RUGS", "1")))
    forget_creators_after_days: int = field(default_factory=lambda: int(os.getenv("FORGET_CREATORS_AFTER_DAYS", "90")))

    alert_chat_id: str | None = field(default_factory=lambda: os.getenv("ALERT_CHAT_ID") or None)
    webhook_urls: tuple[str, ...] = field(default_factory=lambda: _parse_urls(os.getenv("WEBHOOK_URLS", "")))
    public_digest_chat_id: str | None = field(
        default_factory=lambda: os.getenv("PUBLIC_DIGEST_CHAT_ID") or None
    )

    # NFT screener — a second, independent source-type covering Robinhood Chain NFT
    # collections. Screening-only: there is no bonding-curve entry price for an NFT
    # collection, so nothing here is ever fed to DryRunExecutor.
    nft_screener_enabled: bool = field(
        default_factory=lambda: os.getenv("NFT_SCREENER_ENABLED", "false").strip().lower() == "true"
    )
    robinhood_nft_data_url: str = field(
        default_factory=lambda: os.getenv("ROBINHOOD_NFT_DATA_URL", "https://hood.fun")
    )
    nft_min_launch_age_seconds: int = field(
        default_factory=lambda: int(os.getenv("NFT_MIN_LAUNCH_AGE_SECONDS", "60"))
    )
    nft_min_unique_minters: int = field(
        default_factory=lambda: int(os.getenv("NFT_MIN_UNIQUE_MINTERS", "5"))
    )
    nft_alert_chat_id: str | None = field(
        default_factory=lambda: os.getenv("NFT_ALERT_CHAT_ID") or os.getenv("ALERT_CHAT_ID") or None
    )
    # Floor-sweep watcher: alerts on a sudden move away from a collection's
    # first-observed floor price, in either direction, within one poll window.
    floor_sweep_drop_pct: float = field(
        default_factory=lambda: float(os.getenv("FLOOR_SWEEP_DROP_PCT", "40"))
    )
    floor_sweep_pump_pct: float = field(
        default_factory=lambda: float(os.getenv("FLOOR_SWEEP_PUMP_PCT", "100"))
    )

    # Optional per-user Grok OAuth. This is separate from the screening API key.
    xai_oauth_client_id: str = field(default_factory=lambda: os.getenv("XAI_OAUTH_CLIENT_ID", ""))
    xai_oauth_client_secret: str = field(default_factory=lambda: os.getenv("XAI_OAUTH_CLIENT_SECRET", ""))
    xai_oauth_redirect_uri: str = field(default_factory=lambda: os.getenv("XAI_OAUTH_REDIRECT_URI", ""))
    oauth_encryption_key: str = field(default_factory=lambda: os.getenv("OAUTH_ENCRYPTION_KEY", ""))


config = Config()
