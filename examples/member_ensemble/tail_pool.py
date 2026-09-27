"""CN new-regime point forecaster - NOMINATED FIXED CANDIDATE.

model_id:      cn-newera-tail-pool
model_version: 1
training_cutoff_ms: 0

=====================================================================
MECHANISM (three parts, each measured separately - see EXPERIMENTS.md)
=====================================================================

1. THE REMAINING SPAN, NOT THE HORIZON
   The protocol supplies every raw tracker observation up to `input_cutoff_at`,
   which sits 0.25 h before `issued_at` for all 90 dev cases. So the last
   observation is essentially "now", and the span still to close is
   `end_at - t_last_observed`. A horizon-indexed multiplier is mis-specified:
   measured prefix coverage inside these 178-226 h events ranges from 103.5 h to
   217.5 h.

   With a near-linear final stretch,

       final ~= last + v_trailing * (end_at - t_last)

   Unfitted, this identity alone gives raw 65.67 M PT / score 39.31 on the six
   dev events, versus Rinko-DPRA's 64.70 M / 42.11. It is a RATIO statement, so
   scaling a tier by any constant leaves the predicted multiplier unchanged:
   the model is exactly invariant to the per-tier level shift that the earlier
   round measured as the inherited models' main transfer risk.

2. CROSS-TIER SPEED POOLING  (the largest single lever: 47.39 M -> 41.94 M)
   All three sibling tiers arrive in ONE `forecast_panel`, so a dimensionless
   terminal rate

       rate_tier = v_trailing(tier) / last(tier)          [1/hour]

   can be computed jointly. The realised cross-tier ratio is highly stable at
   the cut (corr of log T1000/T500 at cut vs at final = +0.9644 over 48 legacy
   events), so the median rate across the tiers of the same panel is a lower
   variance estimate of each tier's rate. Each tier's own rate is pooled 50/50
   with the panel median, rescaled by that tier's own `last`:

       v_used = 0.5 * v_own + 0.5 * median_rate * last

   This is a variance-reduction device on a dimensionless quantity, NOT a
   cross-tier level model: no tier offset, level gap or era term is estimated.

3. ONLINE DIMENSIONLESS CORRECTION  (65.67 M -> 47.39 M on top of part 1)
   `observe_event` streams completed earlier events. For each observed event,
   at THAT event's own cut and for each horizon, the model recomputes the same
   structural prediction and records

       q = final / structural_prediction

   A per-HORIZON median of q, shrunk toward 1 by n/(n+k), is applied to later
   events, clipped to [0.90, 1.20].

   Per-horizon rather than per-tier is a measured choice: the prefix length is
   itself horizon-indexed, and per-tier pooling costs 10.1 M PT (m6 ablation).

=====================================================================
CAUSALITY
=====================================================================
* `training_cutoff_ms = 0`: no benchmark label is baked in. The constants are
  window lengths (2,3,6)h, pooling weights, the shrinkage k=10, the pool weight
  0.5 and the q clip. None was fitted against a benchmark label; the two that
  were selected on dev data (pool weight, k) were validated by
  leave-one-event-out selection (m15: +9.32% honest for the pool weight).
* Truth reaches the model ONLY through `observe_event`. Verified with the
  official devkit: withholding every target event after event 315 changes
  event 315's forecast by exactly 0.0 (m4_causal.py T1).
* A one-event-ahead check was also run: poisoning every observed label moves
  later events but leaves the zero-shot channel untouched; the target-label
  channel is provably zero.
* `forecast_panel` carries no label key. Degenerate panels (empty, one point,
  constant, zero, huge outlier) fall back to the last observation, never raise.
* `below_current_count = 0` in every official run: predictions never go below
  the last observation, which the benchmark itself treats as non-physical.
"""

from __future__ import annotations

import math

WINDOWS = (2.0, 3.0, 6.0)
WEIGHTS = (0.45, 0.40, 0.15)
HORIZONS = (72, 48, 24, 12, 6)
POOL_WEIGHT = 0.5
K_SHRINK = 10.0
Q_CLIP = (0.90, 1.20)


