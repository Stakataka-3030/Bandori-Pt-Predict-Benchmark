# Calendar provider

BandoriBench treats calendars as **public known-future inputs with publication times**, not as live features fetched during prediction.

## Freeze flow

1. `calendar-fetch` materializes an authoritative candidate JSON.
2. `freeze-walkforward --calendar ...` validates and normalizes it.
3. The normalized calendar SHA-256 becomes part of the benchmark protocol and benchmark fingerprint.
4. Predictors consume only that frozen copy.

## CN 2019–2026

The bundled provider encodes State Council General Office annual holiday schedules for 2019 through 2026, including weekend makeup workdays. Source URLs and publication dates are embedded in the generated JSON provenance.

Two later nationwide changes are applied separately:

- 2019-03-22: Labor Day changed to May 1–4, with April 28 and May 5 as makeup workdays.
- 2020-01-27 public release: the Spring Festival holiday was extended nationwide through February 2. February 1 had previously been announced as a makeup workday.

The 2024 schedule is directly confirmed by the 2023-10-25 State Council notice; the 2025 schedule by the official 2024 State Council Gazette; and the 2026 schedule by the 2025-11-04 State Council notice.

## Causal announcement semantics

For date-level notices without a retained trusted release timestamp, `known_at` uses the **next China-local midnight after publication**. This is intentionally conservative.

A revision stores both `previous_type` and `previous_known_at`:

- before `previous_known_at`: fall back to ordinary weekday/weekend;
- from `previous_known_at` until the revision `known_at`: use the prior announced classification;
- at/after the revision `known_at`: use the revised classification.

This prevents both late revisions and the earlier schedule they replaced from leaking into older hindcasts.
