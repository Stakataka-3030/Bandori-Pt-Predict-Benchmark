# Changelog

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
