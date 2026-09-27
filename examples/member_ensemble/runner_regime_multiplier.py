"""Official model-eval runner for `regime-multiplier-v1`.

Speaks the bandoribench-model-api-v1 JSONL protocol on stdin/stdout.  It receives only
`forecast_panel` messages containing the current event's visible prefix, and
`observe_event` messages for events that have already ENDED.  No current or future
label is ever supplied to the model, and the model never reads benchmark files.

The model rebuilds its multiplier pool purely from the `observe_event` stream, so its
causal boundary is enforced by the harness, not by convention.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "04_final"))
sys.path.insert(0, ROOT)

from bandoribench_model import serve  # noqa: E402
import model_regime_multiplier as M  # noqa: E402


class RegimeMultiplierRunner:
    model_id = "regime-multiplier-v1"
    model_version = "2026-09-26"
    training_cutoff_ms = 0
    supports_online_update = True

    def initialize(self, context):
        self.completed: list[dict] = []
        self.seen: set[int] = set()

    def observe_event(self, event):
        """Called by the harness ONLY after this event's forecasts are complete."""
        ev = int(event["event_id"])
        if ev in self.seen:
            return
        self.seen.add(ev)
        tiers = {}
        for tier_s, payload in (event.get("tiers") or {}).items():
            label = payload.get("label") or {}
            final = label.get("ep")
            if final is None:
                continue
            pts = [(int(p["time"]), float(p["ep"])) for p in (payload.get("points") or [])]
            tiers[int(tier_s)] = {"points": pts, "final": float(final)}
        self.completed.append({
            "event_id": ev,
            "era": event.get("era"),
            "start_at": int(event.get("start_at") or 0),
            "end_at": int(event.get("end_at") or 0),
            "tiers": tiers,
        })

    def _visible_completed(self, issued_at):
        return [r for r in self.completed if r["end_at"] <= issued_at]

    def predict_panel(self, panel):
        rows = []
        for task in panel["tasks"]:
            cutoff = task.get("input_cutoff_at")
            pts = [p for p in task["history"] if p["time"] <= cutoff]
            if pts:
                cur = float(max(pts, key=lambda p: p["time"])["ep"])
            elif task["history"]:
                cur = float(task["history"][-1]["ep"])
            else:
                cur = 0.0
            pred = M.predict_from_parts(
                task["tier"], task["horizon_hours"], task["era"], cur,
                self._visible_completed(int(task["issued_at"])),
            )
            rows.append({"case_id": task["case_id"], "prediction": float(pred)})
        return rows

    def finalize(self):
        sys.stderr.write(f"[regime-multiplier-v1] completed events observed: "
                         f"{len(self.completed)}\n")


if __name__ == "__main__":
    serve(RegimeMultiplierRunner())
