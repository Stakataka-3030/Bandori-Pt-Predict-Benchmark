"""Control-anchored 50-member ensemble from completed same-era trajectories.

The control predictor is unchanged. Member paths are convex combinations of
prequentially measured, completed-event trajectory residuals relative to that
control. With no completed new-era paths, all members coincide with control;
the runner does not invent unsupported extremes.
"""

from __future__ import annotations

import math
import statistics as st
import sys

from bandoribench_model import serve
from calibrated_control import CalibratedControl

N_MEMBERS = 50
HORIZONS = (72, 48, 24, 12, 6)
HOUR = 3600000
PATH_STEPS = 32


def _linear(points, x):
    if x <= points[0][0]:
        return points[0][1]
    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        if x <= xb:
            portion = (x - xa) / (xb - xa) if xb > xa else 0.0
            return ya + portion * (yb - ya)
    return points[-1][1]


def _visible(task):
    issued, cutoff = int(task["issued_at"]), int(task["input_cutoff_at"])
    points = [p for p in task["history"] if int(p["time"]) <= cutoff
              and int(p.get("available_at", p["time"])) <= issued]
    if not points:
        raise ValueError(f"no visible history: {task['case_id']}")
    return sorted(points, key=lambda p: p["time"])


def _weighted_quantile(pairs, quantile):
    ordered = sorted(pairs, key=lambda x: x[0])
    target = quantile * sum(weight for _, weight in ordered)
    tally = 0.0
    for value, weight in ordered:
        tally += weight
        if tally >= target - 1e-12:
            return value
    return ordered[-1][0]


