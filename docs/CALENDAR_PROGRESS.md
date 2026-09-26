# CN Calendar Provider Progress

**Branch:** `calendar-provider-v033`  
**Target version:** `0.3.3`  
**Updated:** 2026-09-26

## Goal

Finish the frozen CN calendar input required before creating `runs/cn-core-v1`: authoritative 2019–2026 holidays, makeup workdays, causal announcement timing, deterministic regeneration, and benchmark fingerprint integration.

## Completed implementation

- Added deterministic, standard-library-only CN provider for 2019–2026.
- Encoded annual State Council General Office schedules and source provenance.
- Added the 2019 Labor Day later adjustment.
- Added the 2020 nationwide Spring Festival extension.
- Added causal `known_at` handling.
- Added revision history via `previous_type` + `previous_known_at`, preventing both revision leakage and first-announcement leakage.
- Added `calendar-fetch --server cn --years ... --out ...`.
- Added committed `calendars/cn-2019-2026.json`.
- Added calendar package to setuptools packaging.
- Added regression tests for official adjustments, cross-year New Year classification, three-stage revision knowledge, CLI generation, and committed-snapshot reproducibility.
- Bumped project markers consistently to 0.3.3.

## Data policy

Prediction and scoring remain offline. No holiday API is called at forecast time. Unsupported years fail loudly rather than falling back to incomplete generic holiday data.

For notices recorded only at date precision, knowledge becomes available at the next China-local midnight. This deliberately favors leakage prevention over same-day availability.

## Verification

Official notice dates/schedules were rechecked before the implementation commit, including the 2019 Labor Day adjustment, 2020 Spring Festival extension, and 2024–2026 schedules.

Repository CI is triggered by the branch push and is the final executable validation for Python 3.11/3.13 on Windows and Ubuntu.

## Next benchmark step

After CI passes, freeze CN-Core with:

```bash
python bandoribench.py freeze-walkforward data/cn-80/dataset.json \
  --warmup-events 12 --tiers 500 1000 2000 \
  --calendar calendars/cn-2019-2026.json --out runs/cn-core-v1
```
