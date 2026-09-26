# CARE-S model

CARE-S is the first trainable model in BandoriBench. It is deliberately small enough for the current event sample size and uses only the Python standard library.

## Forecast construction

For each protocol-v2 task:

1. Recreate the current multi-tier analog ensemble from only `history_event_ids`.
2. For each earlier event that itself has at least five earlier complete events, recreate the same analog forecast at the same horizon.
3. Learn the historical log correction `log(final / analog_median)` with ridge regression from causal features.
4. Estimate correction uncertainty from rolling out-of-sample historical residuals rather than in-sample training residuals.
5. Combine each current analog scenario with each historical residual and emit q05/q10/q25/q50/q75/q90/q95.
6. Floor outcomes at current observed PT and project terminal quantiles to ranking order across requested tiers.

The current implementation caps multiplicative log adjustments to [-2, 2] only as a numerical guardrail.

## Features

CARE-S uses:

- current tier 6/12/24h relative growth and a short-vs-day acceleration term;
- the same multi-tier growth/ratio state used by the analog retriever;
- event elapsed/remaining fraction, event type, era, input age and analog-distance/spread diagnostics;
- target rank and CN reward-boundary semantics;
- server-local clock/day-of-week and known-future calendar fractions over the remaining event, final 24h and final 6h.

These are statistical conditioning variables. The model does not claim that its latent correction corresponds to a specific player-behavior mechanism.

## Calendar contract

```json
{
  "schema": "bandoribench-calendar-v1",
  "server": "cn",
  "utc_offset_hours": 8,
  "days": {
    "2026-10-01": "holiday",
    "2026-10-10": {"type": "makeup_workday", "known_at": 1780000000000}
  }
}
```

Allowed day types are `weekday`, `weekend`, `holiday`, and `makeup_workday`.

A string entry is treated as known throughout the covered benchmark period. An object may provide `known_at`; before that timestamp, the model falls back to ordinary weekday/weekend classification. This lets a historical benchmark avoid using schedule changes that were not yet public at the forecast origin.

The calendar is frozen into the benchmark body, its SHA-256 is recorded in the protocol, and it is included in the benchmark fingerprint.

## Server fitting

JP is the architecture-development environment. CN must refit CARE-S on CN history; JP coefficients are not copied into CN. CN reward-era features distinguish the supplied pre-310 T1000 reward boundary from the post-310 T500/T1500 boundaries.

## Current scope

CARE-S predicts terminal cutoffs and marginal quantiles. It does not yet emit full future trajectories or preserve an externally visible joint ensemble member identity across tiers. Those are later CARE extensions.