class EmpiricalMemberEnsemble:
    model_id = "tsukushi-aoi"
    model_version = "2026-09-27-control-v2"
    training_cutoff_ms = 0
    supports_online_update = True

    def __init__(self):
        self.point_strategy = "control"
        self.control = CalibratedControl(prior_strength=4,
                                         adjusted_horizons=(12,),
                                         adjusted_tiers=(500, 1500),
                                         min_events=6)
        self.templates = {h: [] for h in HORIZONS}
        self.event_id = None
        self.members = []
        self.last_issue = None
        self.last_snapshot = None

    def initialize(self, context):
        self.control.initialize(context)

    def _control_forecasts(self, panel, tasks):
        """Keep the canonical three-tier control panel exact; T1500 is auxiliary."""
        rows = []
        primary = [t for t in tasks if int(t["tier"]) != 1500]
        auxiliary = [t for t in tasks if int(t["tier"]) == 1500]
        if primary:
            rows.extend(self.control.predict_panel(dict(panel, tasks=primary)))
        for task in auxiliary:
            rows.extend(self.control.predict_panel(dict(panel, tasks=[task])))
        return rows

    def _historical_panel(self, event, horizon):
        end = int(event["end_at"])
        issue = end - horizon * HOUR
        tasks, labels, full = [], {}, {}
        for tier in (500, 1000, 1500, 2000):
            blob = (event.get("tiers") or {}).get(str(tier)) or {}
            label = (blob.get("label") or {}).get("ep")
            if label is None:
                return None
            raw = sorted(blob.get("points") or [], key=lambda p: p["time"])
            before = [p for p in raw if int(p["time"]) <= issue
                      and int(p.get("available_at", p["time"])) <= issue]
            if len(before) < 2:
                return None
            case_id = f"template:{event['event_id']}:{tier}:{horizon}"
            tasks.append({"case_id": case_id, "event_id": event["event_id"],
                          "tier": tier, "era": event["era"],
                          "event_type": event.get("event_type", "unknown"),
                          "start_at": event["start_at"], "end_at": end,
                          "horizon_hours": horizon, "issued_at": issue,
                          "input_cutoff_at": before[-1]["time"],
                          "history": before})
            labels[tier] = float(label)
            full[tier] = raw
        return tasks, labels, full

    def observe_event(self, event):
        # The harness calls this only after the event has ended. Extract its
        # out-of-sample trajectory BEFORE the control learns this event.
        if event.get("era") == "voice500_1500":
            for horizon in HORIZONS:
                prepared = self._historical_panel(event, horizon)
                if prepared is None:
                    continue
                tasks, labels, full = prepared
                forecast = {r["case_id"]: float(r["prediction"])
                            for r in self._control_forecasts({
                                "event_id": event["event_id"],
                                "horizon_hours": horizon}, tasks)}
                paths = {}
                for task in tasks:
                    tier = task["tier"]
                    origin = float(task["history"][-1]["ep"])
                    denominator = forecast[task["case_id"]] - origin
                    if denominator <= max(1000.0, 0.01 * origin) or labels[tier] < origin:
                        break
                    issue, end = int(task["issued_at"]), int(task["end_at"])
                    series = [(0.0, 0.0)]
                    for point in full[tier]:
                        at = int(point["time"])
                        if issue < at < end:
                            u = (at - issue) / (end - issue)
                            z = (float(point["ep"]) - origin) / denominator
                            series.append((u, max(0.0, z)))
                    series.append((1.0, (labels[tier] - origin) / denominator))
                    series.sort(key=lambda p: p[0])
                    if any(a[1] > b[1] + 1e-9 for a, b in zip(series, series[1:])):
                        break
                    paths[str(tier)] = series
                if len(paths) == 4:
                    self.templates[horizon].append({"event_id": int(event["event_id"]),
                                                     "paths": paths})
        self.control.observe_event(event)
        if self.event_id == event["event_id"]:
            self.event_id, self.members, self.last_issue, self.last_snapshot = None, [], None, None

    def _make_members(self, horizon):
        library = self.templates[horizon]
        n = len(library)
        members = []
        for i in range(N_MEMBERS):
            if n == 0:
                a = b = None
                alpha = 1.0
                sources = []
            elif i < n:
                a = b = i
                alpha = 1.0
                sources = [library[i]["event_id"]]
            else:
                # A deterministic convex blend of two whole-event paths.
                # It can interpolate between observed scenarios, never invent
                # a terminal or trajectory outside their pointwise envelope.
                k = i - n
                a = k % n
                b = (k * 5 + 1) % n
                alpha = 0.72 + 0.27 * ((k * 17) % 37) / 36
                sources = sorted({library[a]["event_id"], library[b]["event_id"]})
            members.append({"member_id": f"m{i:02d}", "template_a": a,
                            "template_b": b, "alpha": alpha,
                            "event_a": library[a]["event_id"] if a is not None else None,
                            "event_b": library[b]["event_id"] if b is not None else None,
                            "source_event_ids": sources, "weight": 1 / N_MEMBERS})
        return members

    def _member_ratio(self, member, horizon, tier, u):
        if member["event_a"] is None:
            return self._control_shape(horizon, tier, u)
        library = {t["event_id"]: t for t in self.templates[horizon]}
        a = library.get(member["event_a"])
        b = library.get(member["event_b"])
        if a is None or b is None:
            return self._control_shape(horizon, tier, u)
        first = _linear(a["paths"][str(tier)], u)
        second = _linear(b["paths"][str(tier)], u)
        return member["alpha"] * first + (1 - member["alpha"]) * second

    def _control_shape(self, horizon, tier, u):
        library = self.templates[horizon]
        if not library:
            return u
        shapes = []
        for template in library:
            path = template["paths"][str(tier)]
            terminal = path[-1][1]
            if terminal > 0:
                shapes.append(_linear(path, u) / terminal)
        return st.median(shapes) if shapes else u

    def predict_panel(self, panel):
        event_id = int(panel["event_id"])
        if self.event_id != event_id:
            self.event_id, self.members, self.last_issue, self.last_snapshot = event_id, [], None, None
        tasks = panel["tasks"]
        if not tasks:
            return []
        issue = int(tasks[0]["issued_at"])
        end = int(tasks[0]["end_at"])
        if any(int(t["issued_at"]) != issue or int(t["end_at"]) != end for t in tasks):
            raise ValueError("mixed issue/end time in panel")
        if self.last_issue is not None and issue <= self.last_issue:
            raise ValueError("member panels must advance chronologically")
        horizon = min(HORIZONS, key=lambda h: abs(h - float(panel["horizon_hours"])))
        visible = [_visible(t) for t in tasks]
        safe_tasks = [dict(t, history=points, input_cutoff_at=points[-1]["time"])
                      for t, points in zip(tasks, visible)]
        current = {int(t["tier"]): float(points[-1]["ep"])
                   for t, points in zip(tasks, visible)}
        control_rows = self._control_forecasts(panel, safe_tasks)
        control_by_case = {r["case_id"]: float(r["prediction"]) for r in control_rows}
        control = {int(t["tier"]): max(current[int(t["tier"])], control_by_case[t["case_id"]])
                   for t in tasks}
        if len(control) != len(tasks):
            raise ValueError("duplicate tier")

        diagnostics = {"assimilated": bool(self.members), "prior_ess": None,
                       "soft_pruned": 0, "template_event_ids":
                       [t["event_id"] for t in self.templates[horizon]],
                       "template_count": len(self.templates[horizon])}
        if not self.members:
            self.members = self._make_members(horizon)
        else:
            log_weights = []
            for member in self.members:
                penalties = []
                for tier, actual in current.items():
                    previous = member["paths"][str(tier)]
                    expected = _linear(previous, issue)
                    sigma = max(1000.0, 0.025 * actual)
                    z = (actual - expected) / sigma
                    penalties.append(2.0 * math.log1p(z * z / 3.0))
                log_weights.append(math.log(max(member["weight"], 1e-12))
                                   - st.mean(penalties))
            peak = max(log_weights)
            exp_weights = [math.exp(v - peak) for v in log_weights]
            total = sum(exp_weights)
            for member, value in zip(self.members, exp_weights):
                member["weight"] = 0.98 * value / total + 0.02 / N_MEMBERS
            diagnostics["prior_ess"] = 1.0 / sum(m["weight"] ** 2 for m in self.members)
            diagnostics["soft_pruned"] = sum(m["weight"] < 0.2 / N_MEMBERS
                                             for m in self.members)

        # Re-anchor every surviving scenario to both the new actual observation
        # and the updated deterministic control. No member's old terminal is
        # copied forward as if the latest control information did not exist.
        for member in self.members:
            paths, terminals = {}, {}
            for tier, actual in current.items():
                delta = control[tier] - actual
                series = []
                for step in range(PATH_STEPS + 1):
                    u = step / PATH_STEPS
                    value = actual + delta * self._member_ratio(member, horizon, tier, u)
                    series.append([issue + (end - issue) * u, max(actual, value)])
                paths[str(tier)] = series
                terminals[str(tier)] = series[-1][1]
            member["paths"] = paths
            member["terminals"] = terminals
        control_paths = {}
        for tier, actual in current.items():
            control_paths[str(tier)] = [
                [issue + (end - issue) * step / PATH_STEPS,
                 actual + (control[tier] - actual) * self._control_shape(horizon, tier, step / PATH_STEPS)]
                for step in range(PATH_STEPS + 1)]
        quantiles = {}
        for q, name in ((.1, "p10"), (.5, "median"), (.9, "p90")):
            quantiles[name] = {tier: _weighted_quantile(
                [(m["terminals"][str(tier)], m["weight"]) for m in self.members], q)
                for tier in current}
        if abs(sum(m["weight"] for m in self.members) - 1) > 1e-10:
            raise AssertionError("member weights do not sum to one")
        self.last_issue = issue
        self.last_snapshot = {
            "event_id": event_id, "issued_at": issue, "end_at": end,
            "horizon_hours": panel["horizon_hours"], "current": current,
            "control": control, "control_paths": control_paths,
            "member_p10": quantiles["p10"],
            "member_median": quantiles["median"],
            "member_p90": quantiles["p90"],
            "members": [{k: (v.copy() if isinstance(v, dict) else v)
                         for k, v in m.items()} for m in self.members],
            "diagnostics": diagnostics,
        }
        return [{"case_id": t["case_id"], "prediction": control[int(t["tier"])]}
                for t in tasks]

    def finalize(self):
        self.control.finalize()


if __name__ == "__main__":
    serve(EmpiricalMemberEnsemble())
