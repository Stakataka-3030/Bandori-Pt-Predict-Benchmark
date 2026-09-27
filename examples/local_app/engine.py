"""Local Tsukushi inference from a compact derived state package."""

from __future__ import annotations

import hashlib
import json
import math
import statistics as st
import sys
from pathlib import Path

HERE = (Path(sys._MEIPASS) / "examples" / "local_app"
        if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
MODEL_DIR = HERE.parent / "member_ensemble"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))
from member_ensemble import EmpiricalMemberEnsemble, HORIZONS  # noqa: E402
from ensemble import TAIL  # noqa: E402

TIERS = (500, 1000, 1500, 2000)
HOUR = 3600000


def _linear(points, x):
    if x < points[0][0]:
        return None
    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        if x <= xb:
            return ya + (yb - ya) * (x - xa) / (xb - xa) if xb > xa else yb
    return points[-1][1]


def _quantile(pairs, q):
    ordered = sorted(pairs)
    cutoff = q * sum(weight for _, weight in ordered)
    tally = 0.0
    for value, weight in ordered:
        tally += weight
        if tally >= cutoff - 1e-12:
            return value
    return ordered[-1][0]


def read_state(path: Path):
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != "tsukushi-local-state-v1":
        raise ValueError("unsupported model state schema")
    fit = state["fit"]
    actual = hashlib.sha256(json.dumps(fit, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if actual != state["fit_sha256"]:
        raise ValueError("model state hash mismatch")
    if state["model_ids"] != {"control": "tsukushi-kaori", "members": "tsukushi-aoi"}:
        raise ValueError("model identity mismatch")
    return state


class LocalKaori:
    model_id = "tsukushi-kaori"

    def __init__(self, state):
        self.fit = state["fit"]
        self.tail = TAIL.Champion()
        self.tail.pool = {int(h): list(values)
                          for h, values in self.fit["tail_pool"].items()}

    def initialize(self, context):
        pass

    def predict_panel(self, panel):
        first = {r["case_id"]: float(r["prediction"])
                 for r in self.tail.predict_panel(panel)}
        horizon = int(panel["horizon_hours"])
        out = []
        for task in panel["tasks"]:
            tier = int(task["tier"])
            current = float(task["history"][-1]["ep"])
            era = task["era"]
            values = self.fit["multiplier_samples"].get(f"{era}:{tier}:{horizon}") or []
            if not values:
                values = self.fit["multiplier_samples"].get(f"voice1000:{tier}:{horizon}") or []
            multiplier = st.median(values) if values else 1.0
            boundary_factor = .92 if era == "voice500_1500" and tier == 1000 else 1.0
            second = max(0.0, current * multiplier * boundary_factor)
            raw = .5 * (first[task["case_id"]] + second)
            ratio = self.fit["kaori_ratios"].get(f"{tier}:{horizon}") or []
            if era == "voice500_1500" and tier in (500, 1500) and horizon == 12 and len(ratio) >= 6:
                weight = len(ratio) / (len(ratio) + 4.0)
                raw = current + (raw - current) * (1 + weight * (st.median(ratio) - 1))
            out.append({"case_id": task["case_id"], "prediction": max(current, raw)})
        return out


def _early_snapshot(panel, state):
    tasks = panel["tasks"]
    issue = int(tasks[0]["issued_at"])
    start, end = int(tasks[0]["start_at"]), int(tasks[0]["end_at"])
    progress = (issue - start) / (end - start)
    current = {int(task["tier"]): float(task["history"][-1]["ep"])
               for task in tasks}
    eligible = []
    for family in state["fit"]["early_progress_paths"]:
        at_origin = {}
        for tier in TIERS:
            value = _linear(family["paths"][str(tier)], progress)
            if value is None or value <= 0:
                break
            at_origin[tier] = value
        if len(at_origin) == len(TIERS):
            eligible.append((family, at_origin))
    if len(eligible) < 3:
        raise ValueError("早期历史样本不足，无法生成四档预测")
    trajectories = []
    for family, at_origin in eligible:
        paths = {}
        for tier in TIERS:
            now = current[tier]
            points = []
            for step in range(33):
                u = progress + (1 - progress) * step / 32
                fraction = _linear(family["paths"][str(tier)], u)
                value = now * fraction / at_origin[tier]
                points.append([issue + (end - issue) * step / 32, max(now, value)])
            paths[str(tier)] = points
        trajectories.append((family["event_id"], paths))
    control_paths = {}
    control = {}
    for tier in TIERS:
        key = str(tier)
        control_paths[key] = []
        for step in range(33):
            stamp = issue + (end - issue) * step / 32
            value = st.median(paths[key][step][1] for _, paths in trajectories)
            control_paths[key].append([stamp, value])
        control[tier] = control_paths[key][-1][1]
    members = []
    count = len(trajectories)
    for i in range(50):
        if i < count:
            a = b = i
            alpha = 1.0
        else:
            k = i - count
            a = k % count
            b = (k * 5 + 1) % count
            alpha = .72 + .27 * ((k * 17) % 37) / 36
        paths = {}
        terminals = {}
        for tier in TIERS:
            key = str(tier)
            paths[key] = [[trajectories[a][1][key][step][0],
                           alpha * trajectories[a][1][key][step][1]
                           + (1 - alpha) * trajectories[b][1][key][step][1]]
                          for step in range(33)]
            terminals[key] = paths[key][-1][1]
        members.append({"member_id": f"m{i:02d}",
                        "source_event_ids": sorted({trajectories[a][0], trajectories[b][0]}),
                        "weight": 1 / 50, "paths": paths, "terminals": terminals})
    def quantile(q):
        return {tier: _quantile([(m["terminals"][str(tier)], m["weight"])
                                for m in members], q) for tier in TIERS}
    return {"event_id": int(panel["event_id"]), "issued_at": issue,
            "end_at": end, "horizon_hours": (end - issue) / HOUR,
            "current": current, "control": control,
            "control_paths": control_paths,
            "member_p10": quantile(.1), "member_median": quantile(.5),
            "member_p90": quantile(.9), "members": members,
            "diagnostics": {"assimilated": False, "prior_ess": None,
                            "soft_pruned": 0, "template_count": count,
                            "template_event_ids": [e for e, _ in trajectories]},
            "forecast_mode": "early_progress"}


def predict(panel, state):
    remaining = (int(panel["tasks"][0]["end_at"])
                 - int(panel["tasks"][0]["issued_at"])) / HOUR
    if remaining <= 0:
        raise ValueError("活动已经结束")
    if any(int(t["tier"]) not in TIERS for t in panel["tasks"]):
        raise ValueError("unsupported tier")
    if remaining > 72:
        snapshot = _early_snapshot(panel, state)
    else:
        nearest = min(HORIZONS, key=lambda h: abs(h - remaining))
        chosen = dict(panel, horizon_hours=nearest)
        chosen["tasks"] = [dict(t, horizon_hours=nearest) for t in panel["tasks"]]
        model = EmpiricalMemberEnsemble()
        model.control = LocalKaori(state)
        model.templates = {int(h): templates for h, templates in state["fit"]["aoi_templates"].items()}
        model.initialize({})
        model.predict_panel(chosen)
        snapshot = model.last_snapshot
        snapshot["horizon_hours"] = remaining
        snapshot["forecast_mode"] = "evaluated_horizon_family"
        snapshot["nearest_evaluated_horizon_hours"] = nearest
    snapshot["visible_history"] = {str(t["tier"]): list(t["history"])
                                   for t in panel["tasks"]}
    return snapshot
