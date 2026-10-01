"""Selectable T point forecast with causal reward-category residual pooling.

No probabilistic intervals are supplied. This runtime preserves the fixed
research formula accepted for practical comparison after retrospective review.
"""
from __future__ import annotations
import math
import statistics as st
import bandoribench as B

TIERS = (500, 1000, 1500, 2000)
HORIZONS = (72, 48, 24, 12, 6)
HOUR = 3600000
SCHEMA = "tsukushi-topology-fit-v1"
MODEL_ID = "tsukushi-topology-t"
MODEL_VERSION = "topology-pooling-preregistered-v1"

class IneligiblePanel(ValueError):
    """A common, explicitly reported data-eligibility exclusion."""

def visible_history(points, issue, cutoff):
    """Resolve corrections AS OF this issue, retaining no future information."""
    latest = {}
    revisions = {}
    for row in points:
        stamp = int(row['time'])
        available = int(row.get('available_at', stamp))
        if stamp > min(issue, cutoff) or available > issue:
            continue
        value = float(row['ep'])
        if not math.isfinite(value) or value < 0:
            raise ValueError('invalid visible PT')
        revision_key = (stamp, available)
        if revision_key in revisions and revisions[revision_key] != value:
            raise ValueError('conflicting tracker revision')
        revisions[revision_key] = value
        previous = latest.get(stamp)
        if previous is not None and available == previous[0] and value != previous[1]['ep']:
            raise ValueError('conflicting tracker revision')
        if previous is None or available >= previous[0]:
            latest[stamp] = (available, dict(row, time=stamp, ep=value))
    return [v[1] for _, v in sorted(latest.items())]

def make_panel(event, horizon, require_labels=True, tiers=TIERS):
    """Build the same causal, four-tier origin for every comparator."""
    start, end = int(event['start_at']), int(event['end_at'])
    issue = end - int(horizon) * HOUR
    if issue < start:
        raise IneligiblePanel('origin predates event start')
    tasks = []
    for tier in tiers:
        blob = event.get('tiers', {}).get(str(tier), {})
        if require_labels and (blob.get('label') or {}).get('ep') is None:
            raise IneligiblePanel(f'missing terminal label T{tier}')
        history = visible_history(blob.get('points', []), issue, issue)
        history = [p for p in history if p['time'] >= start]
        if len(history) < 2 or issue - history[-1]['time'] > 3 * HOUR:
            raise IneligiblePanel(f'missing or stale origin T{tier}')
        tasks.append({'case_id': f"joint:{event['event_id']}:{tier}:{horizon}",
                      'event_id': int(event['event_id']), 'server': event.get('server', 'cn'),
                      'tier': tier, 'era': event['era'], 'event_type': event.get('event_type', 'unknown'),
                      'start_at': start, 'end_at': end, 'issued_at': issue,
                      'input_cutoff_at': history[-1]['time'], 'horizon_hours': horizon,
                      'history': history})
    return {'event_id': int(event['event_id']), 'horizon_hours': horizon, 'tasks': tasks}

def growth_scale(prediction, current):
    return max(prediction - current, .01 * current, 1.0)

def bounded_isotonic(means, scales, floors):
    """Exact decreasing weighted least squares with individual lower bounds.

    A constant block minimizes its quadratic at its weighted raw mean,
    bounded below by the maximum individual floor in that block. Pool adjacent
    blocks until those constrained optima are decreasing. Scales are standard
    deviation-like objective geometry, not empirically calibrated uncertainty.
    """
    if not means or len(means) != len(scales) or len(means) != len(floors):
        raise ValueError('inconsistent nonempty vectors')
    if any(not math.isfinite(float(v)) for v in (*means, *scales, *floors)):
        raise ValueError('nonfinite projection input')
    if any(s <= 0 for s in scales):
        raise ValueError('projection scale must be positive')
    reference = min(scales)
    blocks = []
    for i, (mean, scale, floor) in enumerate(zip(means, scales, floors)):
        weight = (reference / scale) ** 2
        if weight <= 0:
            raise ValueError('unsupported projection scale range')
        block = {'start': i, 'stop': i + 1, 'weight': weight,
                 'mean': float(mean), 'floor': float(floor),
                 'value': max(float(mean), float(floor))}
        blocks.append(block)
        while len(blocks) >= 2 and blocks[-2]['value'] < blocks[-1]['value']:
            right, left = blocks.pop(), blocks.pop()
            weight = left['weight'] + right['weight']
            # A convex average avoids overflow from unnormalized weighted sums.
            mean = left['mean'] * (left['weight'] / weight) + right['mean'] * (right['weight'] / weight)
            floor = max(left['floor'], right['floor'])
            blocks.append({'start': left['start'], 'stop': right['stop'],
                           'weight': weight, 'mean': mean, 'floor': floor,
                           'value': max(mean, floor)})
    out = [0.] * len(means)
    for block in blocks:
        out[block['start']:block['stop']] = [block['value']] * (block['stop'] - block['start'])
    if any(not math.isfinite(v) for v in out):
        raise ValueError('nonfinite projection output')
    return out

