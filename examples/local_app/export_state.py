"""Export a compact, derived Tsukushi checkpoint from a trusted local bundle.

The output contains fitted ratios and normalized trajectories only. It never
contains raw third-party tracker archives or absolute event-final labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE.parent / "member_ensemble"
sys.path.insert(0, str(MODEL_DIR))
from member_ensemble import EmpiricalMemberEnsemble, HORIZONS  # noqa: E402
from model_regime_multiplier import _samples  # noqa: E402

TIERS = (500, 1000, 1500, 2000)


def export(bundle_path: Path, output: Path) -> dict:
    source = bundle_path / "private" / "benchmark.json" if bundle_path.is_dir() else bundle_path
    raw = source.read_bytes()
    bundle = json.loads(raw)
    events = sorted(bundle["reference_events"], key=lambda e: (e["end_at"], e["event_id"]))
    if not events:
        raise ValueError("reference event history is empty")
    model = EmpiricalMemberEnsemble()
    model.initialize({})
    for event in events:
        model.observe_event(event)
    kaori = model.control
    completed = kaori.base.multiplier.completed
    multiplier_samples = {}
    for era in ("voice1000", "voice500_1500"):
        records = [rec for rec in completed if rec["era"] == era]
        for tier in TIERS:
            for horizon in HORIZONS:
                multiplier_samples[f"{era}:{tier}:{horizon}"] = _samples(records, tier, horizon)
    early_paths = []
    for event in events:
        if event["era"] != "voice500_1500":
            continue
        duration = int(event["end_at"]) - int(event["start_at"])
        if duration <= 0:
            continue
        paths = {}
        for tier in TIERS:
            blob = (event.get("tiers") or {}).get(str(tier)) or {}
            final = (blob.get("label") or {}).get("ep")
            if final is None or float(final) <= 0:
                continue
            by_time = {int(p["time"]): float(p["ep"]) for p in blob.get("points", [])
                       if int(event["start_at"]) <= int(p["time"]) <= int(event["end_at"])}
            points = [[(t - event["start_at"]) / duration, value / float(final)]
                      for t, value in sorted(by_time.items())]
            points.append([1.0, 1.0])
            points.sort()
            if len(points) >= 2 and all(a[1] <= b[1] + 1e-9
                                        for a, b in zip(points, points[1:])):
                paths[str(tier)] = points
        if len(paths) == len(TIERS):
            early_paths.append({"event_id": int(event["event_id"]),
                                "event_type": event["event_type"], "paths": paths})
    fit = {
        "tail_pool": {str(h): values for h, values in kaori.base.tail.pool.items()},
        "multiplier_samples": multiplier_samples,
        "kaori_ratios": {f"{tier}:{horizon}": values
                         for (tier, horizon), values in kaori.ratios.items()},
        "aoi_templates": {str(h): templates for h, templates in model.templates.items()},
        "early_progress_paths": early_paths,
    }
    checkpoint = {
        "schema": "tsukushi-local-state-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_ids": {"control": "tsukushi-kaori", "members": "tsukushi-aoi"},
        "horizons_hours": list(HORIZONS), "tiers": list(TIERS),
        "completed_event_ids": [int(event["event_id"]) for event in events],
        "training_cutoff_at": max(int(event["end_at"]) for event in events),
        "new_era_first_observed_start_at": min(int(event["start_at"]) for event in events
                                               if event["era"] == "voice500_1500"),
        "source_bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "fit": fit,
    }
    checkpoint["fit_sha256"] = hashlib.sha256(json.dumps(fit, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(checkpoint, ensure_ascii=False,
                                 separators=(",", ":")) + "\n", encoding="utf-8")
    return checkpoint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a derived local prediction state")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    state = export(args.bundle, args.out)
    print(json.dumps({"out": str(args.out), "completed_events": len(state["completed_event_ids"]),
                      "early_event_families": len(state["fit"]["early_progress_paths"]),
                      "fit_sha256": state["fit_sha256"]}, ensure_ascii=False))
