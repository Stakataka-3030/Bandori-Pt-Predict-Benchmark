# Tsukushi: experimental 50-member trajectories

This example lives under `examples/member_ensemble/`. It wraps an unchanged
new-regime point control with 50 trajectory members. It does **not** replace
the baseline registry, original CN-Core scoring tiers, or any frozen forecast.

## Why the control is separate

The control remains the scored point forecast. For each origin it supplies a
terminal PT value `C`; the latest visible tracker supplies `Y`. A completed
new-regime event is first predicted by the control using only earlier events.
Only after that event finishes, its actual remaining trajectory is saved in
units of its **control-predicted remaining PT**. New member trajectories are
drawn from these prior same-regime paths or convex combinations of two whole
event paths:

```text
member(t) = Y + (C - Y) × historical_normalized_path(t)
```

The control line uses the median historical progression shape and ends at
`C`. A member may end above or below it. There are 50 visual members, but
only as many independent historical scenario families as completed earlier
new-regime events. The implementation never inserts arbitrary 0.05x/4x
outliers or extends beyond its observed path envelope. With no completed
same-era event it shows no learned spread: all members coincide with control.

Each new report reweights the *previously issued* paths against actual new
tracker values with a robust likelihood, then re-anchors the remaining paths
to the newly observed PT and updated control. Low-weight paths fade. The
runner does not impose a fixed number of deleted and injected paths. The
viewer archives each issued snapshot rather than rewriting old forecasts.

The existing control panel T500/T1000/T2000 is preserved exactly. T1500 is
an auxiliary new-regime research tier calculated separately, because its
older historical coverage was insufficient for the canonical CN-Core panel.
Adding it to the visualization does not revise earlier scores.

## Evidence and interpretation

On the six new-regime development events, the example reproduced all 90
control point forecasts exactly (PointScore **57.5145**). In a descriptive
chronological audit across six later events after at least two prior
new-regime events, member endpoint 10th–90th percentiles contained **77/120**
tier × horizon outcomes; auxiliary T1500 was **15/30**. There are only six
independent events in that audit. The member median had higher raw MAE than
the control (341,189 versus 262,921 PT over those cases). Consequently:

- The control is the point prediction.
- The 10%/90% marks describe the current finite member set. They are not
  calibrated probabilities or a verified 80% interval.
- Outlying paths trace back to completed event trajectories. Fifty lines do
  not imply fifty independent observed regimes.

## Reproduce locally

The runtime and tests use only Python's standard library. A trusted local
protocol-v2 bundle is required for replay. Its `public/tasks.json` retains
labeled reference events and must not be distributed as a competition devkit.

```powershell
python -m unittest discover -s tests -v
python bandoribench.py model-eval PATH/TO/DEVKIT --phase development --track point --bootstrap 0 --out runs/member-report.json --submission-out runs/member-submission.json -- python examples/member_ensemble/member_ensemble.py
python examples/member_ensemble/replay.py PATH/TO/BUNDLE 322 --out runs/member-322.json
python examples/member_ensemble/build_member_viewer.py runs/member-322.json --out runs/member-322.html
```

The self-contained HTML viewer shows all available tiers, 50 member paths,
control and member-median curves, member 10th/90th endpoint marks, and a
grouped terminal bar chart. Browser rendering should be checked separately
in a permitted local browser; the repository tests cover the data contract
and causal boundaries.

Methodological background: [ECMWF on initial/model perturbations](https://www.ecmwf.int/en/research/modelling-and-prediction/quantifying-forecast-uncertainty)
and [ECMWF on re-centring members around a control](https://www.ecmwf.int/en/newsletter/171/news/soft-re-centring-ensemble-data-assimilations).
