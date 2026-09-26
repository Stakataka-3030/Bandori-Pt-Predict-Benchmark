# Changelog

## 0.3.1

- Add `care-s2`: choose conditional-correction shrinkage λ and residual-spread scale τ from each task's earlier out-of-sample historical forecasts. No horizon/tier weight is tuned on the current benchmark outcome.
- Cache CARE ridge fits and OOS records across expanding prefixes to reduce repeated pure-Python fitting work.
- Add `freeze-walkforward --tiers ...` so a collected dataset may freeze a common scoring panel without discarding it just because another sparse tier was also collected.
- Preserve CARE-S v0.3.0 as a reproducible ablation.

## 0.3.0

- Add `care-s`, the first trainable CARE model: the multi-tier analog ensemble is retained as a prior, then a ridge-regularized conditional log correction is fit only from each task's earlier completed events.
- Build probabilistic CARE output by convolving the current analog scenarios with rolling out-of-sample correction residuals; enforce nonnegative remaining PT and cross-tier terminal rank ordering.
- Add `bandoribench-calendar-v1` support to protocol-v2 freezing. Calendar bytes and SHA-256 become part of the frozen benchmark; optional per-day `known_at` prevents late calendar announcements leaking into earlier origins.
- Add server-local clock/weekend/holiday/makeup-workday features plus CN reward-boundary semantics to CARE-S.
- Add causal, calendar, probability and cross-tier-order regression tests.

## 0.2.3

- Fix the HHWX instant-projection regression test's hand-calculated expected value (500 + 300/10min × 100min = 3500); implementation was already correct.

## 0.2.2

- Add `hhwx-instant` and `hhwx-24h`, exact protocol-v2 replays of HHWX's public short-window and 24-hour tracker projections using the published 9m45s / 23h55m minimum windows.
- Add `rinko-dpra-replay`, a standard-library reconstruction of the public Rinko/DPRA rolling regression + slope/gamma `FIN` forecast retained by the 2022 Hoshino plugin.
- Keep Tsugu/MYCX migration out of this patch: historical Tsugu rates are not reliably archived, while MYCX's JP/CN priors require deliberate refitting rather than a cosmetic server switch.
- Document that the eventual main model should be refit/calibrated on CN after JP architecture work.

## 0.2.1

- Add `multitier-analog-ensemble`, a causal protocol-v2 model using T100/T1000/T2000 recent growth fractions and cross-tier ratios to retrieve historical analog events.
- Convert historical analog completion fractions into a weighted ensemble of terminal-cutoff candidates; emit both median point forecasts and q05/q10/q25/q50/q75/q90/q95.
- Add causality and full-coverage regression tests for the new ensemble.

## 0.2.0

- Add protocol v2 walk-forward hindcasting with a configurable warm-up period and expanding historical context for every later event.
- Preserve every causally visible raw tracker observation in v2 tasks; 6-hour coarse sampling remains only for legacy protocol-v1/Pilot reproduction.
- Add rolling `calibrated-linear24` and `linear24-quantiles` baselines that learn residuals only from each task's allowed earlier events.
- Add `bestdori-hierarchical`: Bestdori-formula-family rates shrink event-type history toward the all-history tier rate instead of failing on sparse types.
- Add 50%/90% coverage, interval-width and median-bias diagnostics to probabilistic reports.
- Document the JP 80-event audit: 54 complete three-tier events, 237/240 monotone tracker series and 168 observed post-end labels.
- Keep protocol-v1, `freeze`, and its Pilot baselines for reproducibility.

## 0.1.5

- Freeze only chronologically sorted events with complete labels for every tier requested during collection; incomplete events no longer consume calibration/test slots.
- Persist `requested_tiers` in real and synthetic datasets and record all pre-freeze incomplete-event exclusions in the protocol.
- Fix the v0.1.4 regression test whose fixture accidentally placed its final tracker point exactly at `endAt`, which correctly qualified for the new post-end terminal rule.
- Document the first real JP sample: 89/90 usable tracker series and 65 automatically labeled series across 30 recent events.

## 0.1.4

- Add `post_end_final`: when archives are missing, stable tracker cutoff observation(s) between Bestdori `endAt` and `aggregateEndAt` may anchor the final cutoff.
- Reject automatic terminal labeling when multiple post-end observations disagree instead of silently choosing one.
- Expose archive availability, post-end point count, distinct values and lag-to-end in acquisition audit.
- Record the real JP finding that recent tracker histories are roughly half-hourly while Bestdori archives do not cover recent event IDs.

## 0.1.3

- Use Bestdori `/api/archives/all.5.json` archived `cutoff[server][tier]` as the primary final-cutoff label source.
- Keep explicit provider-final and post-`aggregateEndAt` observations only as fallbacks; ordinary last tracker observations remain ineligible.
- Add archive-shape regression tests and provenance for each archive-derived label.

## 0.1.2

- Fix real Bestdori collection: use `endAt` as the forecast/PT-stop time instead of the nonexistent `aggregateAt`, and preserve `aggregateEndAt` separately as the result-finalization anchor.
- Add conservative automatic labels from tracker observations at/after `aggregateEndAt`; ordinary last observations are still not treated as final.
- Accept nullable tracker rows and add regression coverage for the real Bestdori metadata shape and pre-aggregation no-guess behavior.

## 0.1.1

- Implement executable coarse-window benchmark with PointScore and WIS-based ProbScore, frozen calibration scales and whole-event splits.
- Add public archive collector, causal six-hour sampling, explicit terminal-label validation, release fingerprints and external submission scoring.
- Add four baselines, including clearly labeled calibration-only Bestdori-formula-family replay.
- Encode user-supplied CN chronology/reward corrections and separate era diagnostics.
- Add 39 offline tests, synthetic demo and CI. Real network integration and real benchmark corpus remain unverified.

## 0.1.0

- Initialize the new repository version marker.