def grouped_bias(records,groups):
    n=len(records);weight=n/(n+4.0)
    biases={t:0.0 for t in TIERS}
    for group in groups:
        # One scalar per independent donor event, never one sample per rank.
        samples=[st.mean(row['errors'][TIERS.index(t)] for t in group) for row in records]
        value=weight*st.median(samples) if samples else 0.0
        for tier in group:biases[tier]=value
    return biases

def groups_for(tasks):
    roles = {}
    for task in tasks:
        topology = B.reward_topology(task)
        if (task.get("server") != "cn" or task.get("era") != "voice500_1500"
                or not topology["known"] or topology["is_reward_boundary"] is not True):
            raise ValueError("Nanami requires modern CN reward topology")
        roles[int(task["tier"])] = topology["attraction_category"]
    if len(tasks) != 4 or set(roles) != set(TIERS):
        raise ValueError("Nanami requires four unique cutoff tiers")
    groups = tuple(tuple(t for t in TIERS if roles[t] == role)
                   for role in ("modern_higher_attraction", "modern_other_rewarded"))
    if sorted(map(len, groups)) != [2, 2]:
        raise ValueError("unsupported reward attraction categories")
    return groups


def independent_predictions(control, panel):
    """Preserve the evaluated three-primary plus singleton-T1500 experts."""
    tasks = panel["tasks"]
    rows = control.predict_panel(dict(panel, tasks=[t for t in tasks if int(t["tier"]) != 1500]))
    rows += control.predict_panel(dict(panel, tasks=[t for t in tasks if int(t["tier"]) == 1500]))
    by_case = {r["case_id"]: float(r["prediction"]) for r in rows}
    if len(rows) != 4 or len(by_case) != 4 or set(by_case) != {t["case_id"] for t in tasks}:
        raise ValueError("Nanami independent forecast coverage mismatch")
    raw = {int(t["tier"]): by_case[t["case_id"]] for t in tasks}
    if any(not math.isfinite(v) or v < 0 for v in raw.values()):
        raise ValueError("invalid Nanami independent forecast")
    return raw


def event_available_at(event):
    available = int(event["end_at"])
    for blob in event.get("tiers", {}).values():
        for point in blob.get("points", []):
            available = max(available, int(point.get("available_at", point["time"])))
        label = blob.get("label") or {}
        if label.get("ep") is not None:
            stamp = int(label.get("time", event["end_at"]))
            seen = int(label.get("available_at", stamp))
            value = float(label["ep"])
            if stamp < int(event["end_at"]) or seen < stamp or not math.isfinite(value) or value < 0:
                raise ValueError("invalid completed Nanami label")
            available = max(available, seen)
    return available


def collect_residuals(control, event, prior_available_at):
    """Call BEFORE observing the event; retain derived errors, never labels."""
    if event.get("server") != "cn":
        raise ValueError("Nanami training requires CN events")
    available = event_available_at(event)
    if event.get("era") != "voice500_1500":
        return []
    records = []
    for horizon in HORIZONS:
        try:
            panel = make_panel(event, horizon)
        except IneligiblePanel:
            continue
        issue = int(panel["tasks"][0]["issued_at"])
        if prior_available_at > issue:
            raise ValueError("prior Nanami training observations unavailable at historical origin")
        raw = independent_predictions(control, panel)
        truth = [float(event["tiers"][str(t)]["label"]["ep"]) for t in TIERS]
        current = [float(t["history"][-1]["ep"]) for t in panel["tasks"]]
        if any(b > a for a, b in zip(truth, truth[1:])) or any(c > y for c, y in zip(current, truth)):
            raise ValueError("invalid completed Nanami rank surface")
        errors = [(truth[i] - raw[t]) / growth_scale(raw[t], current[i]) for i, t in enumerate(TIERS)]
        records.append({"event_id": int(event["event_id"]), "era": event["era"],
                        "end_at": int(event["end_at"]), "available_at": available,
                        "horizon_hours": horizon, "errors": errors})
    return records


