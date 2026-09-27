"""Trusted local replay that exports causal 50-member forecast snapshots.

The public benchmark bundle contains labeled reference events. Do not ship it
to competitors. This script streams only completed earlier events into the
model and omits target-event truth from its output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from member_ensemble import EmpiricalMemberEnsemble


def replay(bundle_path: Path, event_id: int, output: Path) -> None:
    path = bundle_path / "public" / "tasks.json" if bundle_path.is_dir() else bundle_path
    bundle = json.loads(path.read_text(encoding="utf-8"))
    tasks = [task for task in bundle["tasks"] if int(task["event_id"]) == event_id]
    if not tasks:
        raise ValueError(f"event {event_id} has no tasks in {path}")
    first_issue = min(int(task["issued_at"]) for task in tasks)
    earlier = sorted((event for event in bundle["reference_events"]
                      if int(event["end_at"]) <= first_issue and int(event["event_id"]) != event_id),
                     key=lambda event: (event["end_at"], event["event_id"]))
    target = next((event for event in bundle["reference_events"]
                   if int(event["event_id"]) == event_id), None)
    model = EmpiricalMemberEnsemble()
    model.initialize({})
    for event in earlier:
        model.observe_event(event)
    snapshots = []
    for horizon in sorted({int(task["horizon_hours"]) for task in tasks}, reverse=True):
        group = sorted((task for task in tasks if int(task["horizon_hours"]) == horizon),
                       key=lambda task: task["tier"])
        if target is not None and "1500" in target.get("tiers", {}):
            issue = int(group[0]["issued_at"])
            history = [p for p in target["tiers"]["1500"]["points"]
                       if int(p["time"]) <= issue
                       and int(p.get("available_at", p["time"])) <= issue]
            if len(history) >= 2:
                group = sorted([*group, dict(group[0],
                                case_id=f"cn:{event_id}:1500:{horizon}",
                                tier=1500, input_cutoff_at=history[-1]["time"],
                                history=history)], key=lambda task: task["tier"])
        model.predict_panel({"panel_id": f"{event_id}:{horizon}",
                             "event_id": event_id, "horizon_hours": horizon,
                             "tasks": group})
        snapshot = model.last_snapshot
        snapshot["visible_history"] = {
            str(task["tier"]): [point for point in task["history"]
                                if int(point["time"]) <= int(task["input_cutoff_at"])
                                and int(point.get("available_at", point["time"])) <= int(task["issued_at"])]
            for task in group
        }
        snapshots.append(snapshot)
    payload = {"event_id": event_id, "model_id": model.model_id,
               "point_strategy": model.point_strategy,
               "prior_completed_event_ids": [event["event_id"] for event in earlier],
               "snapshots": snapshots}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay 50-member forecasts from a local benchmark")
    parser.add_argument("bundle", type=Path, help="benchmark directory or public/tasks.json")
    parser.add_argument("event_id", type=int)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    replay(args.bundle, args.event_id, args.out)
