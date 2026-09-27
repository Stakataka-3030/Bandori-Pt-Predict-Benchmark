"""Research-only prequential new-regime correction for the frozen point control.

Only completed new-regime events contribute residual ratios. The original
control remains accessible and event 324's published forecast is untouched.
"""

from __future__ import annotations

import statistics as st
import sys

from bandoribench_model import serve
from ensemble import FixedEqualNewEra

HORIZONS = (72, 48, 24, 12, 6)
HOUR = 3600000


class CalibratedControl:
    model_id = "tsukushi-ctrl-v2-research"
    model_version = "2026-09-27-prequential-1"
    training_cutoff_ms = 0
    supports_online_update = True

    def __init__(self, prior_strength=4.0, adjusted_horizons=(24, 12, 6),
                 adjusted_tiers=(500, 1500), min_events=0):
        self.base = FixedEqualNewEra()
        self.prior_strength = float(prior_strength)
        self.adjusted_horizons = tuple(adjusted_horizons)
        self.adjusted_tiers = tuple(adjusted_tiers)
        self.min_events = int(min_events)
        self.ratios = {(tier, horizon): [] for tier in adjusted_tiers for horizon in HORIZONS}

    def initialize(self, context):
        self.base.initialize(context)

    def _base_panel(self, panel, tasks):
        primary = [task for task in tasks if int(task["tier"]) != 1500]
        auxiliary = [task for task in tasks if int(task["tier"]) == 1500]
        rows = self.base.predict_panel(dict(panel, tasks=primary)) if primary else []
        for task in auxiliary:
            rows.extend(self.base.predict_panel(dict(panel, tasks=[task])))
        return rows

    def observe_event(self, event):
        if event.get("era") == "voice500_1500":
            for horizon in HORIZONS:
                issue = int(event["end_at"]) - horizon * HOUR
                tasks, labels = [], {}
                for tier in (500, 1000, 1500, 2000):
                    blob = (event.get("tiers") or {}).get(str(tier)) or {}
                    final = (blob.get("label") or {}).get("ep")
                    if final is None:
                        continue
                    history = sorted((p for p in blob.get("points", [])
                                      if int(p["time"]) <= issue
                                      and int(p.get("available_at", p["time"])) <= issue),
                                     key=lambda p: p["time"])
                    if len(history) < 2:
                        continue
                    case_id = f"adapt:{event['event_id']}:{tier}:{horizon}"
                    tasks.append({"case_id": case_id, "event_id": event["event_id"],
                                  "tier": tier, "era": event["era"],
                                  "event_type": event.get("event_type", "unknown"),
                                  "start_at": event["start_at"], "end_at": event["end_at"],
                                  "horizon_hours": horizon, "issued_at": issue,
                                  "input_cutoff_at": history[-1]["time"],
                                  "history": history})
                    if tier in self.adjusted_tiers:
                        labels[case_id] = float(final)
                if tasks:
                    rows = self._base_panel({"event_id": event["event_id"],
                                             "horizon_hours": horizon}, tasks)
                    predictions = {r["case_id"]: float(r["prediction"]) for r in rows}
                    for task in tasks:
                        if task["tier"] not in self.adjusted_tiers:
                            continue
                        current = float(task["history"][-1]["ep"])
                        remaining = predictions[task["case_id"]] - current
                        final = labels[task["case_id"]]
                        if remaining > max(1000.0, 0.01 * current) and final >= current:
                            self.ratios[(task["tier"], horizon)].append(
                                (final - current) / remaining)
        self.base.observe_event(event)

    def predict_panel(self, panel):
        tasks = panel["tasks"]
        safe = []
        for task in tasks:
            issued = int(task["issued_at"])
            visible = sorted((p for p in task["history"]
                              if int(p["time"]) <= int(task["input_cutoff_at"])
                              and int(p.get("available_at", p["time"])) <= issued),
                             key=lambda p: p["time"])
            if not visible:
                raise ValueError(f"no visible tracker: {task['case_id']}")
            safe.append(dict(task, history=visible, input_cutoff_at=visible[-1]["time"]))
        raw = self._base_panel(panel, safe)
        by_case = {r["case_id"]: float(r["prediction"]) for r in raw}
        horizon = min(HORIZONS, key=lambda h: abs(h - float(panel["horizon_hours"])))
        out = []
        for task in safe:
            raw_value = by_case[task["case_id"]]
            current = float(task["history"][-1]["ep"])
            tier = int(task["tier"])
            correction = 1.0
            if (task.get("era") == "voice500_1500" and tier in self.adjusted_tiers
                    and horizon in self.adjusted_horizons):
                prior = self.ratios[(tier, horizon)]
                if len(prior) >= self.min_events and prior:
                    weight = len(prior) / (len(prior) + self.prior_strength)
                    correction = 1.0 + weight * (st.median(prior) - 1.0)
            prediction = max(current, current + (raw_value - current) * correction)
            out.append({"case_id": task["case_id"], "prediction": prediction})
        return out

    def finalize(self):
        self.base.finalize()


if __name__ == "__main__":
    prior = float(sys.argv[sys.argv.index("--prior") + 1]) if "--prior" in sys.argv else 4.0
    horizons = tuple(int(v) for v in sys.argv[sys.argv.index("--horizons") + 1].split(",")) if "--horizons" in sys.argv else (24, 12, 6)
    min_events = int(sys.argv[sys.argv.index("--min-events") + 1]) if "--min-events" in sys.argv else 0
    serve(CalibratedControl(prior_strength=prior, adjusted_horizons=horizons,
                            min_events=min_events))
