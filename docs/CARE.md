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


## CARE-S2 adaptive shrinkage

CARE-S v0.3.0 deliberately applied the learned correction and its OOS residual distribution at full strength. CARE-S2 treats those as hypotheses that must earn weight from prior hindcasts.

For every current task, CARE-S2 reconstructs rolling historical OOS forecasts and chooses:

- `lambda ∈ {0, .25, .5, .75, 1}` to minimize prior point MAE for the conditional log correction;
- `tau ∈ {0, .25, .5, .75, 1}` to minimize prior WIS for the OOS residual spread.

Both choices use only events already present in the current task's `history_event_ids`. `lambda=0` means the learned point correction is rejected; `tau=0` means no extra CARE residual convolution is added beyond the analog ensemble's own dispersion. This creates a causal path back to the strong analog baseline when the extra model does not validate historically.

CARE-S2 also caches ridge models and OOS prefix records during one prediction run; the cache changes runtime only, not forecast semantics.


## Causal stacking baseline

`causal-stack` is intentionally simpler than CARE-S. It asks whether the strongest two established signals are complementary before adding another learned feature model.

For each current `tier × horizon` task it reconstructs earlier hindcasts for:

- the multi-tier analog median and quantiles;
- hierarchical Bestdori.

The point blend is

```text
p = (1-w) * analog + w * bestdori
```

where `w` is the clipped weighted median of the per-hindcast L1 breakpoints `(truth-analog)/(bestdori-analog)`, weighted by the magnitude of component disagreement. Thus the weight is fit from earlier completed cases only.

Probability output keeps the analog ensemble's quantile shape, recenters it on the stacked point, and selects a spread scale from `{0.5, 0.75, 1, 1.25, 1.5, 2}` using earlier sequential OOS WIS. No current-test horizon or tier score is used to pick either parameter.
