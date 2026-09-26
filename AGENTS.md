# Repository rules

- Never confuse a synthetic demo, an algorithm re-run, and an archived live forecast.
- Before each commit, globally check version markers and update VERSION, bandoribench.VERSION, pyproject.toml and documentation consistently.
- Run `python -m unittest discover -s tests -v` before committing.
- Order events by server-local actual start timestamps, never numeric IDs. Preserve the CN 311 -> 310 -> 314 correction and early 312/313; reward era changes at CN event 310's start.
- Keep test truth out of predictor inputs, normalize using calibration only, and preserve raw loss alongside the display score.
- Do not turn missing data into zero PT, guess terminal labels, change a frozen benchmark in place, or silently drop failed predictions.
- Do not commit secrets, raw third-party archives, or real referee truth. Respect API limits and redistribution terms.
