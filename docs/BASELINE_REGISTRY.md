# Baseline Registry and External Replay Audit

This document separates three questions that are easy to conflate:

1. Can an algorithm be replayed causally on the frozen benchmark?
2. Is the replay an exact/preserved public algorithm, or only a formula-family reconstruction?
3. Does a result represent an archived historical platform forecast? A current-code rerun is not a historical platform archive.

The canonical executable registry is exposed by:

```bash
python bandoribench.py baseline-registry
python bandoribench.py baseline-suite runs/cn-core-v1 --out runs/cn-core-baselines.json
```

## Built-in registry

| Model | Class | Status | Tracks |
|---|---|---|---|
| persistence | weak baseline | native baseline | point |
| linear24 | weak baseline | native baseline | point |
| calibrated-linear24 | causal statistical baseline | native reconstruction | point |
| linear24-quantiles | causal statistical baseline | native reconstruction | point + probabilistic |
| bestdori-hierarchical | formula-family reconstruction | **not** a historical platform archive | point |
| hhwx-instant | public-formula replay | strict public-algorithm replay | point |
| hhwx-24h | public-formula replay | strict public-algorithm replay | point |
| rinko-dpra-replay | historical-algorithm replay | public-code reconstruction | point |
| multitier-analog-ensemble | project statistical model | project model | point + probabilistic |
| care-s | project experimental model | project model | point + probabilistic |
| care-s2 | project experimental model | project model | point + probabilistic |
| causal-stack | project experimental model | project model | point + probabilistic |

`bestdori-recalibrated` remains protocol-v1/Pilot-only and is intentionally excluded from the protocol-v2 suite.

## External candidate audit

### Bestdori

Bestdori exposes tracker data and a `/api/tracker/rates.json` resource. The current API documentation describes rates by server/event type/tier, but a current rates resource is not evidence of the rate value that was visible at each historical prediction origin.

Status:

- Current formula/rate-family reconstruction: **available** through `bestdori-hierarchical`.
- Archived historical online predictions: **not established**.
- Historical rate snapshots suitable for exact old-platform replay: **not established**.

Until historical rate/prediction snapshots are found, do not label a modern rerun as “Bestdori historical accuracy”.

References:

- https://bestdori.com/
- https://github.com/WindowsSov8forUs/bestdori-api
- https://github.com/WindowsSov8forUs/bestdori-api/blob/main/docs/api/eventtracker.md

### HHWX

The benchmark keeps the short-window and ~24h projection formulas as explicit protocol-v2 replays. These are classified as public-formula replays, not archived screenshots of what HHWX displayed at every historical timestamp.

Reference:

- https://hhwx.org/bandori/eventtracker

### Rinko / DPRA / Hoshino plugin

The preserved `assassingyk/bandori-predict` Hoshino plugin states that its prediction core came from `Rinko-Predict-Python`. Its retained `getPred.py` includes the DPRA regression, slope analysis, gamma correction and FIN construction used by the benchmark reconstruction.

Status: suitable as a public-code historical-algorithm replay, with reconstruction provenance retained.

References:

- https://github.com/assassingyk/bandori-predict
- https://github.com/assassingyk/bandori-predict/blob/8566093433ebf6ce9feeb070a971205ad21813a6/bandori_predict/getPred.py

### MYCX Skeleton + Kalman Filter

The current public `byydzh/MYCX_1000` project is a serious external candidate rather than a cosmetic service wrapper:

- current production model is Skeleton + Kalman Filter;
- T500/T1000/T1500/T2000 are modeled independently with exact-tier history;
- the repository includes frozen-history/offline evaluation tooling;
- `tuner/global_benchmark.py` has a formal train/holdout split;
- the current default learned preset records its own training/holdout metadata.

However, the current repository/preset is a **current reproducible model**, not automatically an archived historical forecast for older events. Porting it into BandoriBench should use the exact frozen input origins and causal history contract, then classify the result as an external current-code replay.

Status: **high-priority adapter candidate**.

Reference:

- https://github.com/byydzh/MYCX_1000

### Tsugu prediction line 2

Current Tsugu backend forks document a “prediction line 2” dependency on `MYCX_1000`. Treating that route as an independent baseline would double-count MYCX unless a distinct algorithm/configuration is identified.

Reference:

- https://github.com/Kudryavka03/tsugu-bangdream-bot-backend

### Mokabot

Mokabot documentation says its Bandori prediction model is its own rather than Bestdori's. The linked algorithm document in the retained repository is still a TODO and does not provide enough implementation detail to build a trustworthy replay from documentation alone.

Status: external candidate, **insufficient replay specification currently located**.

References:

- https://github.com/MokaDevelopers/mokabot2
- https://docs-mokabot.arisa.moe/plugins/mb2pkg_bandori.html

## Next external integration order

1. Run and freeze the complete built-in `baseline-suite` on CN-Core.
2. Build a MYCX adapter against the same frozen task origins and same-tier causal history.
3. Continue searching for timestamped Bestdori `rates.json` / prediction snapshots before claiming historical Bestdori performance.
4. Keep Tsugu/Mokabot separate only if an independently specified model is recovered.

The benchmark should always preserve source provenance beside the score. A numerically comparable score does not make two provenance classes historically equivalent.