def _series(history):
    pts = []
    for row in history or []:
        try:
            t = float(row["time"])
            v = float(row["ep"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(t) and math.isfinite(v) and v >= 0:
            pts.append((t, v))
    pts.sort()
    out = []
    for t, v in pts:
        if out and t == out[-1][0]:
            out[-1] = (t, v)
        else:
            out.append((t, v))
    return out


def _trailing_speed(pts):
    if len(pts) < 2:
        return None
    t1, v1 = pts[-1]
    ests, wts = [], []
    for w, wt in zip(WINDOWS, WEIGHTS):
        older = [p for p in pts if p[0] <= t1 - w * 3600000.0]
        t0, v0 = (older[-1] if older else pts[0])
        dt = (t1 - t0) / 3600000.0
        if dt <= 0:
            continue
        ests.append(max(0.0, (v1 - v0) / dt))
        wts.append(wt)
    if not ests:
        return None
    return sum(e * w for e, w in zip(ests, wts)) / sum(wts)


def _median(values):
    s = sorted(values)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


class Champion:
    model_id = "cn-newera-tail-pool"
    model_version = "1"
    training_cutoff_ms = 0
    supports_online_update = True

    def __init__(self):
        self.pool = {}

    # ---------------- lifecycle ----------------
    def initialize(self, context):
        pass

    def observe_event(self, event):
        start = float(event.get("start_at", 0))
        end = float(event.get("end_at", 0))
        if end <= start:
            return
        for _tier_s, blob in (event.get("tiers") or {}).items():
            label = (blob.get("label") or {}).get("ep")
            if label is None:
                continue
            pts = []
            for p in blob.get("points", []):
                try:
                    t, v = float(p["time"]), float(p["ep"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(t) and math.isfinite(v) and v >= 0 and t <= end:
                    pts.append((t, v))
            pts.sort()
            if len(pts) < 2:
                continue
            for hz in HORIZONS:
                cut = end - hz * 3600000.0
                pre = [p for p in pts if p[0] <= cut]
                if len(pre) < 2:
                    continue
                t1, v1 = pre[-1]
                rem_h = (end - t1) / 3600000.0
                v = _trailing_speed(pre)
                if v is None or rem_h <= 0:
                    continue
                pred = v1 + v * rem_h
                if not math.isfinite(pred) or pred <= 0:
                    continue
                q = float(label) / pred
                if math.isfinite(q) and q > 0:
                    self.pool.setdefault(hz, []).append(q)

    def _q(self, hz):
        vals = self.pool.get(hz) or []
        if not vals:
            return 1.0
        raw = _median(vals)
        n = len(vals)
        w = n / (n + K_SHRINK)
        q = w * raw + (1.0 - w) * 1.0
        return min(max(q, Q_CLIP[0]), Q_CLIP[1])

    # ---------------- forecast ----------------
    def predict_panel(self, panel):
        hz = panel.get("horizon_hours")
        tasks = list(panel.get("tasks", []))

        # joint cross-tier state: dimensionless terminal rate per sibling tier
        state = {}
        for task in tasks:
            pts = _series(task.get("history"))
            if len(pts) < 2:
                continue
            v = _trailing_speed(pts)
            last = pts[-1][1]
            if v is None or last <= 0:
                continue
            state[task["case_id"]] = (pts, v, last)
        rates = [v / last for (_p, v, last) in state.values() if v > 0]
        median_rate = _median(rates)

        out = []
        for task in tasks:
            cid = task["case_id"]
            entry = state.get(cid)
            if entry is None:
                pts = _series(task.get("history"))
                last = pts[-1][1] if pts else 0.0
                out.append({"case_id": cid, "prediction": last})
                continue
            pts, v_own, last = entry
            t_last = pts[-1][0]
            end = float(task.get("end_at", t_last))
            rem_h = max(0.0, (end - t_last) / 3600000.0)

            v = v_own
            if median_rate is not None and v_own > 0:
                v_sib = median_rate * last
                v = (1.0 - POOL_WEIGHT) * v_own + POOL_WEIGHT * v_sib

            pred = (last + v * rem_h) * self._q(hz)
            if not math.isfinite(pred) or pred < last:
                pred = last
            out.append({"case_id": cid, "prediction": pred})
        return out


if __name__ == "__main__":
    import importlib.util
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    cand = None
    probe = here
    for _ in range(4):
        probe = os.path.dirname(probe)
        p = os.path.join(probe, "bandoribench_model.py")
        if os.path.exists(p):
            cand = p
            break
    if cand is None:
        raise SystemExit("cannot locate bandoribench_model.py")
    spec = importlib.util.spec_from_file_location("bandoribench_model", cand)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.serve(Champion())
