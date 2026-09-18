# Signal webhook schema

PumpGuard sends one HTTP `POST` with `Content-Type: application/json` to every URL in `WEBHOOK_URLS` whenever a token passes the complete pipeline and a dry-run position is opened. Webhook delivery does not depend on `ALERT_CHAT_ID`. A timeout, connection error, or non-2xx response is retried once after 500 ms; failure of one endpoint does not stop delivery to the others or roll back the simulated position.

Consumers must branch on `schema_version`. Version 1 has this exact envelope:

```json
{
  "schema_version": 1,
  "event": "signal.passed",
  "emitted_at": 1788868800,
  "analysis": {
    "token": {
      "mint": "token address",
      "symbol": "PUMP",
      "name": "Example token",
      "creator": "creator address",
      "native_in_curve": 18.4,
      "unique_buyers": 21,
      "created_at": 1788868700.0,
      "chain": "robinhood",
      "reference_price": 0.0000123
    },
    "researcher": {
      "name": "researcher",
      "score": 0.8,
      "summary": "no suspicious history",
      "flags": [],
      "approve": true,
      "fallback": false
    },
    "auditor": {
      "name": "auditor",
      "score": 0.74,
      "summary": "holder distribution looks acceptable",
      "flags": [],
      "approve": true,
      "fallback": false
    },
    "narrative": {
      "name": "narrative",
      "score": 0.69,
      "summary": "recognizable narrative",
      "flags": [],
      "approve": true,
      "fallback": false
    },
    "timing": {
      "name": "timing",
      "score": 0.71,
      "summary": "recent observed window is favorable",
      "flags": [],
      "approve": true,
      "fallback": false
    },
    "checker": {
      "name": "checker",
      "score": 0.66,
      "summary": "no decisive rejection reason",
      "flags": [],
      "approve": true,
      "fallback": false
    },
    "total_score": 0.715,
    "risk": {
      "approved": true,
      "reason": "within_limits",
      "size_sol": 0.25
    }
  }
}
```

`emitted_at` and `token.created_at` are Unix seconds in UTC. Nullable token fields (`symbol`, `name`, `creator`, `unique_buyers`, and `reference_price`) can be JSON `null`. On a passing signal all five verdict objects and `risk` are present; the object shapes mirror the dataclasses in `bot/services/models.py`. New optional fields may be added within version 1, but existing fields and meanings will not be removed or changed without incrementing `schema_version`.

Endpoints should return any 2xx status quickly and process asynchronously. Delivery is at-least-once because a successful request whose response is lost can be retried; use a combination of `event`, `emitted_at`, `analysis.token.chain`, and `analysis.token.mint` for deduplication.

## Added in v1.1 (still `schema_version` 1)

Every addition is optional and additive, as promised above; existing fields keep their meaning.

| Field | Meaning |
|---|---|
| `analysis.token.curve` | Bonding-curve reserves the fill was priced against: `virtual_eth`, `virtual_tokens`, `real_eth`, `fee_bps`. `null` for graduated tokens |
| `analysis.token.trades` | Trade statistics from the launchpad indexer (`trade_count`, `volume_eth`, `volume_24h_eth`, `recent_sample`, ...). `null` when the indexer published nothing |
| `analysis.<verdict>.abstained` / `abstain_reason` | `true` when the agent could not form a view (model down, unparseable reply, or no data). A passing signal never contains an abstention, so on this event both are `false`/`null`; they matter to consumers that also read decision records |
| `analysis.risk.clamps` | Every hard limit that cut the position size: `[{"limit": "daily_budget_share", "before": 0.3, "after": 0.03}]` |
| `analysis.fill` | The simulated entry: `price` (effective, costs included), `tokens`, `fee_eth`, `impact_pct`, `model` (`curve` or `flat`) |

`analysis.risk.size_sol` is the position size in the chain's **native currency, which is ETH on Robinhood Chain**. The field keeps its historical name because renaming it would break every existing consumer of version 1.

`analysis.<verdict>.fallback` is retained and always equals `abstained`; prefer `abstained` in new code.
