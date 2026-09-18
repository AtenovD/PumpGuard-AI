# Decision records

Before v1.1 a token that was rejected left one `signals` row: a stage, an outcome and a reason. The verdicts, the data the agents saw and the model that answered were gone, so nobody could ask "why did we skip this?" or check afterwards whether a rejection was a mistake.

Since v1.1 **every** screening outcome (filtered, protected, abstained, rejected, blocked by risk, or bought) writes one row to `decision_records`.

## Reading them

```bash
sqlite3 pumpguard.db "SELECT id, subject, stage, outcome FROM decision_records ORDER BY id DESC LIMIT 10;"
```

or over HTTP from the read-only dashboard (put authentication in front of it, as with every analytics route):

```
GET /api/decisions?limit=20
GET /api/decisions?mint=0xabc...
```

## Shape of a record

```json
{
  "chain": "robinhood",
  "subject": "0xabc...",
  "symbol": "ABC",
  "outcome": "abstain",
  "stage": "abstain",
  "detail": "auditor:insufficient_trade_data",
  "score": null,
  "inputs": {
    "token": { "mint": "...", "creator": "...", "curve": { "virtual_eth": 2.81, "...": "..." },
               "trades": { "trade_count": 1, "volume_eth": 0.02 } },
    "market_snapshot": { "launches_per_minute": 0.0 },
    "feed_healthy": true
  },
  "verdicts": { "researcher": { "...": "..." }, "auditor": { "abstained": true, "abstain_reason": "insufficient_trade_data" } },
  "risk": null,
  "fill": null,
  "locks": [],
  "stages": [ { "stage": "abstain", "outcome": "abstain", "detail": "auditor:insufficient_trade_data", "at": 1788868800.1 } ],
  "model_calls": [],
  "elapsed_seconds": 0.004
}
```

* `verdicts` is keyed by pipeline role (`researcher`, `auditor`, `narrative`, `timing`, `checker`).
* `model_calls` holds one entry per Grok call: agent, model, a 16-character SHA-256 prefix of the system prompt (so you can tell which prompt version answered), the exact input message, and the parsed answer. `answered: false` means the call failed.
* `risk.clamps` lists every hard limit that cut the position size, with the size before and after.
* `locks` lists any protection that refused the entry, with its scope, reason and expiry.
* `fill` is the simulated entry (effective price, tokens, fee, price impact, cost model).

A crash inside the pipeline is recorded too (`outcome: "error"`) before the exception propagates. Saving a record can never fail a screening: a storage error is logged and swallowed.

## Abstain is not reject

An agent *abstains* when it cannot form a view: the model was unreachable, its reply was unparseable, or there was not enough data to judge. An abstention can never buy (`approve` is false), but it is logged with `outcome = "abstain"` and reported separately, so an API outage cannot masquerade as a screening decision and quietly distort the funnel and the backtest.

The auditor abstains **without calling the model** when the indexer published fewer than 3 trades for a token: there is nothing to audit, and asking a model anyway would make it improvise a verdict about data it was never shown.
