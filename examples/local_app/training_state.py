"""Prequential Tsukushi refits from a frozen seed and completed-event samples.

The mutable training file stores derived contributions, not raw tracker archives.
Every new event is scored against the previous fit before its contributions are
added. The fit is then rebuilt from the immutable seed plus those contributions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import completed_data
from engine import LocalKaori, TAIL, TIERS, read_state
from calibrated_control import CalibratedControl, HORIZONS
from member_ensemble import EmpiricalMemberEnsemble
from runner_regime_multiplier import RegimeMultiplierRunner
from model_regime_multiplier import _samples

SCHEMA = "tsukushi-derived-training-v1"
GROUPS = ("tail_pool", "multiplier_samples", "kaori_ratios", "aoi_templates")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def training_path(state_path: Path):
    return state_path.with_name("tsukushi-training.json")


def seed_path(state_path: Path):
    return state_path.with_name("tsukushi-seed-state.json")


def _store_with_digest(seed):
    base = {"schema": SCHEMA, "seed_fit_sha256": seed["fit_sha256"],
            "updates": []}
    return dict(base, sha256=_digest(base))


def _read_store(path, seed):
    store = json.loads(path.read_text(encoding="utf-8"))
    digest = store.pop("sha256", None)
    if (store.get("schema") != SCHEMA or
            store.get("seed_fit_sha256") != seed["fit_sha256"] or
            not isinstance(store.get("updates"), list) or digest != _digest(store)):
        raise ValueError("training sample store is invalid or does not match the seed")
    store["sha256"] = digest
    return store


def rebuild(seed, store):
    fit = copy.deepcopy(seed["fit"])
    ids = list(map(int, seed["completed_event_ids"]))
    cutoff = int(seed["training_cutoff_at"])
    for update in store["updates"]:
        event_id = int(update["event_id"])
        start, end = int(update["start_at"]), int(update["end_at"])
        if event_id in ids or start <= cutoff or end <= start:
            raise ValueError("training events overlap, repeat, or are out of order")
        cutoff = end
        ids.append(event_id)
        delta = update["delta"]
        for group in GROUPS:
            for key, rows in delta.get(group, {}).items():
                fit[group].setdefault(key, []).extend(copy.deepcopy(rows))
        fit["early_progress_paths"].extend(
            copy.deepcopy(delta.get("early_progress_paths", [])))
    state = {key: copy.deepcopy(value) for key, value in seed.items()
             if key != "fit"}
    state.update({"fit": fit, "fit_sha256": _digest(fit),
                  "completed_event_ids": ids, "training_cutoff_at": cutoff,
                  "seed_fit_sha256": seed["fit_sha256"],
                  "training_samples_sha256": store["sha256"],
                  "auto_updated_event_ids": [int(u["event_id"]) for u in store["updates"]],
                  "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return state


def load_or_create(state_file: Path, bundled_seed: Path):
    state_file = Path(state_file)
    seed_file = seed_path(state_file)
    if state_file.resolve() == seed_file.resolve():
        raise ValueError("mutable state and immutable seed paths must differ")
    if not seed_file.is_file():
        bundled = read_state(bundled_seed)
        _write_json(seed_file, bundled)
    seed = read_state(seed_file)
    samples_file = training_path(state_file)
    if not samples_file.is_file():
        if state_file.is_file():
            existing = read_state(state_file)
            if (existing.get("auto_updated_event_ids") or
                    int(existing["training_cutoff_at"]) > int(seed["training_cutoff_at"])):
                raise ValueError("training samples are missing for a newer local state")
        _write_json(samples_file, _store_with_digest(seed))
    store = _read_store(samples_file, seed)
    expected = rebuild(seed, store)
    try:
        existing = read_state(state_file)
    except (FileNotFoundError, ValueError, KeyError):
        existing = None
    if (existing is None or existing["fit_sha256"] != expected["fit_sha256"]
            or existing["completed_event_ids"] != expected["completed_event_ids"]
            or existing.get("training_samples_sha256") != store["sha256"]):
        _write_json(state_file, expected)
        return expected, seed, store
    return existing, seed, store


class _FrozenControl(LocalKaori):
    def observe_event(self, _event):
        pass


def _early_path(event):
    duration = int(event["end_at"]) - int(event["start_at"])
    paths = {}
    for tier in TIERS:
        blob = event["tiers"][str(tier)]
        final = float(blob["label"]["ep"])
        if final <= 0:
            return []
        by_time = {int(p["time"]): float(p["ep"]) for p in blob["points"]
                   if event["start_at"] <= int(p["time"]) <= event["end_at"]}
        points = [[(stamp - event["start_at"]) / duration, value / final]
                  for stamp, value in sorted(by_time.items())]
        points.append([1.0, 1.0])
        points.sort()
        if len(points) < 2 or any(a[1] > b[1] + 1e-9
                                   for a, b in zip(points, points[1:])):
            return []
        paths[str(tier)] = points
    return [{"event_id": int(event["event_id"]),
             "event_type": event["event_type"], "paths": paths}]


def event_delta(state, event):
    """Compute one event's fit contributions using the previous fit only."""
    if event["era"] != "voice500_1500":
        raise ValueError("automatic updater only accepts completed new-regime events")
    if int(event["start_at"]) <= int(state["training_cutoff_at"]):
        raise ValueError("event is not strictly later than the current training cutoff")

    model = EmpiricalMemberEnsemble()
    model.control = _FrozenControl(state)
    model.templates = {int(h): copy.deepcopy(rows)
                       for h, rows in state["fit"]["aoi_templates"].items()}
    prior_counts = {h: len(rows) for h, rows in model.templates.items()}
    model.initialize({})
    model.observe_event(event)
    templates = {str(h): model.templates[h][prior_counts[h]:]
                 for h in HORIZONS if len(model.templates[h]) > prior_counts[h]}

    base_state = copy.deepcopy(state)
    base_state["fit"]["kaori_ratios"] = {}
    calibration = CalibratedControl()
    calibration.base = _FrozenControl(base_state)
    calibration.observe_event(event)
    ratios = {f"{tier}:{h}": rows for (tier, h), rows in calibration.ratios.items()
              if rows}

    tail = TAIL.Champion()
    tail.observe_event(event)
    tail_pool = {str(h): rows for h, rows in tail.pool.items() if rows}

    multiplier = RegimeMultiplierRunner()
    multiplier.initialize({})
    multiplier.observe_event(event)
    multiplier_samples = {}
    for tier in TIERS:
        for horizon in HORIZONS:
            samples = _samples(multiplier.completed, tier, horizon)
            if samples:
                multiplier_samples[f"voice500_1500:{tier}:{horizon}"] = samples
    return {"tail_pool": tail_pool, "multiplier_samples": multiplier_samples,
            "kaori_ratios": ratios, "aoi_templates": templates,
            "early_progress_paths": _early_path(event)}


