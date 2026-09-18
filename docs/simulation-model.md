# Simulation model

Every position in this project is simulated (see the README: no wallet, no signing, no live executor). A simulation is only as honest as the assumptions it makes, so this page lists all of them. If a number in a report looks too good, check it against this list first.

## What is priced, and how

**Entry.** A buy is priced off the token's bonding curve using the reserves hood.fun publishes for it. The curve is treated as constant product (`x * y = k`) over the *virtual* reserves.

* The 1% trade fee (`tradeFeeBps`, read from the curve) is taken from the ETH leg: off the input on a buy, off the proceeds on a sell.
* **Price impact is real.** Buying 0.3 ETH into a fresh curve (2.81 virtual ETH) moves the price about 10.6%; 0.5 ETH moves it about 17.6%. The 1% fee is charged on top, so the effective price is about 11.7% and 18.8% above spot respectively. Before v1.1 the simulator filled at spot with no fee and no impact.

**Exit.** A sale is priced against the *currently observed* reserves, with the position layered on top of them (`curve.with_position`): our deposit is added and our tokens removed, exactly as if the buy had really happened. This matters because a simulated buy never touches the chain. Selling into reserves that never saw our buy would charge the price impact twice, and every position would look underwater the moment it opened. With the position layered in, an immediate exit on an unchanged curve costs just the two fees (about -2%).

**Exit liquidity.** A seller can never be paid more than the ETH the curve actually holds (`real_eth`). If a sale would exceed it, the payout is capped and the trade is flagged `liquidity_capped`.

**Graduated tokens.** Once a token migrates to a DEX pool there is no bonding curve to price against. A flat cost of `FALLBACK_COST_BPS` (default 100) is charged on each side. This *understates* impact for large sizes.

## Exits

A position closes on the first rule that fires, in this order: `stop_loss`, `roi`, `trailing_stop`, `time_stop`. All thresholds are compared against the **net** result of selling right now (after fee and impact), not the spot price.

| Rule | Setting | Default | Fires when |
|---|---|---|---|
| Stop-loss | `STOP_LOSS_PCT` | 35 | net loss reaches the percentage |
| ROI table | `ROI_TABLE` | `0:100,30:40,120:15,360:0` | held for at least N minutes and net profit is at least the paired percentage |
| Trailing stop | `TRAILING_ACTIVATE_PCT` / `TRAILING_STOP_PCT` | 25 / 12 | profit ran to the activation level, then gave back the stop percentage from its peak |
| Time stop | `MAX_HOLD_MINUTES` | 720 | still open after this long |

The ROI table reads "after 0 minutes take +100%, after 30 take +40%, after 120 take +15, after 360 take anything not negative". Malformed entries are skipped rather than raising, so a typo cannot stop the watcher.

## What is NOT modelled

Be sceptical of the report wherever these matter:

* **Latency and failed transactions.** Fills are instant and always succeed.
* **Gas.** Not charged.
* **Front-running and MEV.** Not modelled.
* **Your own market impact on others.** Positions are small next to a real market; here they can be large next to a thin curve, and the simulated buy does not move the observed reserves other trades see.
* **Fee schedule changes.** The fee is whatever the curve reported when the position was priced.
* **Slippage tolerance.** A real order could revert on a moved price; the simulation never reverts.

## Why the report also prints a confidence interval

The launchpad is thin and its data is not stable: while v1.1 was being built, the board endpoint returned 436 tokens to one HTTP client and 10,630 to another, and its latest launches were more than a day old. A win rate over a handful of trades is noise. The report therefore prints the sample size, a 95% bootstrap confidence interval for the mean PnL, and a plain-language verdict ("too few trades to conclude anything") until at least 30 closed trades exist.

## Borrowed ideas

The exit scheme and protections are inspired by concepts in [Freqtrade](https://github.com/freqtrade/freqtrade) (a time-decaying ROI table, trailing stop, stop-loss guard) and its practice of documenting its backtest assumptions. Freqtrade is GPLv3 and **no Freqtrade code is used here**; everything was written from scratch from the documented behaviour. Neither Freqtrade nor ai-hedge-fund models price impact, which is why `bot/services/curve.py` exists.