def new_fit():
    return {"schema": SCHEMA, "model_version": MODEL_VERSION,
            "topology_schema": B.REWARD_TOPOLOGY_SCHEMA, "records": [], "available_at": -1}


def validate_fit(fit):
    if (fit.get("schema") != SCHEMA or fit.get("model_version") != MODEL_VERSION
            or fit.get("topology_schema") != B.REWARD_TOPOLOGY_SCHEMA):
        raise ValueError("unsupported Nanami state schema")
    keys = set()
    for row in fit["records"]:
        key = (int(row["event_id"]), int(row["horizon_hours"]))
        if key in keys or key[1] not in HORIZONS or row["era"] != "voice500_1500":
            raise ValueError("invalid or duplicate Nanami residual record")
        keys.add(key)
        if (len(row["errors"]) != 4 or any(not math.isfinite(float(v)) for v in row["errors"])
                or int(row["available_at"]) < int(row["end_at"])
                or int(row["available_at"]) > int(fit["available_at"])):
            raise ValueError("invalid Nanami residual availability or values")
    return fit


def predict_point(panel, control, fit, completed_event_ids, training_cutoff_at):
    validate_fit(fit)
    tasks = sorted(panel["tasks"], key=lambda t: int(t["tier"]))
    groups = groups_for(tasks)
    for key in ("event_id", "issued_at", "start_at", "end_at", "era"):
        if len({t[key] for t in tasks}) != 1:
            raise ValueError("mixed Nanami panel metadata")
    issue, end = int(tasks[0]["issued_at"]), int(tasks[0]["end_at"])
    event_id = int(tasks[0]["event_id"])
    remaining = (end - issue) / HOUR
    if not 0 < remaining <= 72:
        raise ValueError("Nanami 点预测仅支持活动结束前 72 小时内")
    if event_id in set(map(int, completed_event_ids)) or max(int(training_cutoff_at), int(fit["available_at"])) > issue:
        raise ValueError("Nanami state contains target, future, or unavailable training observations")
    clean = []
    for task in tasks:
        history = visible_history(task["history"], issue, int(task.get("input_cutoff_at", issue)))
        if len(history) < 2 or issue - history[-1]["time"] > 3 * HOUR:
            raise ValueError("Nanami needs four fresh cutoff histories (within 3 hours)")
        clean.append(dict(task, history=history, input_cutoff_at=history[-1]["time"]))
    current = [float(t["history"][-1]["ep"]) for t in clean]
    if any(b > a for a, b in zip(current, current[1:])):
        raise ValueError("visible current cutoff ranks are inconsistent")
    horizon = min(HORIZONS, key=lambda h: abs(h - remaining))
    chosen = dict(panel, horizon_hours=horizon,
                  tasks=[dict(t, horizon_hours=horizon) for t in clean])
    raw = independent_predictions(control, chosen)
    eligible = [r for r in fit["records"] if r["horizon_hours"] == horizon
                and r["end_at"] <= issue and r["available_at"] <= issue and r["event_id"] != event_id]
    if not eligible:
        raise ValueError("Nanami 状态尚无该时距的历史校准样本，请使用 Mashiro 或重新导出 Nanami 状态")
    biases = grouped_bias(eligible, groups)
    scales = [growth_scale(raw[t], current[i]) for i, t in enumerate(TIERS)]
    means = [raw[t] + scales[i] * biases[t] for i, t in enumerate(TIERS)]
    values = bounded_isotonic(means, scales, current)
    return dict(zip(TIERS, values)), {
        "model_id": MODEL_ID, "model_version": MODEL_VERSION,
        "sample_count": len(eligible), "source_event_ids": [r["event_id"] for r in eligible],
        "groups": groups, "raw_predictions": raw, "corrected_means": dict(zip(TIERS, means)),
        "growth_scales": dict(zip(TIERS, scales)), "normalized_biases": biases,
        "nearest_evaluated_horizon_hours": horizon,
        "horizon_usage": ("outside_evaluated_range" if remaining < 6 else
                          "exact" if abs(remaining - horizon) < 1e-6 else "nearest_horizon_approximation"),
        "uncertainty": "point_forecast_only"}
