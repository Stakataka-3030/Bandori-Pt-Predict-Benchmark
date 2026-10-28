"""Offline, point-only joint-tier research candidate; not the app default.

Frozen 2026-10-28 candidate: robust completed-event pair ratios, plus a
tempered family-level sequential filter for Rui. No network or state writes.
It did not pass the separate T1500 validation: see docs/JOINT_RESEARCH.md.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from engine import HOUR, TIERS, _linear, predict, read_state
from output_order import project_ranked
from reward_topology_control import visible_history

MODEL_VERSION = "joint-rui-20261028-locked-v1"


def pair_prior(events, start, event_id):
    """Learn dimensionless ratios from strictly earlier, completed CN events."""
    records, seen = [], set()
    for event in sorted(events, key=lambda e: int(e["start_at"])):
        if event.get("era") != "voice500_1500" or event.get("server", "cn") != "cn":
            continue
        eid = int(event["event_id"])
        if eid in seen:
            raise ValueError("duplicate pair-prior event")
        if eid == event_id or int(event["end_at"]) >= start:
            raise ValueError("pair prior contains target or unfinished events")
        if int(event.get("aggregate_end_at", event["end_at"])) >= start:
            raise ValueError("pair prior settlement was not available")
        seen.add(eid)
        labels = []
        for tier in (1000, 1500):
            blob = event.get("tiers", {}).get(str(tier), {})
            label = blob.get("label", {}).get("ep")
            if label is None:
                break
            label_row = blob["label"]
            if max(int(label_row.get("time", event["end_at"])),
                   int(label_row.get("available_at", event["end_at"]))) >= start:
                raise ValueError("pair-prior terminal was not available")
            value = float(label)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid pair-prior terminal")
            labels.append(value)
        if len(labels) != 2:
            continue
        if labels[1] > labels[0]:
            raise ValueError("crossed historical pair terminals")
        records.append((eid, math.log(labels[1] / labels[0])))
    values = [value for _, value in records]
    mu = st.median(values) if values else None
    sigma = (max(.04, 1.4826 * st.median(abs(v - mu) for v in values))
             if len(values) >= 2 else .06)
    return {"event_ids": [eid for eid, _ in records], "mu": mu, "sigma": sigma}


def reconcile(values, current, prior, remaining):
    """The frozen lower-anchor, robust-deadband pair candidate."""
    out = dict(values)
    n, mu = len(prior["event_ids"]), prior["mu"]
    diagnostic = {"n_prior": n, "ratio_prior": math.exp(mu) if mu is not None else None,
                  "status": "insufficient_prior"}
    p10, p15 = float(out[1000]), float(out[1500])
    if n >= 3 and min(p10, p15) > 0:
        raw = math.log(p15 / p10)
        if raw > 0:
            fitted = min(mu, 0.0)
            diagnostic["status"] = "crossing_to_prior"
        else:
            difference = raw - mu
            shrink = max(0.0, 1 - 1.5 * prior["sigma"] / abs(difference)) if abs(difference) > 1e-12 else 0
            strength = n / (n + 2.0) * min(1.0, max(0.0, remaining) / 72.0)
            fitted = raw - strength * shrink * difference
            diagnostic["status"] = "deadband_shrunk" if shrink > 0 else "inside_band"
        # Keep the independently predicted T1000 anchor in this candidate.
        out[1000] = max(current[1000], math.exp(math.log(p10)))
        out[1500] = max(current[1500], math.exp(math.log(p10) + fitted))
        out[1000] = min(out[1000], out[500])
        out[1500] = min(out[1500], out[500])
        out[1500] = max(out[1500], out[2000])
        out[1000] = max(out[1000], out[1500])
        out[1500] = min(out[1500], out[1000])
    out = project_ranked(out, current)
    diagnostic["ratio_used"] = out[1500] / out[1000] if out[1000] else None
    return out, diagnostic


def _weighted_median(pairs):
    ordered = sorted(pairs)
    total = sum(weight for _, weight in ordered)
    positions, cumulative = [], 0.0
    for value, weight in ordered:
        positions.append((value, (cumulative + weight / 2) / total))
        cumulative += weight
    if .5 <= positions[0][1]:
        return positions[0][0]
    for (v0, p0), (v1, p1) in zip(positions, positions[1:]):
        if .5 <= p1:
            return v0 + (.5 - p0) / (p1 - p0) * (v1 - v0) if p1 > p0 else v1
    return positions[-1][0]


def _ordered(values):
    blocks = []
    for tier in TIERS:
        blocks.append([values[tier], 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            right = blocks.pop()
            blocks[-1][0] += right[0]
            blocks[-1][1] += right[1]
    return dict(zip(TIERS, [total / count for total, count in blocks for _ in range(count)]))


def _family_filter(panel, state):
    tasks = panel["tasks"]
    start, end, issue = (int(tasks[0][key]) for key in ("start_at", "end_at", "issued_at"))
    first = start + 3 * HOUR
    if issue < first:
        raise ValueError("before first three-hour report")
    checkpoints = sorted(set(list(range(first, issue, 3 * HOUR))
                             + list(range(max(first, end - 24 * HOUR), issue, HOUR)) + [issue]))
    families = {int(f["event_id"]): f for f in state["fit"]["early_progress_paths"]}
    if len(families) != len(state["fit"]["early_progress_paths"]):
        raise ValueError("duplicate historical family")
    if not set(families).issubset(set(map(int, state["completed_event_ids"]))):
        raise ValueError("family missing from completed training events")
    def fractions(eid, at):
        progress = (at - start) / (end - start)
        return {t: _linear(families[eid]["paths"][str(t)], progress) for t in TIERS}
    weights, previous, replayed, result = {}, None, 0, None
    for at in checkpoints:
        visible = {int(t["tier"]): visible_history(t["history"], at, min(at, int(t.get("input_cutoff_at", issue)))) for t in tasks}
        if any(not history for history in visible.values()):
            continue
        current = {t: float(history[-1]["ep"]) for t, history in visible.items()}
        eligible = {eid: fractions(eid, at) for eid in families}
        eligible = {eid: q for eid, q in eligible.items() if all(v is not None and v > 0 for v in q.values())}
        if len(eligible) < 2:
            continue
        if not weights:
            weights = {eid: 1 / len(eligible) for eid in eligible}
        else:
            new = [eid for eid in eligible if eid not in weights]
            if new:
                share, total = 1.0 / len(eligible), sum(weights.values())
                weights = {eid: w * (1 - share * len(new)) / total for eid, w in weights.items()}
                weights.update({eid: share for eid in new})
        if previous is not None:
            prev_at, prev_current = previous
            logw = {}
            for eid, q in eligible.items():
                prior_q = fractions(eid, prev_at)
                penalty = 0.0
                if all(v is not None and v > 0 for v in prior_q.values()):
                    residuals = [math.log(max(current[t], 1) / max(prev_current[t] * q[t] / prior_q[t], 1)) for t in TIERS]
                    level = st.mean(residuals)
                    penalty = st.mean(((r - level) / .035) ** 2 for r in residuals)
                logw[eid] = math.log(max(weights[eid], 1e-12)) - min(2.0, .5 * penalty)
            peak = max(logw.values())
            raw = {eid: math.exp(w - peak) for eid, w in logw.items()}
            total = sum(raw.values())
            weights = {eid: .9 * w / total + .1 / len(eligible) for eid, w in raw.items()}
            replayed += 1
        terminals = {eid: {t: current[t] / q[t] for t in TIERS} for eid, q in eligible.items()}
        result = _ordered({t: _weighted_median([(terminals[eid][t], weights[eid]) for eid in eligible]) for t in TIERS})
        previous = (at, current)
    if result is None:
        raise ValueError("insufficient completed family trajectories")
    return result, {"family_weights": weights, "replayed_reports": replayed,
                    "effective_families": 1 / sum(w * w for w in weights.values())}


def predict_research(panel, state, completed_events, mode="mashiro"):
    """Return point estimates only. Target labels are neither needed nor read."""
    if mode not in ("mashiro", "rui"):
        raise ValueError("research mode must be mashiro or rui")
    tasks = panel.get("tasks", [])
    if len(tasks) != 4 or {int(t["tier"]) for t in tasks} != set(TIERS):
        raise ValueError("research needs exactly four distinct tiers")
    if any(t.get("era") != "voice500_1500" for t in tasks):
        raise ValueError("joint research supports only the new regime")
    if any(int(t.get("event_id", panel["event_id"])) != int(panel["event_id"]) for t in tasks):
        raise ValueError("inconsistent target event")
    metadata = {(int(t["start_at"]), int(t["end_at"]), int(t["issued_at"])) for t in tasks}
    if len(metadata) != 1:
        raise ValueError("inconsistent forecast origin")
    start, end, issue = metadata.pop()
    if not start <= issue < end:
        raise ValueError("forecast origin outside event")
    if int(state["training_cutoff_at"]) >= start or int(panel["event_id"]) in state["completed_event_ids"]:
        raise ValueError("state contains target or future training")
    for task in tasks:
        visible_history(task["history"], issue, int(task.get("input_cutoff_at", issue)))
    prior = pair_prior(completed_events, start, int(panel["event_id"]))
    baseline = predict(panel, state, mode=mode)
    raw = baseline["raw_control"]
    model_diagnostics = {}
    if mode == "rui":
        raw, model_diagnostics = _family_filter(panel, state)
    values, adjustment = reconcile(raw, baseline["current"], prior, (end - issue) / HOUR)
    return {"model_version": MODEL_VERSION, "logic_mode": mode, "event_id": panel["event_id"],
            "issued_at": issue, "end_at": end, "current": baseline["current"],
            "control": values, "baseline_control": baseline["control"], "raw_control": raw,
            "diagnostics": adjustment, "filter_diagnostics": model_diagnostics,
            "prior_event_ids": prior["event_ids"], "uncertainty": "point_forecast_only",
            "research_only": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--completed", type=Path, required=True)
    parser.add_argument("--mode", choices=("mashiro", "rui"), default="mashiro")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    history = json.loads(args.completed.read_text(encoding="utf-8"))
    result = predict_research(json.loads(args.panel.read_text(encoding="utf-8")), read_state(args.state),
                              history["reference_events"], args.mode)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
