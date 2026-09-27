"""Group 27: FINAL DEPLOYABLE CANDIDATE — `regime-multiplier-v1`.

Semantics fixed after the Group-26 audit.  The `k_ex_boundary` coefficient corrects the
NEW-REGIME T1000 terminal multiplier, so it must be applied to whichever pool supplies
that multiplier (same-era preferred, legacy fallback), not only to the legacy branch.

    pred = cur(t) * M_hat(tier, horizon, era) * k(tier, era)

    M_hat : median of final/cur(horizon) over COMPLETED events; same-era pool preferred,
            legacy pool as fallback (only reachable for ev315, the first new-regime
            event, where no same-era completion exists yet).
    k     : k_ex_boundary for (tier=1000, era=voice500_1500), else 1.0.

Parameter: k_ex_boundary = 0.92, selected by the pre-registered rule in `f3_select.py`
("minimise the worst development-block ratio to Rinko").  Its structural grounding is
the reward-boundary flip at CN event 310 (see experiments/00_audit/FINDINGS.md and
03_shape/FINDINGS.md), which independently identifies a value near 0.74 with the same
sign.

This module is the single source of truth for the internal harness AND the official
`model-eval` runner.
"""
from __future__ import annotations

import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "00_audit"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "01_harness"))

HOUR = 3600 * 1000
K_EX_BOUNDARY = 0.92
ERA_NEW = "voice500_1500"
ERA_OLD = "voice1000"


def _samples(events, tier, horizon):
    vals = []
    for rec in events:
        t = rec["tiers"].get(tier)
        if not t or not t["points"]:
            continue
        cut = rec["end_at"] - horizon * HOUR
        vis = [p for p in t["points"] if p[0] <= cut]
        if not vis:
            continue
        _, ep = max(vis, key=lambda z: z[0])
        if ep <= 0:
            continue
        vals.append(t["final"] / ep)
    return vals


def multiplier(completed, tier, horizon, era):
    """Median terminal multiplier from COMPLETED events only. Same-era preferred."""
    same = _samples([r for r in completed if r["era"] == era], tier, horizon)
    if same:
        return st.median(same)
    legacy = _samples([r for r in completed if r["era"] == ERA_OLD], tier, horizon)
    if legacy:
        return st.median(legacy)
    return None


def predict_from_parts(tier, horizon, era, cur, completed):
    """Core predictor, usable without a panel object."""
    if cur <= 0:
        return 0.0
    m = multiplier(completed, tier, horizon, era)
    if m is None:
        return float(cur)
    k = K_EX_BOUNDARY if (era == ERA_NEW and tier == 1000) else 1.0
    return max(0.0, cur * m * k)


def predict(panel, history) -> float:
    """Causal prediction. `history` exposes .available(issued_at) -> completed records."""
    return predict_from_parts(
        panel["tier"], panel["horizon_hours"], panel["era"],
        float(panel["history"][-1]["ep"]),
        history.available(panel["issued_at"]),
    )


if __name__ == "__main__":
    import causal as C
    res = C.eval_model(predict)
    C.report("regime-multiplier-v1 (k_ex=0.92, k always applied)", res)
