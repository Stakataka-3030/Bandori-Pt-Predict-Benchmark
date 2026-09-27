# Changelog

## 0.3.11

- License project-authored source code under MPL 2.0, with the supplied character icon and third-party data outside that grant.
- Add the supplied in-app help, run a release check on startup, and display update or connection-failure status on the bottom button.
- Remove the requested explanatory copy from the member viewer and display T1500 without an asterisk.

## 0.3.10

- Add the local Windows Tsukushi app with Bestdori/HHWX source selection, four-tier member chart, image export, editable help slot, and version-aware GitHub Release checks. The distributable carries a derived model checkpoint rather than the raw benchmark.
- Add a single-file Windows exe that embeds the derived model state and recognizes future single-file assets in update checks.
- Fix generated viewer URLs with cache parameters, clarify initial prediction actions, and add the supplied application icon.
- Add `tsukushi-kaori` as the experimental deterministic point control and `tsukushi-aoi` as its 50-member trajectory ensemble built from completed new-regime event paths, with causal next-report weighting and no hand-inserted extreme members.
- Add a self-contained interactive viewer with member lines, endpoint 10th/90th marks, grouped terminal bars, and an auxiliary T1500 new-regime view.
- Add a gated, same-era 12-hour control correction for T500/T1500 after six completed new-regime events; preserve zero-shot and original development forecasts.
- Preserve the canonical baseline registry, scoring tiers, and frozen benchmarks. Document that member percentiles are not calibrated probabilities.

## 0.3.9

- Add a typed baseline registry distinguishing native baselines, public-formula replays, historical-algorithm reconstructions, formula-family reconstructions, and project experimental models.
- Add `baseline-registry` and `baseline-suite`; the suite runs every selected built-in protocol-v2 model across development / selection / final / all, with both point and probabilistic tracks when supported.
- Persist a machine-comparable summary plus full detailed phase reports including horizon/tier/era breakdowns, whole-event bootstrap intervals, and final regime-shift diagnostics.
- Explicitly mark `bestdori-hierarchical` as a formula-family reconstruction rather than a historical Bestdori platform forecast archive.
- Add an external replay audit covering Bestdori, HHWX, Rinko/DPRA, MYCX, Tsugu and Mokabot, including provenance limits and the next integration order.

## 0.3.8

- Make reward-era composition explicit in every model-evaluation phase via target and initial-training era counts.
- Detect when chronological final contains eras unseen in all pre-final training/selection events and label it `regime_shift_challenge` (or a mixed/same-regime variant).
- For one-shot final evaluation, add aggregate regime-shift diagnostics: first unseen-regime event (zero-shot), later post-adaptation events, and overall regime-shift score.
- Keep regime-shift classification and full phase-era composition judge-side; runners learn only the current event's ordinary task metadata, avoiding future-phase metadata leakage.
- Keep selection/final case-, tier-, horizon-, and era-level losses redacted; the new regime diagnostics are aggregate event-level summaries intended only for the central final report.

## 0.3.7

- Add `model-export-devkit`, producing a self-contained development-only frozen benchmark that contains warm-up + development events/truth but physically excludes selection/final events and labels.
- Make devkits evaluate their entire exposed target range as development, so workers can iterate locally without access to the central benchmark.
- Mark `n_events_scored` explicitly in reports to distinguish effective event sample size from correlated tier × horizon case count.
- Redact selection diagnostics to aggregate score/coverage/bootstrap-level output, matching the final holdout's anti-overfit posture.
- Document that protocol-v2 `public/tasks.json` is a trusted replay artifact, not a competition-safe package, because its shared `reference_events` retain labels and rely on logical `history_event_ids` filtering.

## 0.3.6

- Fix `model-eval` CLI parsing: split the runner command at the explicit `--` before argparse processes benchmark options, so `--phase`, `--track`, `--out`, and `--submission-out` are no longer swallowed by the runner positional.
- Keep the documented `... -- python model.py checkpoint.bin` syntax unchanged.
- Add an end-to-end CLI regression test that invokes `model-eval` with the separator and scores the example persistence runner.

## 0.3.5

- Add a persistent JSONL external-model API with a small Python `bandoribench_model.serve()` helper and executable persistence-runner example.
- Add `model-plan`, `model-export-training`, and `model-eval` so fixed or online-updating trained models can use a standardized leak-controlled interface and receive a score directly.
- Stream only previously completed event truth to runners; current/future labels are never included in forecast panels.
- Add deterministic chronological development / selection / final phases (~70/15/15), with multiple development checkpoints, case-level redaction for selection, and coarse one-shot final-holdout reporting.
- Require a runner declaration of the latest BandoriBench label baked into its initial checkpoint and mark protocol eligibility separately from numerical score validity.
- Allow the core evaluator to score a frozen case subset without changing the benchmark fingerprint, and document the model-selection/overfitting contract.

## 0.3.4

- Change protocol-v2 target eligibility from event-tier batch rejection to horizon-local complete multi-tier panels: a bad origin drops only that event × horizon panel, not every other horizon for the activity.
- Preserve the full requested-tier sibling set required by multi-tier analog and CARE; if one tier is unavailable at an origin, sibling cases at that same origin are excluded with explicit panel-failure provenance.
- Record candidate case count, eligible/excluded case counts, eligible target events, fully excluded target events, and exclusion reasons in the frozen protocol/manifest.
- Extend `freeze-walkforward` CLI output with eligible target-event and excluded-case counts and add regression coverage for partial and fully excluded target events.

## 0.3.3

- Add a deterministic, standard-library-only CN calendar provider for the State Council General Office holiday and makeup-workday schedules covering 2019–2026.
- Add `calendar-fetch` and commit `calendars/cn-2019-2026.json` for reproducible CN benchmark freezing; prediction and scoring remain offline.
- Preserve causal announcement timing with `known_at`; revisions carry both `previous_type` and `previous_known_at` so neither revised schedules nor the earlier schedules they replaced leak into older hindcasts.
- Encode the later 2019 Labor Day adjustment and the nationwide 2020 Spring Festival extension, plus cross-year official holiday classification such as 2022-12-31 in the 2023 New Year break.
- Package `calendar_provider`, add CLI/snapshot/revision regression tests, and document authoritative provenance and the CN-Core freeze workflow.

## 0.3.2

- Add `causal-stack`, a protocol-v2 ensemble that combines the multi-tier analog point forecast with hierarchical Bestdori using an L1-optimal blend weight learned only from earlier completed hindcasts.
- Calibrate probabilistic spread by centering the analog quantile shape on the stacked point forecast and selecting a spread multiplier from earlier OOS WIS only.
- Keep CARE-S / CARE-S2 as ablations rather than tuning their horizon/tier behavior from the already inspected JP leaderboard.

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
