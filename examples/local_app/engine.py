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
from output_order import constrain_snapshot  # noqa: E402

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


def one_hour_projection(history, issued_at, end_at):
    """Project the latest visible PT using its exact preceding hour of growth."""
    visible = sorted((p for p in history
                      if int(p["time"]) <= issued_at
                      and int(p.get("available_at", p["time"])) <= issued_at),
                     key=lambda p: p["time"])
    if len(visible) < 2:
        return None
    latest = visible[-1]
    last_time = int(latest["time"])
    if last_time >= end_at or issued_at - last_time > HOUR:
        return None
    boundary = last_time - HOUR
    before = next((p for p in reversed(visible) if int(p["time"]) <= boundary), None)
    after = next((p for p in visible if int(p["time"]) >= boundary), None)
    if before is None or after is None:
        return None
    ta, tb = int(before["time"]), int(after["time"])
    value = (float(before["ep"]) if ta == tb else
             float(before["ep"]) + (float(after["ep"]) - float(before["ep"]))
             * (boundary - ta) / (tb - ta))
    current = float(latest["ep"])
    if current < value:
        return None
    growth_per_hour = current - value
    terminal = current + growth_per_hour * (end_at - last_time) / HOUR
    return {"terminal": terminal, "growth_per_hour": growth_per_hour,
            "basis_from": boundary, "basis_to": last_time,
            "path": [[last_time, current], [end_at, terminal]]}


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


def _rui_snapshot(panel, state):
    """Replay visible reports and update one fixed set of member identities."""
    tasks = panel["tasks"]
    issue = int(tasks[0]["issued_at"])
    start, end = int(tasks[0]["start_at"]), int(tasks[0]["end_at"])
    first = start + 3 * HOUR
    if issue < first:
        raise ValueError("活动开场未满 3 小时，暂不起报")
    checkpoints = list(range(first, issue, 3 * HOUR))
    checkpoints.extend(range(max(first, end - 24 * HOUR), issue, HOUR))
    checkpoints.append(issue)
    checkpoints = sorted(set(checkpoints))
    previous = None
    fixed_state = None
    replayed = 0
    for at in checkpoints:
        visible_tasks = []
        for task in tasks:
            points = [p for p in task["history"]
                      if int(p["time"]) <= at
                      and int(p.get("available_at", p["time"])) <= at]
            if not points:
                break
            visible_tasks.append(dict(task, history=points, issued_at=at,
                                      input_cutoff_at=points[-1]["time"]))
        if len(visible_tasks) != len(tasks):
            continue
        visible_panel = dict(panel, tasks=visible_tasks)
        if fixed_state is None:
            progress = (at - start) / (end - start)
            families = [family for family in state["fit"]["early_progress_paths"]
                        if all((_linear(family["paths"][str(tier)], progress) or 0) > 0
                               for tier in TIERS)]
            if len(families) < 3:
                continue
            fixed_state = dict(state, fit=dict(state["fit"],
                                             early_progress_paths=families))
        snapshot = _early_snapshot(visible_panel, fixed_state)
        if previous is not None:
            log_weights = []
            for member in previous["members"]:
                penalties = []
                for tier in TIERS:
                    actual = snapshot["current"][tier]
                    expected = _linear(member["paths"][str(tier)], at)
                    sigma = max(1000.0, 0.025 * actual)
                    z = (actual - expected) / sigma
                    penalties.append(2.0 * math.log1p(z * z / 3.0))
                log_weights.append(math.log(max(member["weight"], 1e-12))
                                   - st.mean(penalties))
            peak = max(log_weights)
            weights = [math.exp(value - peak) for value in log_weights]
            total = sum(weights)
            for old, new, value in zip(previous["members"], snapshot["members"], weights):
                if old["member_id"] != new["member_id"] or old["source_event_ids"] != new["source_event_ids"]:
                    raise ValueError("Rui member identity changed during replay")
                new["weight"] = 0.98 * value / total + 0.02 / len(weights)
            replayed += 1
        members = snapshot["members"]
        for q, field in ((.1, "member_p10"), (.5, "member_median"),
                         (.9, "member_p90")):
            snapshot[field] = {tier: _quantile(
                [(m["terminals"][str(tier)], m["weight"]) for m in members], q)
                for tier in TIERS}
        snapshot["control"] = dict(snapshot["member_median"])
        snapshot["control_paths"] = {
            str(tier): [[members[0]["paths"][str(tier)][step][0],
                         _quantile([(m["paths"][str(tier)][step][1], m["weight"])
                                    for m in members], .5)]
                        for step in range(33)] for tier in TIERS}
        ess = 1.0 / sum(m["weight"] ** 2 for m in members)
        snapshot["diagnostics"].update(
            assimilated=replayed > 0, prior_ess=ess,
            soft_pruned=sum(m["weight"] < 0.2 / len(members) for m in members),
            replayed_reports=replayed)
        previous = snapshot
    if previous is None:
        raise ValueError("早期历史样本不足，Rui 暂无法起报")
    previous["forecast_mode"] = "sequential_pruning"
    return previous


def predict(panel, state, mode="mashiro"):
    if mode not in ("mashiro", "rui"):
        raise ValueError("unknown forecast mode")
    remaining = (int(panel["tasks"][0]["end_at"])
                 - int(panel["tasks"][0]["issued_at"])) / HOUR
    if remaining <= 0:
        raise ValueError("活动已经结束")
    if any(int(t["tier"]) not in TIERS for t in panel["tasks"]):
        raise ValueError("unsupported tier")
    if mode == "rui":
        snapshot = _rui_snapshot(panel, state)
    elif remaining > 72:
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
    snapshot["visible_history"] = {
        str(t["tier"]): [p for p in t["history"]
                          if int(p["time"]) <= int(t["issued_at"])
                          and int(p.get("available_at", p["time"])) <= int(t["issued_at"])]
        for t in panel["tasks"]}
    snapshot["logic_mode"] = "Rui" if mode == "rui" else "Mashiro"
    snapshot["linear1h"] = {}
    snapshot["linear1h_paths"] = {}
    snapshot["linear1h_details"] = {}
    if remaining <= 24:
        issue = int(panel["tasks"][0]["issued_at"])
        end = int(panel["tasks"][0]["end_at"])
        for task in panel["tasks"]:
            projection = one_hour_projection(task["history"], issue, end)
            if projection is not None:
                tier = int(task["tier"])
                snapshot["linear1h"][tier] = projection["terminal"]
                snapshot["linear1h_paths"][tier] = projection["path"]
                snapshot["linear1h_details"][tier] = {
                    key: projection[key] for key in ("growth_per_hour", "basis_from", "basis_to")}
    return constrain_snapshot(snapshot)
