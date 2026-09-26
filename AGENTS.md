# Repository rules

- Never confuse a synthetic demo, an algorithm re-run, and an archived live forecast.
- Before each commit, globally check version markers and update VERSION, bandoribench.VERSION, pyproject.toml and documentation consistently.
- Run `python -m unittest discover -s tests -v` before committing.
- Order events by server-local actual start timestamps, never numeric IDs. Preserve the CN 311 -> 310 -> 314 correction and early 312/313; reward era changes at CN event 310's start.
- Keep test truth out of predictor inputs, normalize using calibration only, and preserve raw loss alongside the display score.
- Do not turn missing data into zero PT, guess terminal labels, change a frozen benchmark in place, or silently drop failed predictions.
- Do not commit secrets, raw third-party archives, or real referee truth. Respect API limits and redistribution terms.
- External model APIs must not expose current/future target labels to forecast calls; only completed events may be streamed as training observations after their forecast panels are finished.
- Treat development as the tuning surface, selection as candidate choice, and final as a one-shot tail holdout; do not add richer final diagnostics by default.
- Do not describe protocol-v2 public/tasks.json as truth-free or competition-safe: reference_events retain labels for trusted replay. Use model-export-devkit for worker distribution.
- In model competitions, count held-out events separately from tier × horizon cases; selection/final candidate budgets are global across workers, not per-worker leaderboard sweeps.
