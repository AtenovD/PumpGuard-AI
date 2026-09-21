# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions before 1.1.0 were never tagged; `v1.0.0` marks the repository as it stood before this release (commit `cbce7e0`).

## [Unreleased]

### Changed
- **Renamed to PumpGuard AI** (formerly `grokbot-pumpfun`). GitHub redirects the old URL.
- Documentation now describes the model layer as provider-neutral: any OpenAI-compatible chat-completions API works by setting `GROK_BASE_URL`, `GROK_API_KEY` and the two model names. xAI Grok remains the default. The `GROK_*` variable names are unchanged so existing `.env` files keep working.

## [1.1.0] - Honest simulation

Theme: make every number the bot reports mean something. The screening logic is unchanged; the way a trade is priced, exited, explained and reported is not.

### Added
- **Cost-aware fills.** Entries and exits are priced off the bonding curve, including the 1% fee and the price impact of the trade's size (`bot/services/curve.py`). A flat cost applies to graduated tokens. All assumptions are in `docs/simulation-model.md`.
- **Exit scheme.** Time-decaying ROI table, trailing stop and time stop alongside the stop-loss (`bot/services/exits.py`). Before this, stop-loss was the only way a position could close.
- **Abstain as a first-class outcome.** A model outage, an unparseable reply or missing data is an abstention, logged separately from a rejection and reported on its own.
- **Decision records for every outcome**, including the exact model calls behind each verdict (`docs/decision-records.md`, `GET /api/decisions`).
- **Risk-clamp journal and a total-exposure limit** (`MAX_TOTAL_EXPOSURE_ETH`). Each limit that cuts a position is recorded with the before/after size.
- **Protections** (`bot/services/protections.py`): a stop-loss guard that pauses entries after a cluster of stop-outs, and a lock while the price feed is unhealthy.
- **Real trade data for the auditor.** The board's `activity` block (trade count, volumes, recent trades with trader addresses) is now passed to the auditor.
- **Honest backtest report**: net PnL after costs, exit-reason breakdown, abstentions, open positions, a bootstrap 95% confidence interval and a plain-language sample-size verdict.
- **Launch-age guard** (`MAX_LAUNCH_AGE_SECONDS`, default 3600). The indexer's `/api/board` answered 436 tokens to one client and 10,630 to another, and the bot treated anything missing from its first poll as a new launch, so a change in what the endpoint serves would have flooded the paid pipeline with old tokens. Tokens older than the limit, and records with no timestamp, are now marked seen and skipped.
- `ROBINHOOD_POLL_SECONDS` to tune how often the board is polled.
- `bot.__version__`, this changelog.

### Fixed
- The auditor was called with empty holder and trade dictionaries and so judged nothing.
- Model scores were not bounded to 0..1, so `MAX_SOL_PER_TRADE` was not actually a cap: a reply of `score: 5` sized a position at 5x the limit.
- A Grok outage was logged as `scoring / below_threshold` and counted as a rejection.
- Backtest win rate was structurally zero: only stop-losses could close a position.
- Risk limits, alerts, stats and the dashboard were labelled SOL on an ETH chain.
- The NFT screener started against an endpoint that returns 404; it now probes the endpoint at startup and stays off, with a warning, if it is not there.

### Changed
- `MAX_SOL_PER_TRADE` / `DAILY_LOSS_LIMIT_SOL` are now `MAX_ETH_PER_TRADE` / `DAILY_LOSS_LIMIT_ETH`. The old names keep working (the new name wins if both are set).
- Auditor, narrative and timing now run one after another and stop at the first abstention, saving calls.
- Protections run before any paid call.
- `analysis.risk.size_sol` (webhook v1) keeps its name but is ETH.

### Database
Additive migration only: `positions` gains `token_amount`, `peak_pnl_pct`, `exit_proceeds`; a `decision_records` table is created. Existing data is untouched and older databases upgrade automatically on start.

### Design credits
Ideas taken from [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (MIT): abstain-not-guess failure handling, a full record per decision, risk clamps with an audit trail, and statistical honesty about sample size. Ideas taken from [Freqtrade](https://github.com/freqtrade/freqtrade) (GPLv3, concepts only, no code): ROI table, trailing stop, stop-loss guard, and documenting simulation assumptions.

## [1.0.0]
The repository as of commit `cbce7e0`: four Grok agents plus a researcher, risk manager, reputation book, dry-run executor, Robinhood Chain adapter, NFT screener, dashboard, webhooks and Prometheus metrics.