def sync_once(state_file: Path, bundled_seed: Path, now_ms: int | None = None):
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    state, seed, store = load_or_create(state_file, bundled_seed)
    updated = []
    for _start, _end, event_id in completed_data.eligible_events(state, now_ms):
        try:
            event, evidence = completed_data.completed_event(event_id, now_ms)
        except completed_data.PendingFinal as exc:
            return {"status": "pending_final", "event_id": event_id,
                    "reason": str(exc), "updated_event_ids": updated,
                    "state": state}
        if int(event["start_at"]) <= int(state["training_cutoff_at"]):
            raise ValueError("completed event chronology conflicts with the current state")
        delta = event_delta(state, event)
        update = {"event_id": event_id, "start_at": int(event["start_at"]),
                  "end_at": int(event["end_at"]),
                  "aggregate_end_at": int(event["aggregate_end_at"]),
                  "observed_at": now_ms, "evidence": evidence,
                  "final_cutoffs": {str(t): event["tiers"][str(t)]["label"]["ep"]
                                    for t in TIERS}, "delta": delta}
        core = {key: copy.deepcopy(value) for key, value in store.items()
                if key != "sha256"}
        core["updates"].append(update)
        next_store = dict(core, sha256=_digest(core))
        next_state = rebuild(seed, next_store)
        read_state_from_memory(next_state)
        _write_json(training_path(state_file), next_store)
        _write_json(state_file, next_state)
        store, state = next_store, next_state
        updated.append(event_id)
    return {"status": "updated" if updated else "up_to_date",
            "updated_event_ids": updated, "state": state}


def read_state_from_memory(state):
    if state.get("schema") != "tsukushi-local-state-v1" or _digest(state["fit"]) != state["fit_sha256"]:
        raise ValueError("refitted model state failed integrity check")
    if len(state["completed_event_ids"]) != len(set(state["completed_event_ids"])):
        raise ValueError("refitted model state contains duplicate events")
    return state
