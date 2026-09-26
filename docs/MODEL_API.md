# External model API

BandoriBench v0.3.5 adds a persistent JSONL interface so a trained model can be evaluated without hand-building submission JSON or receiving current/future truth by accident.

## Recommended workflow

Use the three chronological phases in order:

1. **development** — first ~70% of target events. Use detailed diagnostics and temporal blocks for architecture/hyperparameter work.
2. **selection** — next ~15%. Choose among candidates already developed; the default report omits case-level losses.
3. **final** — last ~15%. Treat this as a one-shot tail holdout. The default report intentionally removes per-case and per-dimension diagnostics.
4. **all** — full prequential diagnostic. Useful for research, but it should not be the only number used to select a model.

The split is chronological, deterministic, and derived from the frozen benchmark. With 42 target events it becomes 30 development / 6 selection / 6 final.

This cannot prevent a determined local user from opening private benchmark truth or repeatedly rerunning the final phase. The goal is to make the safe path easy and accidental leakage difficult.

## Inspect the plan

```powershell
python bandoribench.py model-plan runs/cn-core-v1 --out runs/cn-core-v1/model-plan.json
```

The plan records exact event IDs, scored case IDs, phase training cutoffs, and development blocks.

## Export a leak-safe fixed-checkpoint training set

For a checkpoint intended for the selection phase:

```powershell
python bandoribench.py model-export-training runs/cn-core-v1 --phase selection --out data/model-selection-train.json
```

For a final candidate:

```powershell
python bandoribench.py model-export-training runs/cn-core-v1 --phase final --out data/model-final-train.json
```

The export includes only events whose labels are allowed before the requested phase starts. It never includes labels from the phase being scored. If a checkpoint already contains part or all of this export, set `training_cutoff_ms` to the latest included benchmark-event end time; the evaluator then skips replaying those already-consumed events and only streams newer allowed history.

A model runner must declare `training_cutoff_ms`: the latest **BandoriBench event label** already included in its initial checkpoint. Use `0` when no benchmark labels were baked into the checkpoint. The evaluator marks the run `protocol_eligible=false` if this declaration is missing/invalid or exceeds the phase's allowed cutoff.

This is a trust declaration, not a filesystem sandbox.

## Runner protocol

The evaluator starts a long-lived process and communicates with newline-delimited JSON on stdin/stdout. **stdout is reserved for protocol JSON; write logs to stderr.**

The normal sequence is:

```text
init
observe_event ...     # only already-ended training events
forecast_panel ...    # one event × horizon, all requested sibling tiers together
forecast_panel ...
observe_event         # current event truth becomes visible only after its forecasts
...
finish
```

A `forecast_panel` never contains current/future labels. This prevents the common accidental leakage mode where a training script reads the whole reference-event truth pool.

For Python, use `bandoribench_model.serve()` and implement:

```python
class MyModel:
    model_id = "my-model"
    model_version = "2026-09-26"
    training_cutoff_ms = 0
    supports_online_update = True

    def initialize(self, context):
        ...

    def observe_event(self, event):
        # optional causal online update
        ...

    def predict_panel(self, panel):
        return [
            {"case_id": task["case_id"], "prediction": ...}
            for task in panel["tasks"]
        ]
```

For probabilistic evaluation, each returned row must additionally include all benchmark quantiles and set `prediction` equal to the median, exactly like the normal submission protocol.

The runner may be written in any language; the Python helper is optional.

## Direct evaluation

Point track:

```powershell
python bandoribench.py model-eval runs/cn-core-v1 --phase development --track point --out runs/my-model-dev-report.json --submission-out runs/my-model-dev-submission.json -- python my_model.py checkpoint.bin
```

Final holdout:

```powershell
python bandoribench.py model-eval runs/cn-core-v1 --phase final --track point --out runs/my-model-final-report.json -- python my_model.py checkpoint-final.bin
```

`examples/persistence_model.py` is an executable reference runner.

## Anti-overfit design

The interface combines several safeguards:

- chronological 70/15/15 development-selection-final phases;
- phase-specific leak-safe training exports;
- stateful streaming, so benchmark labels arrive only after the model has forecast that event;
- complete multi-tier panels at each origin;
- an initial-checkpoint training cutoff declaration;
- multiple contiguous development blocks and cumulative checkpoints instead of one scalar tuning target;
- coarse selection output and deliberately redacted final-holdout diagnostics;
- the existing whole-event bootstrap and era/tier/horizon macro aggregation.

A useful candidate should not merely maximize one development block. Prefer models whose performance is stable across development blocks, horizons, tiers, eras, and the selection phase before touching final.


## CLI separator note

v0.3.6 fixes the initial v0.3.5 parser bug where the runner remainder could swallow `model-eval` options after the benchmark path. Keep all BandoriBench options before the explicit `--`, then put the runner command after it:

```powershell
python bandoribench.py model-eval runs/cn-core-v1 --phase development --track point --out runs/report.json -- python my_model.py checkpoint.bin
```


## Multi-worker competition layout

Do **not** distribute the central benchmark's `public/tasks.json` or `private/benchmark.json` to workers. Protocol-v2 `public/tasks.json` is intended for trusted replay compatibility: its shared `reference_events` retain event labels, and built-in algorithms obey each task's `history_event_ids` causally. That logical contract is insufficient for a model-training competition.

Instead, the judge creates a development-only devkit:

```powershell
python bandoribench.py model-export-devkit runs/cn-core-v1 --out D:\Creations\PtBenchmark\PtBenchmark-Shared\cn-core-development
```

The devkit physically contains only the original warm-up events and development target events. Selection/final events and truth are absent. Workers may freely inspect the devkit and repeatedly run:

```powershell
python bandoribench.py model-eval D:\Creations\PtBenchmark\PtBenchmark-Shared\cn-core-development --phase development --track point --out runs\development.json -- python model.py
```

For the 42-event CN-Core split, the central benchmark has 30 development events, 6 selection events, and 6 final events. The corresponding 90 selection/final cases are not 90 independent samples: they are 6 events × 5 horizons × 3 tiers, with strong within-event dependence. Treat the event count as the effective model-selection sample size.

Recommended tournament rule: each worker nominates exactly one development champion to the central judge; the judge evaluates all champions once on selection; one global winner advances to final. Selection and final reports intentionally omit case/tier/horizon diagnostics.


## Explicit reward-regime shift semantics

The CN reward era is part of each event/task metadata. The benchmark uses actual chronology, with explicit CN corrections around events 310–314, rather than assuming numeric event ID order:

- `voice1000`: the older T1000 voice-expression reward regime.
- `voice500_1500`: the newer T500/T1500 reward-boundary regime.

`model-plan` now records `target_era_counts` and `initial_training_era_counts` for every phase. It also emits a top-level `regime_shift` object. If every final-era label is unseen before final, `final_role` is `regime_shift_challenge`; mixed and same-regime tails are labeled separately.

For the current CN-Core chronology, this makes the interpretation explicit: development/selection validate the legacy regime, while final is intended to measure transfer into the new reward regime. Final remains prequential. Therefore its central one-shot report separates:

- `first_new_regime_event_score`: zero-shot transfer before any new-regime final truth has been observed;
- `post_adaptation_score`: later final events after at least the first new-regime truth has been delivered through `observe_event`;
- `overall_regime_shift_score`: the normal final macro score across the whole final phase.

These are final-report diagnostics, not new tuning surfaces. Workers still receive only the development devkit.
