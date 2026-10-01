#!/usr/bin/env python3
"""Bandori PT terminal-forecast benchmark. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics as st
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERSION = "0.3.20"
HOUR = 3_600_000
MODEL_API_VERSION = "bandoribench-model-api-v1"
MODEL_PHASES = ("development", "selection", "final", "all")
BASELINE_REGISTRY = {
    "persistence": {
        "class": "weak_baseline",
        "source_status": "native_baseline",
        "tracks": ("point",),
        "description": "Current cutoff held constant to event end.",
    },
    "linear24": {
        "class": "weak_baseline",
        "source_status": "native_baseline",
        "tracks": ("point",),
        "description": "Recent ~24h causal linear extrapolation.",
    },
    "calibrated-linear24": {
        "class": "causal_statistical_baseline",
        "source_status": "native_reconstruction",
        "tracks": ("point",),
        "description": "Linear24 plus median residual from only earlier completed events.",
    },
    "linear24-quantiles": {
        "class": "causal_statistical_baseline",
        "source_status": "native_reconstruction",
        "tracks": ("point", "probabilistic"),
        "description": "Causal residual-quantile extension of linear24.",
    },
    "bestdori-hierarchical": {
        "class": "formula_family_reconstruction",
        "source_status": "not_historical_platform_archive",
        "tracks": ("point",),
        "description": "Bestdori public formula-family replay with causal hierarchical rate estimation.",
    },
    "hhwx-instant": {
        "class": "public_formula_replay",
        "source_status": "strict_public_algorithm_replay",
        "tracks": ("point",),
        "description": "HHWX short-window public projection formula replay.",
    },
    "hhwx-24h": {
        "class": "public_formula_replay",
        "source_status": "strict_public_algorithm_replay",
        "tracks": ("point",),
        "description": "HHWX ~24h public projection formula replay.",
    },
    "rinko-dpra-replay": {
        "class": "historical_algorithm_replay",
        "source_status": "public_code_reconstruction",
        "tracks": ("point",),
        "description": "Reconstruction of the preserved Rinko/DPRA FIN algorithm.",
    },
    "multitier-analog-ensemble": {
        "class": "project_statistical_model",
        "source_status": "project_model",
        "tracks": ("point", "probabilistic"),
        "description": "Causal multi-tier analog retrieval ensemble.",
    },
    "care-s": {
        "class": "project_experimental_model",
        "source_status": "project_model",
        "tracks": ("point", "probabilistic"),
        "description": "CARE-S causal conditional correction model.",
    },
    "care-s2": {
        "class": "project_experimental_model",
        "source_status": "project_model",
        "tracks": ("point", "probabilistic"),
        "description": "CARE-S2 adaptive OOS-tuned correction model.",
    },
    "causal-stack": {
        "class": "project_experimental_model",
        "source_status": "project_model",
        "tracks": ("point", "probabilistic"),
        "description": "Causal OOS stack of multi-tier analog and hierarchical Bestdori.",
    },
}
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
TIERS = (1, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 1500,
         2000, 3000, 4000, 5000, 10000, 20000, 30000, 40000, 50000, 70000, 100000)
SERVERS = {"jp": 0, "en": 1, "tw": 2, "cn": 3}
SERVER_UTC_OFFSETS = {"jp": 9.0, "en": 0.0, "tw": 8.0, "cn": 8.0}
CALENDAR_DAY_TYPES = ("weekday", "weekend", "holiday", "makeup_workday")
# User-supplied domain corrections, 2026-09-26; NOT verified official reward data.
CN_OVERRIDES = {310: "voice500_1500", 311: "voice1000", 312: "voice1000", 313: "voice1000", 314: "voice500_1500"}
CN_ORDER = ((312, 311), (313, 311), (311, 310), (310, 314))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def load(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def save(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def number(value: Any, name: str = "number", minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: expected a number")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name}: must be finite and >= {minimum}")
    return float(value)


def integer(value: Any, name: str, minimum: int = 0) -> int:
    n = number(value, name, minimum)
    if int(n) != n:
        raise ValueError(f"{name}: expected an integer")
    return int(n)


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("empty quantile sample")
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def cn_era(event_id: int, start_at: int, transition_start: int | None) -> str:
    if event_id in CN_OVERRIDES:
        return CN_OVERRIDES[event_id]
    if transition_start is None:
        return "unknown"
    return "voice500_1500" if start_at >= transition_start else "voice1000"




def validate_calendar(calendar: dict | None, server: str) -> dict | None:
    """Validate/freeze public calendar facts known independently of event outcomes."""
    if calendar is None:
        return None
    if not isinstance(calendar, dict) or calendar.get("schema") != "bandoribench-calendar-v1":
        raise ValueError("calendar schema must be bandoribench-calendar-v1")
    if calendar.get("server") != server:
        raise ValueError("calendar server does not match benchmark server")
    offset = number(calendar.get("utc_offset_hours", SERVER_UTC_OFFSETS[server]),
                    "calendar utc_offset_hours", -12)
    if offset > 14:
        raise ValueError("calendar utc_offset_hours must be <=14")
    days = calendar.get("days", {})
    if not isinstance(days, dict):
        raise ValueError("calendar days must be an object")
    normalized = {}
    for day, raw in days.items():
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"invalid calendar date: {day}") from exc
        if isinstance(raw, str):
            dtype, known_at, previous_type, previous_known_at = raw, None, None, None
        elif isinstance(raw, dict):
            dtype = raw.get("type")
            known_at = raw.get("known_at")
            previous_type = raw.get("previous_type")
            previous_known_at = raw.get("previous_known_at")
        else:
            raise ValueError(f"invalid calendar entry for {day}")
        if dtype not in CALENDAR_DAY_TYPES:
            raise ValueError(f"invalid calendar day type for {day}")
        entry = {"type": dtype}
        if known_at is not None:
            entry["known_at"] = integer(known_at, f"calendar {day} known_at")
        if previous_type is not None:
            if previous_type not in CALENDAR_DAY_TYPES or previous_type == dtype:
                raise ValueError(f"invalid previous calendar day type for {day}")
            if known_at is None or previous_known_at is None:
                raise ValueError(f"calendar {day} previous_type requires known_at and previous_known_at")
            previous_known = integer(previous_known_at, f"calendar {day} previous_known_at")
            if previous_known >= entry["known_at"]:
                raise ValueError(f"calendar {day} previous_known_at must predate known_at")
            entry["previous_type"] = previous_type
            entry["previous_known_at"] = previous_known
        elif previous_known_at is not None:
            raise ValueError(f"calendar {day} previous_known_at requires previous_type")
        normalized[day] = entry
    result = {"schema": "bandoribench-calendar-v1", "server": server,
              "utc_offset_hours": float(offset), "days": dict(sorted(normalized.items()))}
    if "years" in calendar:
        years = calendar["years"]
        if (not isinstance(years, list) or any(isinstance(y, bool) or not isinstance(y, int) for y in years)
                or years != sorted(set(years))):
            raise ValueError("calendar years must be a sorted unique integer list")
        result["years"] = years
    if "provenance" in calendar:
        if not isinstance(calendar["provenance"], dict):
            raise ValueError("calendar provenance must be an object")
        result["provenance"] = calendar["provenance"]
    return result

def _local_datetime(timestamp_ms: int, server: str, calendar: dict | None = None) -> datetime:
    offset = (calendar or {}).get("utc_offset_hours", SERVER_UTC_OFFSETS[server])
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc) + timedelta(hours=float(offset))


def _calendar_day_type(timestamp_ms: int, server: str, calendar: dict | None,
                       knowledge_at: int) -> str:
    local = _local_datetime(timestamp_ms, server, calendar)
    day = local.strftime("%Y-%m-%d")
    entry = (calendar or {}).get("days", {}).get(day)
    if isinstance(entry, dict):
        known_at = entry.get("known_at")
        if known_at is None or known_at <= knowledge_at:
            return entry["type"]
        if entry.get("previous_type") is not None:
            previous_known_at = entry.get("previous_known_at")
            if previous_known_at is None or previous_known_at <= knowledge_at:
                return entry["previous_type"]
    return "weekend" if local.weekday() >= 5 else "weekday"

def _calendar_window_fractions(task: dict, calendar: dict | None,
                               start: int, end: int, prefix: str) -> dict[str, float]:
    counts = {kind: 0.0 for kind in CALENDAR_DAY_TYPES}
    total = max(0.0, (end - start) / HOUR)
    if total <= 0:
        return {f"{prefix}_{kind}_frac": 0.0 for kind in CALENDAR_DAY_TYPES}
    cursor = start
    while cursor < end:
        nxt = min(end, cursor + HOUR)
        midpoint = cursor + (nxt - cursor) // 2
        kind = _calendar_day_type(midpoint, task["server"], calendar, task["issued_at"])
        counts[kind] += (nxt - cursor) / HOUR
        cursor = nxt
    return {f"{prefix}_{kind}_frac": counts[kind] / total for kind in CALENDAR_DAY_TYPES}


def calendar_feature_dict(task: dict, calendar: dict | None) -> dict[str, float]:
    local = _local_datetime(task["issued_at"], task["server"], calendar)
    current_kind = _calendar_day_type(task["issued_at"], task["server"], calendar, task["issued_at"])
    hour = local.hour + local.minute / 60.0
    weekday = local.weekday()
    out = {
        "cal_hour_sin": math.sin(2 * math.pi * hour / 24.0),
        "cal_hour_cos": math.cos(2 * math.pi * hour / 24.0),
        "cal_weekday_sin": math.sin(2 * math.pi * weekday / 7.0),
        "cal_weekday_cos": math.cos(2 * math.pi * weekday / 7.0),
    }
    for kind in CALENDAR_DAY_TYPES:
        out[f"cal_now_{kind}"] = 1.0 if current_kind == kind else 0.0
    out.update(_calendar_window_fractions(task, calendar, task["issued_at"], task["end_at"], "cal_rem"))
    out.update(_calendar_window_fractions(task, calendar, max(task["issued_at"], task["end_at"] - 24 * HOUR),
                                          task["end_at"], "cal_last24"))
    out.update(_calendar_window_fractions(task, calendar, max(task["issued_at"], task["end_at"] - 6 * HOUR),
                                          task["end_at"], "cal_last6"))
    return out


def legacy_reward_feature_dict(task: dict) -> dict[str, float]:
    """Frozen CARE v1 geometry; not an exhaustive reward-topology statement."""
    tier = float(task["tier"])
    boundaries: list[float] = []
    if task["server"] == "cn":
        if task.get("era") == "voice1000":
            boundaries = [1000.0]
        elif task.get("era") == "voice500_1500":
            boundaries = [500.0, 1500.0]
    out = {"rank_log": math.log(max(tier, 1.0)), "reward_known": float(bool(boundaries)),
           "reward_boundary": float(tier in boundaries), "reward_between": 0.0,
           "reward_log_distance": 0.0}
    if boundaries:
        out["reward_log_distance"] = min(abs(math.log(tier / b)) for b in boundaries)
        if len(boundaries) == 2:
            lo, hi = sorted(boundaries)
            out["reward_between"] = float(lo < tier < hi)
    return out

REWARD_TOPOLOGY_SCHEMA = "cn-reward-topology-v2"
CARE_REWARD_FEATURE_SCHEMA = "legacy-reward-geometry-v1"


def reward_topology(task: dict) -> dict:
    """Categorical user-supplied cutoff topology, corrected on 2026-10-01.

    A boundary means a distinct reward cutoff, not whether an individual player
    above that rank receives any reward. Era identifiers remain compatible.
    No item values or equal attractiveness across eras are assumed.
    """
    tier = float(task["tier"])
    era = task.get("era")
    known = (task.get("server") == "cn" and tier in (500, 1000, 1500, 2000)
             and era in ("voice1000", "voice500_1500"))
    role = "unknown"
    boundary = None
    if known:
        if era == "voice1000":
            boundary = tier == 1000
            role = "legacy_sole_boundary" if boundary else "no_separate_tracked_boundary"
        else:
            boundary = True
            role = "modern_higher_attraction" if tier in (500, 1500) else "modern_other_rewarded"
    return {"schema": REWARD_TOPOLOGY_SCHEMA,
            "source": "user_supplied_2026-10-01", "known": known,
            "is_reward_boundary": boundary, "attraction_category": role}


def reward_feature_dict(task: dict) -> dict[str, float]:
    """Full reward topology v2; existing CARE baselines explicitly use v1."""
    topology = reward_topology(task)
    tier = float(task["tier"])
    known = topology["known"]
    boundaries = ([1000.0] if task.get("era") == "voice1000"
                  else [500.0, 1000.0, 1500.0, 2000.0]) if known else []
    role = topology["attraction_category"]
    return {"rank_log": math.log(max(tier, 1.0)),
            "reward_known": float(known),
            "reward_boundary": float(topology["is_reward_boundary"] is True),
            "reward_between": float(bool(boundaries) and min(boundaries) < tier < max(boundaries)
                                    and tier not in boundaries),
            "reward_log_distance": min(abs(math.log(tier / b)) for b in boundaries) if boundaries else 0.0,
            "reward_known_boundary_count": float(len(boundaries)),
            "reward_attraction_known": float(role in ("modern_higher_attraction", "modern_other_rewarded")),
            "reward_attraction_higher": float(role == "modern_higher_attraction"),
            "reward_other_rewarded": float(role == "modern_other_rewarded"),
            "reward_legacy_sole_boundary": float(role == "legacy_sole_boundary")}


def normalize_points(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("cutoffs must be an array")
    by_time: dict[int, dict] = {}
    for row in rows:
        if row is None:
            continue
        if not isinstance(row, dict):
            raise ValueError("cutoff rows must be objects or null")
        t = integer(row["time"], "observation time")
        ep = number(row["ep"], "PT")
        final = row.get("isFinal", False)
        if not isinstance(final, bool):
            raise ValueError("isFinal must be boolean")
        point = {"time": t, "ep": ep, "isFinal": final}
        if row.get("available_at") is not None:
            point["available_at"] = integer(row["available_at"], "available_at", t)
        old = by_time.get(t)
        if old:
            if old["ep"] != ep or old.get("available_at") != point.get("available_at"):
                raise ValueError("conflicting duplicate observation")
            point["isFinal"] = final or old["isFinal"]
        by_time[t] = point
    points = sorted(by_time.values(), key=lambda p: p["time"])
    if any(a["ep"] > b["ep"] for a, b in zip(points, points[1:])):
        raise ValueError("decreasing PT; repair/review outside the main benchmark")
    return points


def validate_dataset(data: dict) -> list[dict]:
    if data.get("schema") != "bandoribench-dataset-v1":
        raise ValueError("unknown dataset schema")
    events, seen = [], set()
    for raw in data["events"]:
        e = dict(raw)
        if e["server"] not in SERVERS:
            raise ValueError("unsupported server")
        e["event_id"] = integer(e["event_id"], "event ID", 1)
        e["start_at"] = integer(e["start_at"], "start_at")
        e["end_at"] = integer(e["end_at"], "PT stop time", e["start_at"] + 1)
        key = (e["server"], e["event_id"])
        if key in seen:
            raise ValueError("duplicate event")
        seen.add(key)
        e["era"] = str(e.get("era", "unknown"))
        e["event_type"] = str(e.get("event_type", "unknown"))
        tiers = {}
        for tier, series in e["tiers"].items():
            if int(tier) not in TIERS or str(int(tier)) != str(tier):
                raise ValueError("unsupported or noncanonical tier")
            s = dict(series)
            s["points"] = normalize_points(s["points"])
            label = dict(s["label"])
            label["ep"] = number(label["ep"], "final PT", 1)
            label["time"] = integer(label["time"], "label time", e["end_at"])
            if label.get("quality") not in ("archive_final", "explicit_final", "post_end_final", "post_aggregate_observation", "verified", "synthetic"):
                raise ValueError("terminal labels require archive_final / explicit_final / post_end_final / post_aggregate_observation / verified / synthetic")
            if not label.get("evidence"):
                raise ValueError("label evidence is required")
            if label["quality"] == "synthetic" and not data.get("synthetic", False):
                raise ValueError("synthetic labels require a synthetic dataset")
            live = [p for p in s["points"] if p["time"] < e["end_at"] and not p["isFinal"]]
            if live and max(p["ep"] for p in live) > label["ep"]:
                raise ValueError("final PT below observed PT")
            flags = [p for p in s["points"] if p["isFinal"]]
            if flags and any(p["ep"] != label["ep"] for p in flags):
                raise ValueError("final label conflicts with provider final flag")
            s["label"] = label
            tiers[str(tier)] = s
        e["tiers"] = tiers
        events.append(e)
    # Validate only orders for which both entries are present. Never fix dates by ID.
    cn = {e["event_id"]: e for e in events if e["server"] == "cn"}
    for before, after in CN_ORDER:
        if before in cn and after in cn and cn[before]["start_at"] >= cn[after]["start_at"]:
            raise ValueError(f"CN chronology conflicts with supplied correction: {before} before {after}")
    for eid, era in CN_OVERRIDES.items():
        if eid in cn and cn[eid]["era"] != era:
            raise ValueError(f"incorrect CN reward era for event {eid}")
    return sorted(events, key=lambda e: (e["start_at"], e["server"], e["event_id"]))


def coarse_history(points: list[dict], start: int, issue: int,
                   step_hours: int = 6, stale_hours: int = 3) -> list[dict]:
    """Causal as-of samples on an issue-anchored grid. No future interpolation."""
    if step_hours <= 0 or stale_hours < 0 or issue < start:
        raise ValueError("invalid sampling window")
    visible = [p for p in points if start <= p["time"] <= issue and not p.get("isFinal")
               and p.get("available_at", p["time"]) <= issue]
    slots = list(range(issue, start - 1, -step_hours * HOUR))[::-1]
    out, last_time = [], None
    for slot in slots:
        candidates = [p for p in visible if p["time"] <= slot
                      and p.get("available_at", p["time"]) <= slot]
        if not candidates:
            continue
        p = max(candidates, key=lambda row: row["time"])
        if slot - p["time"] > stale_hours * HOUR or p["time"] == last_time:
            continue
        out.append({"time": p["time"], "ep": p["ep"], "slot_time": slot})
        last_time = p["time"]
    return out


def make_task(e: dict, tier: str, horizon: int, protocol: dict) -> dict:
    issue = e["end_at"] - horizon * HOUR
    if issue - e["start_at"] < 24 * HOUR:
        raise ValueError("less than 24 hours of event history")
    history = coarse_history(e["tiers"][tier]["points"], e["start_at"], issue,
                             protocol["step_hours"], protocol["stale_hours"])
    if len(history) < 3 or issue - history[-1]["time"] > protocol["stale_hours"] * HOUR:
        raise ValueError("insufficient or stale coarse history")
    if history[-1]["time"] - history[0]["time"] < 18 * HOUR:
        raise ValueError("insufficient observation span")
    metadata = {k: e[k] for k in ("server", "event_id", "start_at", "end_at", "event_type", "era")}
    return {**metadata, "case_id": f'{e["server"]}:{e["event_id"]}:{tier}:{horizon}',
            "tier": int(tier), "horizon_hours": horizon, "issued_at": issue,
            "input_cutoff_at": history[-1]["time"], "history": history}



def raw_history(points: list[dict], start: int, issue: int) -> list[dict]:
    """Return every causally visible tracker observation; never interpolate/resample."""
    if issue < start:
        raise ValueError("invalid raw history window")
    out = []
    for p in points:
        available = p.get("available_at", p["time"])
        if start <= p["time"] <= issue and not p.get("isFinal") and available <= issue:
            row = {"time": p["time"], "ep": p["ep"]}
            if "available_at" in p:
                row["available_at"] = p["available_at"]
            out.append(row)
    return out


def make_raw_task(e: dict, tier: str, horizon: int, protocol: dict) -> dict:
    issue = e["end_at"] - horizon * HOUR
    if issue - e["start_at"] < 24 * HOUR:
        raise ValueError("less than 24 hours of event history")
    history = raw_history(e["tiers"][tier]["points"], e["start_at"], issue)
    stale_hours = protocol["stale_hours"]
    if len(history) < 3 or issue - history[-1]["time"] > stale_hours * HOUR:
        raise ValueError("insufficient or stale raw history")
    if history[-1]["time"] - history[0]["time"] < 18 * HOUR:
        raise ValueError("insufficient observation span")
    metadata = {k: e[k] for k in ("server", "event_id", "start_at", "end_at", "event_type", "era")}
    return {**metadata, "case_id": f'{e["server"]}:{e["event_id"]}:{tier}:{horizon}',
            "tier": int(tier), "horizon_hours": horizon, "issued_at": issue,
            "input_cutoff_at": history[-1]["time"], "input_sampling": "raw_tracker_observations",
            "history": history}


def linear24(task: dict) -> float:
    history = task["history"]
    last = history[-1]
    earlier = [p for p in history[:-1] if p["time"] <= last["time"] - 24 * HOUR]
    first = earlier[-1] if earlier else history[0]
    velocity = max(0.0, (last["ep"] - first["ep"]) / (last["time"] - first["time"]))
    return last["ep"] + velocity * (task["end_at"] - last["time"])


def regression(points: list[dict], start: int, end: int) -> tuple[float, float]:
    if len(points) < 2:
        raise ValueError("not enough regression points")
    xs = [(p["time"] - start) / (end - start) for p in points]
    ys = [p["ep"] for p in points]
    mx, my = st.mean(xs), st.mean(ys)
    denominator = sum((x - mx) ** 2 for x in xs)
    if denominator <= 0:
        raise ValueError("singular regression")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denominator
    return my - b * mx, b


def bestdori_recalibrated(task: dict, rate: float) -> float:
    """Formula-family baseline on COARSE inputs, not archived platform forecasts."""
    start, end = task["start_at"], task["end_at"]
    fit_points, weighted, weight_sum, previous = [], 0.0, 0.0, None
    for point in task["history"]:
        if point["time"] - start >= 12 * HOUR:
            fit_points.append(point)
        if point["time"] - start >= 24 * HOUR and end - point["time"] >= 24 * HOUR and len(fit_points) >= 5:
            a, b = regression(fit_points, start, end)
            previous = a + b * (1 + rate)
        if previous is not None:
            w = ((point["time"] - start) / (end - start)) ** 2
            weighted += previous * w
            weight_sum += w
    if not weight_sum:
        raise ValueError("Bestdori-family baseline cannot initialize on this coarse prefix")
    return max(0.0, weighted / weight_sum)


def scale_key(task: dict) -> str:
    return f'{task["server"]}:{task["tier"]}:{task["horizon_hours"]}'


def rate_key(task: dict) -> str:
    return f'{task["server"]}:{task["event_type"]}:{task["tier"]}'


def freeze(data: dict, n_calibration: int = 10, horizons: tuple[int, ...] = (72, 48, 24, 12, 6),
           step_hours: int = 6, stale_hours: int = 3) -> dict:
    events = validate_dataset(data)
    if len({e["server"] for e in events}) != 1:
        raise ValueError("freeze one server per benchmark; compare servers in separate leaderboards")
    requested_tiers = [int(t) for t in data.get("requested_tiers", [])]
    if requested_tiers:
        if len(set(requested_tiers)) != len(requested_tiers) or any(t not in TIERS for t in requested_tiers):
            raise ValueError("dataset requested_tiers are invalid")
        complete, incomplete = [], []
        for e in events:
            missing = [t for t in requested_tiers if str(t) not in e["tiers"]]
            if missing:
                incomplete.append({"event": e["event_id"], "missing_tiers": missing})
            else:
                complete.append(e)
        events = complete
    else:
        incomplete = []
    if n_calibration < 3 or len(events) <= n_calibration:
        raise ValueError("need >=3 calibration events and >=1 later complete test event")
    if not horizons or len(set(horizons)) != len(horizons) or any(h <= 0 for h in horizons):
        raise ValueError("horizons must be unique positive hours")
    if step_hours <= 0 or stale_hours < 0:
        raise ValueError("invalid sampling parameters")
    calibration, test = events[:n_calibration], events[n_calibration:]
    if max(e["end_at"] for e in calibration) >= min(e["start_at"] for e in test):
        raise ValueError("calibration overlaps test period")
    protocol = {"schema": "bandoribench-protocol-v1", "software_version": VERSION,
                "horizons": list(horizons), "step_hours": step_hours, "stale_hours": stale_hours,
                "label_policy": "explicit_final_or_verified", "synthetic": bool(data.get("synthetic")),
                "knowledge_time": data.get("knowledge_time", "observed_at_only"),
                "requested_tiers": requested_tiers,
                "incomplete_events_excluded": incomplete,
                "calibration_event_ids": [e["event_id"] for e in calibration],
                "test_event_ids": [e["event_id"] for e in test], "aggregation": "equal_era_tier_horizon_cells",
                "score": "100/(1+macro_scaled_loss)", "dataset_sha256": digest(data)}
    errors, finals, residuals, rates = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    exclusions, tasks, truth = [], [], {}
    for e in calibration:
        for tier, series in e["tiers"].items():
            for h in horizons:
                try:
                    task = make_task(e, tier, h, protocol)
                except ValueError as exc:
                    exclusions.append({"stage": "calibration", "event": e["event_id"], "tier": tier, "horizon": h, "reason": str(exc)})
                    continue
                y, pred, key = series["label"]["ep"], linear24(task), scale_key(task)
                errors[key].append(abs(pred - y))
                residuals[key].append(y - pred)
                finals[key].append(y)
            try:
                t = make_task(e, tier, 24, protocol)
                eligible = [p for p in t["history"] if p["time"] >= e["start_at"] + 12 * HOUR]
                a, b = regression(eligible, e["start_at"], e["end_at"])
                if b > 0:
                    rates[rate_key(t)].append((series["label"]["ep"] - a - b) / b)
            except ValueError:
                pass  # This optional baseline has no rate; it will report unsupported cases.
    scales = {k: max(st.mean(v), st.median(finals[k]) * 0.01, 1.0)
              for k, v in errors.items() if len(v) >= 3}
    for e in test:
        for tier, series in e["tiers"].items():
            batch = []
            try:
                for h in horizons:
                    task = make_task(e, tier, h, protocol)
                    if scale_key(task) not in scales:
                        raise ValueError(f"fewer than 3 calibration cases for {scale_key(task)}")
                    batch.append(task)
            except ValueError as exc:
                exclusions.append({"stage": "test", "event": e["event_id"], "tier": tier, "reason": str(exc)})
                continue  # Entire event-tier is excluded before any submitted forecast is read.
            for task in batch:
                tasks.append(task)
                truth[task["case_id"]] = series["label"]
    if not tasks:
        raise ValueError("no eligible test cases; inspect labels, sampling, and calibration coverage")
    body = {"protocol": protocol, "scales": scales, "tasks": tasks, "truth": truth,
            "calibration": {"events": calibration, "residuals": dict(residuals),
                            "rates": {k: st.median(v) for k, v in rates.items() if len(v) >= 3}},
            "exclusions": exclusions}
    body["benchmark_id"] = digest(body)
    return body



def freeze_walkforward(data: dict, warmup_events: int = 12,
                       horizons: tuple[int, ...] = (72, 48, 24, 12, 6),
                       stale_hours: int = 3, calendar: dict | None = None,
                       requested_tiers_override: tuple[int, ...] | None = None) -> dict:
    """Protocol v2: raw tracker prefixes with expanding historical context."""
    events = validate_dataset(data)
    if len({e["server"] for e in events}) != 1:
        raise ValueError("freeze one server per benchmark; compare servers separately")
    server = events[0]["server"]
    calendar = validate_calendar(calendar, server)
    requested_tiers = ([int(t) for t in requested_tiers_override]
                       if requested_tiers_override is not None
                       else [int(t) for t in data.get("requested_tiers", [])])
    if not requested_tiers or len(set(requested_tiers)) != len(requested_tiers) or any(t not in TIERS for t in requested_tiers):
        raise ValueError("walk-forward datasets require valid requested_tiers")
    complete, incomplete = [], []
    for e in events:
        missing = [t for t in requested_tiers if str(t) not in e["tiers"]]
        if missing:
            incomplete.append({"event": e["event_id"], "missing_tiers": missing})
        else:
            complete.append(e)
    events = complete
    if warmup_events < 3 or len(events) <= warmup_events:
        raise ValueError("need >=3 warm-up events and >=1 later complete hindcast event")
    if not horizons or len(set(horizons)) != len(horizons) or any(h <= 0 for h in horizons):
        raise ValueError("horizons must be unique positive hours")
    if stale_hours < 0:
        raise ValueError("invalid stale-hours")
    warmup, targets = events[:warmup_events], events[warmup_events:]
    if max(e["end_at"] for e in warmup) >= min(e["start_at"] for e in targets):
        raise ValueError("warm-up overlaps hindcast period")
    protocol = {
        "schema": "bandoribench-protocol-v2",
        "mode": "expanding_walk_forward_raw",
        "software_version": VERSION,
        "horizons": list(horizons),
        "stale_hours": stale_hours,
        "synthetic": bool(data.get("synthetic")),
        "knowledge_time": data.get("knowledge_time", "observed_at_only"),
        "requested_tiers": requested_tiers,
        "incomplete_events_excluded": incomplete,
        "warmup_event_ids": [e["event_id"] for e in warmup],
        "target_event_ids": [e["event_id"] for e in targets],
        "aggregation": "equal_era_tier_horizon_cells",
        "score": "100/(1+macro_scaled_loss)",
        "scale_policy": "warmup_linear24_fixed",
        "input_policy": "all raw tracker observations visible by issued_at",
        "history_policy": "models may fit only events listed in history_event_ids",
        "dataset_sha256": digest(data),
        "calendar_sha256": digest(calendar) if calendar is not None else None,
    }
    errors, finals = defaultdict(list), defaultdict(list)
    exclusions, tasks, truth = [], [], {}
    for e in warmup:
        for tier in map(str, requested_tiers):
            series = e["tiers"][tier]
            for h in horizons:
                try:
                    task = make_raw_task(e, tier, h, protocol)
                except ValueError as exc:
                    exclusions.append({"stage": "warmup_scale", "event": e["event_id"],
                                       "tier": tier, "horizon": h, "reason": str(exc)})
                    continue
                key = scale_key(task)
                errors[key].append(abs(linear24(task) - series["label"]["ep"]))
                finals[key].append(series["label"]["ep"])
    scales = {k: max(st.mean(v), st.median(finals[k]) * 0.01, 1.0)
              for k, v in errors.items() if len(v) >= 3}
    for i, e in enumerate(events[warmup_events:], start=warmup_events):
        allowed = [past["event_id"] for past in events[:i]]
        for h in horizons:
            panel, failures = [], {}
            for tier in map(str, requested_tiers):
                series = e["tiers"][tier]
                try:
                    task = make_raw_task(e, tier, h, protocol)
                    if scale_key(task) not in scales:
                        raise ValueError(f"fewer than 3 warm-up scale cases for {scale_key(task)}")
                    task["history_event_ids"] = allowed
                    panel.append((tier, task, series["label"]))
                except ValueError as exc:
                    failures[tier] = str(exc)
            if failures:
                panel_failures = dict(sorted(failures.items(), key=lambda item: int(item[0])))
                for tier in map(str, requested_tiers):
                    exclusions.append({
                        "stage": "hindcast",
                        "event": e["event_id"],
                        "tier": tier,
                        "horizon": h,
                        "reason": failures.get(tier, "incomplete multi-tier panel"),
                        "panel_failures": panel_failures,
                    })
                continue
            for _, task, label in panel:
                tasks.append(task)
                truth[task["case_id"]] = label
    if not tasks:
        raise ValueError("no eligible walk-forward cases")
    hindcast_exclusions = [row for row in exclusions if row.get("stage") == "hindcast"]
    reason_counts = defaultdict(int)
    for row in hindcast_exclusions:
        reason_counts[row["reason"]] += 1
    eligible_event_ids = {task["event_id"] for task in tasks}
    protocol["eligibility_policy"] = "complete requested-tier panel per event and horizon"
    protocol["target_case_count_before_eligibility"] = (
        len(targets) * len(requested_tiers) * len(horizons)
    )
    protocol["eligible_case_count"] = len(tasks)
    protocol["excluded_case_count"] = len(hindcast_exclusions)
    protocol["excluded_cases_by_reason"] = dict(sorted(reason_counts.items()))
    protocol["eligible_target_event_ids"] = [
        e["event_id"] for e in targets if e["event_id"] in eligible_event_ids
    ]
    protocol["fully_excluded_target_event_ids"] = [
        e["event_id"] for e in targets if e["event_id"] not in eligible_event_ids
    ]
    if protocol["eligible_case_count"] + protocol["excluded_case_count"] != protocol["target_case_count_before_eligibility"]:
        raise ValueError("internal target eligibility accounting mismatch")
    body = {
        "protocol": protocol,
        "scales": scales,
        "tasks": tasks,
        "truth": truth,
        # Kept once for compactness. history_event_ids is the causal contract for each task.
        "reference_events": events,
        "exclusions": exclusions,
    }
    if calendar is not None:
        body["calendar"] = calendar
    body["benchmark_id"] = digest(body)
    return body


def verify_bundle(bundle: dict) -> None:
    body = {k: v for k, v in bundle.items() if k != "benchmark_id"}
    if digest(body) != bundle.get("benchmark_id"):
        raise ValueError("benchmark fingerprint mismatch; do not edit a frozen benchmark")


def public_bundle(bundle: dict) -> dict:
    """Public replay inputs. This is a trusted offline protocol, not an anti-cheat sandbox."""
    keys = ["benchmark_id", "protocol", "tasks", "scales"]
    if bundle["protocol"]["schema"] == "bandoribench-protocol-v1":
        keys.append("calibration")
    else:
        keys.append("reference_events")
        if "calendar" in bundle:
            keys.append("calendar")
    return {k: bundle[k] for k in keys}


def _walkforward_events(public: dict, task: dict) -> list[dict]:
    allowed = set(task.get("history_event_ids", []))
    return [e for e in public["reference_events"] if e["event_id"] in allowed]


def _walkforward_residuals(public: dict, task: dict) -> list[float]:
    residuals = []
    protocol, tier, horizon = public["protocol"], str(task["tier"]), task["horizon_hours"]
    for e in _walkforward_events(public, task):
        if tier not in e["tiers"]:
            continue
        try:
            hist_task = make_raw_task(e, tier, horizon, protocol)
        except ValueError:
            continue
        residuals.append(e["tiers"][tier]["label"]["ep"] - linear24(hist_task))
    return residuals


def _walkforward_bestdori_rate(public: dict, task: dict, shrink_k: float = 3.0) -> float:
    tier = str(task["tier"])
    global_rates, type_rates = [], []
    for e in _walkforward_events(public, task):
        if tier not in e["tiers"]:
            continue
        try:
            t = make_raw_task(e, tier, 24, public["protocol"])
            eligible = [p for p in t["history"] if p["time"] >= e["start_at"] + 12 * HOUR]
            a, b = regression(eligible, e["start_at"], e["end_at"])
            if b <= 0:
                continue
            rate = (e["tiers"][tier]["label"]["ep"] - a - b) / b
        except ValueError:
            continue
        global_rates.append(rate)
        if e["event_type"] == task["event_type"]:
            type_rates.append(rate)
    if len(global_rates) < 3:
        raise ValueError("fewer than 3 historical rates for this tier")
    global_rate = st.median(global_rates)
    if not type_rates:
        return global_rate
    type_rate = st.median(type_rates)
    w = len(type_rates) / (len(type_rates) + shrink_k)
    return w * type_rate + (1 - w) * global_rate


def predict(public: dict, model: str = "linear24") -> dict:
    models = set(BASELINE_REGISTRY) | {"bestdori-recalibrated"}
    if model not in models:
        raise ValueError("unknown baseline")
    v2 = public["protocol"]["schema"] == "bandoribench-protocol-v2"
    predictions = []
    care_cache = {}
    for task in public["tasks"]:
        row = {"case_id": task["case_id"]}
        try:
            value = linear24(task)
            if model == "persistence":
                value = task["history"][-1]["ep"]
            elif model == "calibrated-linear24":
                residuals = _walkforward_residuals(public, task) if v2 else public["calibration"]["residuals"][scale_key(task)]
                if len(residuals) < 3:
                    raise ValueError("fewer than 3 historical residuals")
                value += st.median(residuals)
            elif model == "bestdori-recalibrated":
                if v2:
                    raise ValueError("legacy fixed-calibration Bestdori baseline is protocol-v1 only")
                rate = public["calibration"]["rates"].get(rate_key(task))
                if rate is None:
                    raise ValueError("no >=3-event calibration rate for this type/tier")
                value = bestdori_recalibrated(task, rate)
            elif model == "bestdori-hierarchical":
                if not v2:
                    raise ValueError("hierarchical Bestdori baseline requires protocol-v2")
                value = bestdori_recalibrated(task, _walkforward_bestdori_rate(public, task))
            elif model == "multitier-analog-ensemble":
                value, row["quantiles"], row["ensemble"] = multitier_analog_ensemble(public, task)
            elif model == "hhwx-instant":
                if not v2:
                    raise ValueError("HHWX exact projection replay requires protocol-v2 raw history")
                value = hhwx_projection(task, "instant")
            elif model == "hhwx-24h":
                if not v2:
                    raise ValueError("HHWX exact projection replay requires protocol-v2 raw history")
                value = hhwx_projection(task, "24h")
            elif model == "rinko-dpra-replay":
                if not v2:
                    raise ValueError("Rinko/DPRA replay requires protocol-v2 raw history")
                value = rinko_dpra_replay(task)
            elif model == "care-s":
                value, row["quantiles"], row["care"] = care_s_forecast(public, task, care_cache)
            elif model == "care-s2":
                value, row["quantiles"], row["care"] = care_s2_forecast(public, task, care_cache)
            elif model == "causal-stack":
                value, row["quantiles"], row["stack"] = causal_stack_forecast(public, task, care_cache)
            row["prediction"] = value
            if model == "linear24-quantiles":
                residuals = _walkforward_residuals(public, task) if v2 else public["calibration"]["residuals"][scale_key(task)]
                if len(residuals) < 5:
                    raise ValueError("fewer than 5 historical residuals for quantiles")
                row["quantiles"] = {str(q): max(task["history"][-1]["ep"], value + percentile(residuals, q)) for q in QUANTILES}
                row["prediction"] = row["quantiles"]["0.5"]
        except (ValueError, KeyError) as exc:
            row["error"] = str(exc)
        predictions.append(row)
    if model in ("care-s", "care-s2", "causal-stack"):
        _enforce_care_tier_order(public, predictions)
    return {"benchmark_id": public["benchmark_id"], "model_id": model,
            "model_version": VERSION,
            "provenance": ("causal_stack_oos_tuned" if model == "causal-stack"
                           else "care_s2_adaptive_causal_walk_forward" if model == "care-s2"
                           else "care_s_causal_walk_forward" if model == "care-s"
                           else "walk_forward_raw_history" if v2
                           else "offline_coarse_recompute_not_platform_archive"),
            "predictions": predictions}




HHWX_INSTANT_MIN_WINDOW_MS = (9 * 60 + 45) * 1000
HHWX_DAY_MIN_WINDOW_MS = (23 * 60 + 55) * 60 * 1000


def _hhwx_round(value: float) -> int:
    """Match JavaScript Math.round for the non-negative tracker projections."""
    return math.floor(value + 0.5)


def hhwx_projection(task: dict, mode: str) -> float:
    """Replay HHWX's public instant/24h projection from the visible prefix."""
    if mode not in ("instant", "24h"):
        raise ValueError("unknown HHWX projection mode")
    history = list(task["history"])
    if not history:
        raise ValueError("empty tracker prefix")
    points = history
    if history[0]["time"] > task["start_at"]:
        points = [{"time": task["start_at"], "ep": 0.0}] + history
    last = points[-1]
    minimum = HHWX_INSTANT_MIN_WINDOW_MS if mode == "instant" else HHWX_DAY_MIN_WINDOW_MS
    references = [p for p in points[:-1] if last["time"] - p["time"] >= minimum]
    if not references:
        raise ValueError(f"HHWX {mode} projection has no minimum-window reference")
    reference = max(references, key=lambda p: p["time"])
    elapsed = last["time"] - reference["time"]
    if elapsed <= 0:
        raise ValueError("HHWX projection reference is not earlier than latest point")
    velocity_per_ms = (last["ep"] - reference["ep"]) / elapsed
    projected = last["ep"] + velocity_per_ms * (task["end_at"] - last["time"])
    return float(max(0, _hhwx_round(projected)))


def _xy_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("not enough DPRA regression points")
    mx, my = st.mean(xs), st.mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance <= 0:
        raise ValueError("singular DPRA regression")
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
    return my - slope * mx, slope


def rinko_dpra_replay(task: dict, pred_length: int = 6, gamma_threshold: float = 1.0) -> float:
    """Replay the public Rinko/DPRA FIN curve used by the 2022 Hoshino plugin."""
    duration = task["end_at"] - task["start_at"]
    if duration <= 0:
        raise ValueError("invalid event duration")
    rows, seen = [], set()
    for point in task["history"]:
        pct = round((point["time"] - task["start_at"]) / duration * 100.0, 3)
        pct = min(100.0, pct)
        item = (pct, float(point["ep"]))
        if item not in seen:
            seen.add(item)
            rows.append(item)
    if len(rows) < pred_length + 1:
        raise ValueError("Rinko/DPRA needs more tracker points")

    reg = {}
    for num in range(pred_length + 1, len(rows) + 1):
        current_pct = rows[num - 1][0]
        lower_span = math.ceil((1.0 - current_pct / 100.0) * len(rows))
        left = num - lower_span
        if left <= pred_length + 1:
            left = pred_length - 1
        elif left == num:
            left = num - 1
        else:
            left = num - lower_span
        window = rows[left:num]
        try:
            intercept, slope = _xy_regression([x for x, _ in window], [y for _, y in window])
        except ValueError:
            continue
        reg[current_pct] = {
            "reg_intercept": intercept,
            "reg_slope": slope,
            "reg_final": intercept + slope * 100.0,
        }

    slopes = {}
    for num in range(2, len(rows) + 1):
        (x0, y0), (x1, y1) = rows[num - 2:num]
        if x1 == x0:
            continue
        slope = (y0 - y1) / (x0 - x1)
        slopes[x1] = y1 + (100.0 - x1) * slope

    common = [pct for pct, _ in rows if pct in reg and pct in slopes]
    if not common:
        raise ValueError("Rinko/DPRA cannot initialize")
    diffs = [abs(reg[pct]["reg_final"] - slopes[pct]) for pct in common]
    correction = st.mean(diffs)

    mcp = 90.0
    for pct in common:
        if reg[pct]["reg_slope"] == 0 and pct > 90 and pct != 100:
            mcp = pct
            break

    pct = common[-1]
    gamma = 1.0 - ((pct - mcp) / (100.0 - mcp))
    if gamma > gamma_threshold:
        gamma = gamma_threshold
    prediction = reg[pct]["reg_final"] + correction * gamma
    if not math.isfinite(prediction):
        raise ValueError("non-finite Rinko/DPRA prediction")
    return float(prediction)


def _point_at_or_before(history: list[dict], when: int) -> dict | None:
    candidates = [p for p in history if p["time"] <= when]
    return max(candidates, key=lambda p: p["time"]) if candidates else None


def _multitier_snapshot(tasks: dict[int, dict], tiers: list[int]) -> list[float]:
    """Scale-free current-event features shared across ranking tiers."""
    current = []
    features = []
    for tier in tiers:
        task = tasks[tier]
        last = task["history"][-1]
        current.append(last["ep"])
        for hours in (6, 12, 24):
            old = _point_at_or_before(task["history"], task["issued_at"] - hours * HOUR)
            if old is None:
                raise ValueError(f"missing T-{hours}h analog feature for tier {tier}")
            features.append((last["ep"] - old["ep"]) / max(last["ep"], 1.0))
    for a, b in zip(current, current[1:]):
        features.append(math.log((a + 1.0) / (b + 1.0)))
    return features


def _robust_feature_scales(rows: list[list[float]]) -> list[float]:
    columns = list(zip(*rows))
    scales = []
    for col in columns:
        center = st.median(col)
        mad = st.median(abs(x - center) for x in col)
        scales.append(max(1.4826 * mad, 1e-4))
    return scales


def _weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    if not values or len(values) != len(weights) or not 0 <= q <= 1:
        raise ValueError("invalid weighted quantile")
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total = sum(max(w, 0.0) for _, w in pairs)
    if total <= 0:
        raise ValueError("nonpositive ensemble weight")
    threshold = q * total
    running = 0.0
    for value, weight in pairs:
        running += max(weight, 0.0)
        if running >= threshold:
            return value
    return pairs[-1][0]


def _multitier_analog_components(public: dict, task: dict) -> tuple[list[float], list[float], dict]:
    if public["protocol"]["schema"] != "bandoribench-protocol-v2":
        raise ValueError("multitier analog ensemble requires protocol-v2")
    tiers = list(public["protocol"]["requested_tiers"])
    task_index = {(t["event_id"], t["horizon_hours"], t["tier"]): t for t in public["tasks"]}
    siblings = {}
    for tier in tiers:
        sibling = task_index.get((task["event_id"], task["horizon_hours"], tier))
        if sibling is None:
            raise ValueError(f"missing current sibling tier {tier}")
        siblings[tier] = sibling
    current_features = _multitier_snapshot(siblings, tiers)
    analogs = []
    for e in _walkforward_events(public, task):
        hist_tasks = {}
        try:
            for tier in tiers:
                hist_tasks[tier] = make_raw_task(e, str(tier), task["horizon_hours"], public["protocol"])
            features = _multitier_snapshot(hist_tasks, tiers)
        except (KeyError, ValueError):
            continue
        target_hist = hist_tasks[task["tier"]]
        current_ep = siblings[task["tier"]]["history"][-1]["ep"]
        progress = target_hist["history"][-1]["ep"] / e["tiers"][str(task["tier"])]["label"]["ep"]
        if not 0 < progress <= 1:
            continue
        candidate = max(current_ep, current_ep / progress)
        analogs.append((e["event_id"], features, candidate))
    if len(analogs) < 5:
        raise ValueError("fewer than 5 usable multi-tier analog events")
    scales = _robust_feature_scales([a[1] for a in analogs])
    ranked = []
    for event_id, features, candidate in analogs:
        distance = math.sqrt(sum(((a - b) / s) ** 2
                                 for a, b, s in zip(current_features, features, scales))
                             / len(scales))
        ranked.append((distance, event_id, candidate))
    ranked.sort()
    k = min(len(ranked), max(8, round(math.sqrt(len(ranked)) * 2)))
    selected = ranked[:k]
    distances = [r[0] for r in selected]
    local_scale = st.median(distances) if any(d > 0 for d in distances) else 1.0
    weights = [math.exp(-d / max(local_scale, 1e-6)) for d in distances]
    values = [r[2] for r in selected]
    meta = {"analog_count": k, "nearest_event_ids": [r[1] for r in selected],
            "nearest_distances": distances, "candidate_values": values}
    return values, weights, meta


def multitier_analog_ensemble(public: dict, task: dict) -> tuple[float, dict[str, float], dict]:
    """Causal analog ensemble using multi-tier growth shape, not future/current truth."""
    values, weights, meta = _multitier_analog_components(public, task)
    quantiles = {str(q): _weighted_quantile(values, weights, q) for q in QUANTILES}
    return quantiles["0.5"], quantiles, meta




def _care_sibling_tasks(public: dict, task: dict) -> dict[int, dict]:
    tiers = list(public["protocol"]["requested_tiers"])
    index = {(t["event_id"], t["horizon_hours"], t["tier"]): t for t in public["tasks"]}
    siblings = {}
    for tier in tiers:
        sibling = index.get((task["event_id"], task["horizon_hours"], tier))
        if sibling is None:
            raise ValueError(f"CARE-S missing sibling tier {tier}")
        siblings[tier] = sibling
    return siblings


def care_feature_dict(public: dict, task: dict, analog_meta: dict) -> dict[str, float]:
    siblings = _care_sibling_tasks(public, task)
    tiers = list(public["protocol"]["requested_tiers"])
    own = siblings[task["tier"]]
    last = own["history"][-1]
    duration = max(own["end_at"] - own["start_at"], 1)
    features = {
        "elapsed_frac": (own["issued_at"] - own["start_at"]) / duration,
        "remaining_frac": (own["end_at"] - own["issued_at"]) / duration,
        "input_age_hours": (own["issued_at"] - own["input_cutoff_at"]) / HOUR,
        "log_current_ep": math.log1p(last["ep"]),
        "log_duration_hours": math.log1p(duration / HOUR),
        f"event_type:{own['event_type']}": 1.0,
        f"era:{own['era']}": 1.0,
    }
    own_growth = {}
    for hours in (6, 12, 24):
        old = _point_at_or_before(own["history"], own["issued_at"] - hours * HOUR)
        if old is None:
            raise ValueError(f"CARE-S missing T-{hours}h feature")
        frac = (last["ep"] - old["ep"]) / max(last["ep"], 1.0)
        own_growth[hours] = frac
        features[f"own_growth_{hours}h"] = frac
    features["own_accel_6_vs_24"] = own_growth[6] - own_growth[24] / 4.0
    for i, value in enumerate(_multitier_snapshot(siblings, tiers)):
        features[f"multitier_{i}"] = value
    distances = analog_meta.get("nearest_distances", [])
    candidates = analog_meta.get("candidate_values", [])
    features["analog_count_log"] = math.log1p(float(analog_meta.get("analog_count", 0)))
    features["analog_nearest_distance"] = min(distances) if distances else 0.0
    features["analog_median_distance"] = st.median(distances) if distances else 0.0
    if candidates:
        med = max(st.median(candidates), 1.0)
        features["analog_relative_spread"] = (max(candidates) - min(candidates)) / med
    else:
        features["analog_relative_spread"] = 0.0
    features.update(legacy_reward_feature_dict(own))
    features.update(calendar_feature_dict(own, public.get("calendar")))
    return features


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    a = [list(row) + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular ridge system")
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor == 0:
                continue
            a[row] = [x - factor * y for x, y in zip(a[row], a[col])]
    return [a[i][-1] for i in range(n)]


def _ridge_fit(features: list[dict[str, float]], targets: list[float], l2: float = 4.0) -> dict:
    if len(features) != len(targets) or not features:
        raise ValueError("invalid CARE-S ridge sample")
    keys = sorted({key for row in features for key in row})
    means, scales = {}, {}
    for key in keys:
        values = [row.get(key, 0.0) for row in features]
        means[key] = st.mean(values)
        sigma = st.pstdev(values) if len(values) > 1 else 0.0
        scales[key] = sigma if sigma > 1e-9 else 1.0
    xrows = [[1.0] + [(row.get(key, 0.0) - means[key]) / scales[key] for key in keys]
             for row in features]
    width = len(keys) + 1
    gram = [[0.0] * width for _ in range(width)]
    rhs = [0.0] * width
    for x, y in zip(xrows, targets):
        for i in range(width):
            rhs[i] += x[i] * y
            for j in range(width):
                gram[i][j] += x[i] * x[j]
    for i in range(1, width):
        gram[i][i] += l2
    beta = _solve_linear_system(gram, rhs)
    return {"keys": keys, "means": means, "scales": scales, "beta": beta}


def _ridge_predict(model: dict, features: dict[str, float]) -> float:
    x = [1.0] + [(features.get(key, 0.0) - model["means"][key]) / model["scales"][key]
                 for key in model["keys"]]
    return sum(a * b for a, b in zip(x, model["beta"]))


def _care_historical_public(public: dict, event: dict, horizon: int,
                            prior_ids: list[int]) -> tuple[dict, dict]:
    tasks = []
    target = None
    for tier in public["protocol"]["requested_tiers"]:
        task = make_raw_task(event, str(tier), horizon, public["protocol"])
        task["history_event_ids"] = list(prior_ids)
        tasks.append(task)
    mini = {"protocol": public["protocol"], "reference_events": public["reference_events"],
            "tasks": tasks}
    if "calendar" in public:
        mini["calendar"] = public["calendar"]
    return mini, {task["tier"]: task for task in tasks}


def _care_training_samples(public: dict, task: dict, cache: dict) -> list[dict]:
    allowed = set(task.get("history_event_ids", []))
    ordered = [e for e in public["reference_events"] if e["event_id"] in allowed]
    samples = []
    for index, event in enumerate(ordered):
        prior_ids = [e["event_id"] for e in ordered[:index]]
        if len(prior_ids) < 5:
            continue
        key = (event["event_id"], task["tier"], task["horizon_hours"],
               tuple(prior_ids), digest(public.get("calendar")) if public.get("calendar") else None)
        sample = cache.get(key)
        if sample is None:
            try:
                mini, task_map = _care_historical_public(public, event, task["horizon_hours"], prior_ids)
                hist_task = task_map[task["tier"]]
                values, weights, meta = _multitier_analog_components(mini, hist_task)
                base = _weighted_quantile(values, weights, 0.5)
                final = event["tiers"][str(task["tier"])]["label"]["ep"]
                sample = {"event_id": event["event_id"],
                          "features": care_feature_dict(mini, hist_task, meta),
                          "target": math.log(max(final, 1.0) / max(base, 1.0)),
                          "base": base, "final": final,
                          "current_ep": hist_task["history"][-1]["ep"],
                          "analog_values": list(values),
                          "analog_weights": list(weights)}
            except (KeyError, ValueError):
                sample = False
            cache[key] = sample
        if sample:
            samples.append(sample)
    return samples


def _care_ridge_model(samples: list[dict], cache: dict) -> dict:
    key = ("care_ridge", tuple(row["event_id"] for row in samples))
    model = cache.get(key)
    if model is None:
        model = _ridge_fit([row["features"] for row in samples],
                           [row["target"] for row in samples])
        cache[key] = model
    return model


def _care_oos_records(samples: list[dict], cache: dict) -> list[dict]:
    key = ("care_oos_records", tuple(row["event_id"] for row in samples))
    cached = cache.get(key)
    if cached is not None:
        return cached
    records, prior_residuals = [], []
    for index in range(2, len(samples)):
        earlier = samples[:index]
        try:
            model = _care_ridge_model(earlier, cache)
            correction = _ridge_predict(model, samples[index]["features"])
        except ValueError:
            continue
        record = {"sample": samples[index], "correction": correction,
                  "prior_residuals": list(prior_residuals)}
        records.append(record)
        prior_residuals.append(samples[index]["target"] - correction)
    cache[key] = records
    return records


def _care_oos_residuals(samples: list[dict], cache: dict) -> list[float]:
    residuals = [record["sample"]["target"] - record["correction"]
                 for record in _care_oos_records(samples, cache)]
    if len(residuals) < 3 and samples:
        center = st.median(row["target"] for row in samples)
        residuals = [row["target"] - center for row in samples]
    return residuals or [0.0]


def _care_s2_tuning(samples: list[dict], cache: dict) -> tuple[float, float]:
    key = ("care_s2_tuning", tuple(row["event_id"] for row in samples))
    cached = cache.get(key)
    if cached is not None:
        return cached
    records = _care_oos_records(samples, cache)
    lambda_grid = (0.0, 0.25, 0.5, 0.75, 1.0)
    tau_grid = (0.0, 0.25, 0.5, 0.75, 1.0)
    if len(records) < 3:
        result = (0.0, 0.0)
        cache[key] = result
        return result

    def point_loss(lam: float) -> float:
        errors = []
        for record in records:
            sample, correction = record["sample"], record["correction"]
            adjustment = max(-2.0, min(2.0, lam * correction))
            pred = max(sample["current_ep"], sample["base"] * math.exp(adjustment))
            errors.append(abs(pred - sample["final"]))
        return st.mean(errors)

    lam = min(lambda_grid, key=lambda value: (point_loss(value), value))
    wis_scores = []
    for tau in tau_grid:
        losses = []
        for record in records:
            residuals = record["prior_residuals"]
            if len(residuals) < 3:
                continue
            sample, correction = record["sample"], record["correction"]
            values, weights = [], []
            for analog_value, analog_weight in zip(sample["analog_values"], sample["analog_weights"]):
                for residual in residuals:
                    adjustment = max(-2.0, min(2.0, lam * correction + tau * residual))
                    values.append(max(sample["current_ep"], analog_value * math.exp(adjustment)))
                    weights.append(analog_weight / len(residuals))
            quantiles = {str(q): _weighted_quantile(values, weights, q) for q in QUANTILES}
            losses.append(weighted_interval_score(quantiles, sample["final"]))
        if losses:
            wis_scores.append((st.mean(losses), tau))
    tau = min(wis_scores)[1] if wis_scores else 0.0
    result = (lam, tau)
    cache[key] = result
    return result


def care_s_forecast(public: dict, task: dict, cache: dict | None = None) -> tuple[float, dict[str, float], dict]:
    """CARE-S: analog ensemble + causal learned conditional correction + OOS residuals."""
    if public["protocol"]["schema"] != "bandoribench-protocol-v2":
        raise ValueError("CARE-S requires protocol-v2")
    cache = cache if cache is not None else {}
    values, weights, analog_meta = _multitier_analog_components(public, task)
    samples = _care_training_samples(public, task, cache)
    current_features = care_feature_dict(public, task, analog_meta)
    if len(samples) >= 2:
        model = _care_ridge_model(samples, cache)
        correction = _ridge_predict(model, current_features)
        feature_count = len(model["keys"])
    elif samples:
        correction = st.median(row["target"] for row in samples)
        feature_count = len(samples[0]["features"])
    else:
        correction, feature_count = 0.0, len(current_features)
    residuals = _care_oos_residuals(samples, cache)
    current_ep = task["history"][-1]["ep"]
    ensemble_values, ensemble_weights = [], []
    for value, weight in zip(values, weights):
        for residual in residuals:
            adjustment = max(-2.0, min(2.0, correction + residual))
            ensemble_values.append(max(current_ep, value * math.exp(adjustment)))
            ensemble_weights.append(weight / len(residuals))
    quantiles = {str(q): _weighted_quantile(ensemble_values, ensemble_weights, q) for q in QUANTILES}
    meta = {"training_events": len(samples), "oos_residuals": len(residuals),
            "feature_count": feature_count, "log_correction": correction,
            "analog_count": analog_meta["analog_count"],
            "calendar_sha256": digest(public["calendar"]) if "calendar" in public else None}
    return quantiles["0.5"], quantiles, meta



def care_s2_forecast(public: dict, task: dict, cache: dict | None = None) -> tuple[float, dict[str, float], dict]:
    """CARE-S2: causally tune correction shrinkage and residual spread on prior OOS forecasts."""
    if public["protocol"]["schema"] != "bandoribench-protocol-v2":
        raise ValueError("CARE-S2 requires protocol-v2")
    cache = cache if cache is not None else {}
    values, weights, analog_meta = _multitier_analog_components(public, task)
    samples = _care_training_samples(public, task, cache)
    current_features = care_feature_dict(public, task, analog_meta)
    if len(samples) >= 2:
        model = _care_ridge_model(samples, cache)
        raw_correction = _ridge_predict(model, current_features)
        feature_count = len(model["keys"])
    elif samples:
        raw_correction = st.median(row["target"] for row in samples)
        feature_count = len(samples[0]["features"])
    else:
        raw_correction, feature_count = 0.0, len(current_features)

    lam, tau = _care_s2_tuning(samples, cache)
    residuals = _care_oos_residuals(samples, cache)
    current_ep = task["history"][-1]["ep"]
    ensemble_values, ensemble_weights = [], []
    for value, weight in zip(values, weights):
        if tau == 0.0:
            adjustment = max(-2.0, min(2.0, lam * raw_correction))
            ensemble_values.append(max(current_ep, value * math.exp(adjustment)))
            ensemble_weights.append(weight)
            continue
        for residual in residuals:
            adjustment = max(-2.0, min(2.0, lam * raw_correction + tau * residual))
            ensemble_values.append(max(current_ep, value * math.exp(adjustment)))
            ensemble_weights.append(weight / len(residuals))
    quantiles = {str(q): _weighted_quantile(ensemble_values, ensemble_weights, q) for q in QUANTILES}
    meta = {"training_events": len(samples), "oos_residuals": len(residuals),
            "feature_count": feature_count, "raw_log_correction": raw_correction,
            "correction_shrinkage": lam, "residual_scale": tau,
            "analog_count": analog_meta["analog_count"],
            "calendar_sha256": digest(public["calendar"]) if "calendar" in public else None}
    return quantiles["0.5"], quantiles, meta




def _stack_component_record(public: dict, event: dict, task: dict,
                            prior_ids: list[int], cache: dict) -> dict | None:
    key = ("stack_components", event["event_id"], task["tier"], task["horizon_hours"], tuple(prior_ids))
    cached = cache.get(key)
    if cached is not None:
        return cached if cached is not False else None
    try:
        mini, task_map = _care_historical_public(public, event, task["horizon_hours"], prior_ids)
        hist_task = task_map[task["tier"]]
        analog_point, analog_q, analog_meta = multitier_analog_ensemble(mini, hist_task)
        bestdori = bestdori_recalibrated(hist_task, _walkforward_bestdori_rate(mini, hist_task))
        final = event["tiers"][str(task["tier"])]["label"]["ep"]
        row = {
            "event_id": event["event_id"],
            "analog_point": analog_point,
            "analog_quantiles": analog_q,
            "bestdori_point": bestdori,
            "final": final,
            "current_ep": hist_task["history"][-1]["ep"],
            "analog_meta": analog_meta,
        }
    except (KeyError, ValueError):
        cache[key] = False
        return None
    cache[key] = row
    return row


def _stack_training_records(public: dict, task: dict, cache: dict) -> list[dict]:
    allowed = set(task.get("history_event_ids", []))
    ordered = [e for e in public["reference_events"] if e["event_id"] in allowed]
    key = ("stack_training", task["tier"], task["horizon_hours"], tuple(e["event_id"] for e in ordered))
    cached = cache.get(key)
    if cached is not None:
        return cached
    records = []
    for index, event in enumerate(ordered):
        prior_ids = [e["event_id"] for e in ordered[:index]]
        if len(prior_ids) < 5:
            continue
        record = _stack_component_record(public, event, task, prior_ids, cache)
        if record is not None:
            records.append(record)
    cache[key] = records
    return records


def _l1_stack_weight(records: list[dict]) -> float:
    ratios, weights = [], []
    for row in records:
        delta = row["bestdori_point"] - row["analog_point"]
        if abs(delta) < 1e-9:
            continue
        ratio = (row["final"] - row["analog_point"]) / delta
        ratios.append(ratio)
        weights.append(abs(delta))
    if not ratios:
        return 0.0
    return min(1.0, max(0.0, _weighted_quantile(ratios, weights, 0.5)))


def _stack_point(record: dict, weight: float) -> float:
    return max(record["current_ep"],
               (1.0 - weight) * record["analog_point"] + weight * record["bestdori_point"])


def _stack_oos_records(records: list[dict], cache: dict) -> list[dict]:
    key = ("stack_oos", tuple(row["event_id"] for row in records))
    cached = cache.get(key)
    if cached is not None:
        return cached
    out = []
    for index in range(3, len(records)):
        earlier = records[:index]
        weight = _l1_stack_weight(earlier)
        row = records[index]
        point = _stack_point(row, weight)
        median = row["analog_quantiles"]["0.5"]
        offsets = {str(q): row["analog_quantiles"][str(q)] - median for q in QUANTILES}
        out.append({
            "event_id": row["event_id"], "weight": weight, "point": point,
            "offsets": offsets, "final": row["final"], "current_ep": row["current_ep"],
        })
    cache[key] = out
    return out


def _stack_spread_scale(records: list[dict], cache: dict) -> float:
    oos = _stack_oos_records(records, cache)
    if len(oos) < 3:
        return 1.0
    grid = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
    scored = []
    for scale in grid:
        losses = []
        for row in oos:
            quantiles = {
                str(q): max(row["current_ep"], row["point"] + scale * row["offsets"][str(q)])
                for q in QUANTILES
            }
            ordered = []
            for q in QUANTILES:
                value = quantiles[str(q)]
                if ordered:
                    value = max(value, ordered[-1])
                ordered.append(value)
            quantiles = {str(q): value for q, value in zip(QUANTILES, ordered)}
            losses.append(weighted_interval_score(quantiles, row["final"]))
        scored.append((st.mean(losses), abs(scale - 1.0), scale))
    return min(scored)[2]


def causal_stack_forecast(public: dict, task: dict, cache: dict | None = None) -> tuple[float, dict[str, float], dict]:
    """Causal stacking of analog and hierarchical Bestdori, tuned only on earlier OOS cases."""
    if public["protocol"]["schema"] != "bandoribench-protocol-v2":
        raise ValueError("causal-stack requires protocol-v2")
    cache = cache if cache is not None else {}
    analog_point, analog_q, analog_meta = multitier_analog_ensemble(public, task)
    bestdori = bestdori_recalibrated(task, _walkforward_bestdori_rate(public, task))
    current = {
        "analog_point": analog_point,
        "analog_quantiles": analog_q,
        "bestdori_point": bestdori,
        "current_ep": task["history"][-1]["ep"],
    }
    records = _stack_training_records(public, task, cache)
    weight = _l1_stack_weight(records)
    point = _stack_point(current, weight)
    scale = _stack_spread_scale(records, cache)
    median = analog_q["0.5"]
    quantiles = {str(q): max(current["current_ep"], point + scale * (analog_q[str(q)] - median))
                 for q in QUANTILES}
    ordered = []
    for q in QUANTILES:
        value = quantiles[str(q)]
        if ordered:
            value = max(value, ordered[-1])
        ordered.append(value)
    quantiles = {str(q): value for q, value in zip(QUANTILES, ordered)}
    meta = {
        "training_records": len(records),
        "bestdori_weight": weight,
        "analog_weight": 1.0 - weight,
        "spread_scale": scale,
        "analog_point": analog_point,
        "bestdori_point": bestdori,
        "analog_count": analog_meta["analog_count"],
    }
    return quantiles["0.5"], quantiles, meta


def _isotonic_nonincreasing(values: list[float]) -> list[float]:
    if not values:
        return []
    blocks = [{"sum": -float(value), "weight": 1, "count": 1} for value in values]
    i = 0
    while i < len(blocks) - 1:
        left = blocks[i]["sum"] / blocks[i]["weight"]
        right = blocks[i + 1]["sum"] / blocks[i + 1]["weight"]
        if left <= right:
            i += 1
            continue
        blocks[i:i + 2] = [{"sum": blocks[i]["sum"] + blocks[i + 1]["sum"],
                            "weight": blocks[i]["weight"] + blocks[i + 1]["weight"],
                            "count": blocks[i]["count"] + blocks[i + 1]["count"]}]
        i = max(0, i - 1)
    out = []
    for block in blocks:
        value = -(block["sum"] / block["weight"])
        out.extend([value] * block["count"])
    return out


def _enforce_care_tier_order(public: dict, predictions: list[dict]) -> None:
    task_by_case = {task["case_id"]: task for task in public["tasks"]}
    pred_by_case = {row["case_id"]: row for row in predictions if not row.get("error")}
    groups = defaultdict(list)
    for task in public["tasks"]:
        if task["case_id"] in pred_by_case:
            groups[(task["event_id"], task["horizon_hours"])].append(task)
    for tasks in groups.values():
        tasks.sort(key=lambda task: task["tier"])
        for q in QUANTILES:
            key = str(q)
            values = [max(pred_by_case[t["case_id"]]["quantiles"][key], t["history"][-1]["ep"])
                      for t in tasks]
            projected = _isotonic_nonincreasing(values)
            for task, value in zip(tasks, projected):
                pred_by_case[task["case_id"]]["quantiles"][key] = value
        for task in tasks:
            row = pred_by_case[task["case_id"]]
            ordered = []
            floor = task["history"][-1]["ep"]
            for q in QUANTILES:
                value = max(floor, row["quantiles"][str(q)])
                if ordered:
                    value = max(value, ordered[-1])
                ordered.append(value)
            row["quantiles"] = {str(q): value for q, value in zip(QUANTILES, ordered)}
            row["prediction"] = row["quantiles"]["0.5"]
            if "care" in row:
                row["care"]["tier_order_constraint"] = True
            if "stack" in row:
                row["stack"]["tier_order_constraint"] = True

def weighted_interval_score(quantiles: dict, y: float) -> float:
    y = number(y, "outcome")
    q = {float(k): number(v, "quantile") for k, v in quantiles.items()}
    if set(q) != set(QUANTILES) or len(quantiles) != len(QUANTILES):
        raise ValueError(f"exact quantiles required: {QUANTILES}")
    if any(q[a] > q[b] for a, b in zip(QUANTILES, QUANTILES[1:])):
        raise ValueError("crossing quantiles")
    total = 0.5 * abs(q[0.5] - y)
    for alpha, low, high in ((0.5, 0.25, 0.75), (0.2, 0.1, 0.9), (0.1, 0.05, 0.95)):
        lo, hi = q[low], q[high]
        interval = hi - lo + 2 / alpha * (max(lo - y, 0) + max(y - hi, 0))
        total += alpha / 2 * interval
    return total / 3.5


def macro(rows: list[dict]) -> float:
    cells: dict[tuple, list[float]] = defaultdict(list)
    for row in rows:
        cells[(row["era"], row["tier"], row["horizon_hours"])].append(row["scaled_loss"])
    if not cells:
        raise ValueError("no scored cases")
    return st.mean(st.mean(v) for v in cells.values())


def score_transform(loss: float) -> float:
    return 100.0 / (1.0 + loss)


def _phase_regime_shift_diagnostics(report: dict, plan: dict, phase: str) -> dict | None:
    if phase != "final" or not report.get("eligible"):
        return None
    regime = plan["regime_shift"]
    if not regime.get("unseen_target_eras"):
        return None
    rows = list(report.get("case_losses", []))
    first_shift = regime.get("first_unseen_regime_event_id")
    diag = {
        "role": regime["final_role"],
        "source_era_counts": regime["source_era_counts"],
        "target_era_counts": regime["target_era_counts"],
        "unseen_target_eras": regime["unseen_target_eras"],
        "first_new_regime_event_id": first_shift,
        "overall_regime_shift_score": report["score"],
    }
    if first_shift is not None:
        final_order = list(plan["phases"]["final"]["target_event_ids"])
        first_index = final_order.index(first_shift)
        first_rows = [row for row in rows if row["event_id"] == first_shift]
        post_ids = final_order[first_index + 1:]
        post_set = set(post_ids)
        post_rows = [row for row in rows if row["event_id"] in post_set]
        diag["first_new_regime_event_score"] = (
            score_transform(macro(first_rows)) if first_rows else None
        )
        diag["post_adaptation_event_ids"] = post_ids
        diag["post_adaptation_score"] = (
            score_transform(macro(post_rows)) if post_rows else None
        )
        diag["note"] = (
            "The first-new-regime score measures zero-shot transfer. "
            "Post-adaptation covers later final events after the normal prequential "
            "observe_event update has exposed earlier final truth."
        )
    return diag


def evaluate(bundle: dict, submission: dict, track: str = "point", bootstrap: int = 200,
             case_ids: set[str] | None = None) -> dict:
    verify_bundle(bundle)
    if submission.get("benchmark_id") != bundle["benchmark_id"]:
        raise ValueError("submission targets a different benchmark")
    if track not in ("point", "probabilistic") or bootstrap < 0:
        raise ValueError("invalid evaluation options")
    all_expected = {t["case_id"]: t for t in bundle["tasks"]}
    if case_ids is None:
        expected = all_expected
    else:
        unknown = set(case_ids) - set(all_expected)
        if unknown:
            raise ValueError(f"evaluation view contains unknown cases: {sorted(unknown)[:3]}")
        expected = {key: all_expected[key] for key in all_expected if key in case_ids}
        if not expected:
            raise ValueError("evaluation view has no cases")
    submitted = {}
    for row in submission["predictions"]:
        key = row["case_id"]
        if key in submitted or key not in expected:
            raise ValueError(f"duplicate or unknown case: {key}")
        submitted[key] = row
    rows, failures = [], []
    for key, task in expected.items():
        pred = submitted.get(key)
        try:
            if pred is None or pred.get("error"):
                raise ValueError("missing prediction" if pred is None else str(pred["error"]))
            y = bundle["truth"][key]["ep"]
            p = number(pred["prediction"], "point prediction")
            if track == "probabilistic":
                loss = weighted_interval_score(pred["quantiles"], y)
                q = {float(k): v for k, v in pred["quantiles"].items()}
                if p != q[0.5]:
                    raise ValueError("probabilistic point prediction must equal median")
            else:
                loss = abs(p - y)
            scale = bundle["scales"][scale_key(task)]
            if not math.isfinite(loss) or not math.isfinite(loss / scale):
                raise ValueError("numerical overflow in forecast loss")
            result = {k: task[k] for k in ("case_id", "server", "event_id", "era", "tier", "horizon_hours")}
            result.update(loss=loss, scaled_loss=loss / scale, bias=p - y,
                          below_current=p < task["history"][-1]["ep"])
            if track == "probabilistic":
                result["covered50"] = q[0.25] <= y <= q[0.75]
                result["covered90"] = q[0.05] <= y <= q[0.95]
                result["width50"] = q[0.75] - q[0.25]
                result["width90"] = q[0.95] - q[0.05]
            rows.append(result)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append({"case_id": key, "reason": str(exc)})
    eligible = not failures
    report = {"benchmark_id": bundle["benchmark_id"], "software_version": VERSION,
              "model_id": submission.get("model_id"), "track": track,
              "synthetic": bundle["protocol"]["synthetic"], "eligible": eligible,
              "score": None, "coverage": len(rows) / len(expected), "n_expected": len(expected),
              "n_scored": len(rows),
              "n_events_expected": len({t["event_id"] for t in expected.values()}),
              "n_events_scored": len({row["event_id"] for row in rows}),
              "failures": failures, "dataset_exclusions": bundle["exclusions"],
              "knowledge_time": bundle["protocol"]["knowledge_time"],
              "evaluation_subset": case_ids is not None}
    # No flattering partial score: full case coverage is required on each track.
    if not eligible:
        return report
    loss = macro(rows)
    report.update(score=score_transform(loss), macro_scaled_loss=loss,
                  below_current_count=sum(r["below_current"] for r in rows),
                  median_bias=st.median(r["bias"] for r in rows))
    for dimension in ("horizon_hours", "tier", "era"):
        report["by_" + dimension] = {str(value): score_transform(macro([r for r in rows if r[dimension] == value]))
                                      for value in sorted({r[dimension] for r in rows})}
    if track == "probabilistic":
        report["coverage50"] = st.mean(r["covered50"] for r in rows)
        report["coverage90"] = st.mean(r["covered90"] for r in rows)
        report["mean_interval_width50"] = st.mean(r["width50"] for r in rows)
        report["mean_interval_width90"] = st.mean(r["width90"] for r in rows)
    # Whole-event bootstrap, stratified by era; calibration is held fixed.
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[row["era"]][row["event_id"]].append(row)
    if bootstrap and all(len(group) >= 2 for group in groups.values()):
        rng, scores = random.Random(20260926), []
        for _ in range(bootstrap):
            sample = []
            for group in groups.values():
                ids = sorted(group)
                for event_id in rng.choices(ids, k=len(ids)):
                    sample.extend(group[event_id])
            scores.append(score_transform(macro(sample)))
        report["event_bootstrap_95_interval"] = [percentile(scores, 0.025), percentile(scores, 0.975)]
    report["case_losses"] = rows
    return report



def _balanced_blocks(values: list[int], count: int) -> list[list[int]]:
    if count <= 0:
        raise ValueError("block count must be positive")
    if not values:
        return []
    count = min(count, len(values))
    base, extra = divmod(len(values), count)
    blocks, cursor = [], 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        blocks.append(values[cursor:cursor + size])
        cursor += size
    return blocks


def _benchmark_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_dir():
        p = p / "private" / "benchmark.json"
    if not p.is_file():
        raise ValueError(f"benchmark not found: {p}")
    return p


def model_evaluation_plan(bundle: dict, development_blocks: int = 5) -> dict:
    """Deterministic chronological dev/selection/final split for model work."""
    verify_bundle(bundle)
    if bundle["protocol"].get("schema") != "bandoribench-protocol-v2":
        raise ValueError("model API requires protocol-v2")
    target_ids = list(bundle["protocol"]["target_event_ids"])
    if len(target_ids) < 1:
        raise ValueError("model API needs at least 1 target event")
    is_devkit = bool(bundle["protocol"].get("model_devkit"))
    if is_devkit:
        development = target_ids
        selection = []
        final = []
    else:
        if len(target_ids) < 3:
            raise ValueError("model API needs at least 3 target events")
        n_selection = max(1, round(len(target_ids) * 0.15))
        n_final = max(1, round(len(target_ids) * 0.15))
        while n_selection + n_final >= len(target_ids):
            if n_selection >= n_final and n_selection > 1:
                n_selection -= 1
            elif n_final > 1:
                n_final -= 1
            else:
                raise ValueError("not enough target events for three model-evaluation phases")
        n_development = len(target_ids) - n_selection - n_final
        development = target_ids[:n_development]
        selection = target_ids[n_development:n_development + n_selection]
        final = target_ids[n_development + n_selection:]
    warmup = list(bundle["protocol"]["warmup_event_ids"])
    ref = {e["event_id"]: e for e in bundle["reference_events"]}
    task_ids_by_event = defaultdict(list)
    for task in bundle["tasks"]:
        task_ids_by_event[task["event_id"]].append(task["case_id"])

    def era_counts(event_ids: list[int]) -> dict[str, int]:
        counts = defaultdict(int)
        for eid in event_ids:
            counts[str(ref[eid].get("era", "unknown"))] += 1
        return dict(sorted(counts.items()))

    def make_phase(name: str, targets: list[int], initial_training: list[int],
                   block_count: int) -> dict:
        missing = [eid for eid in initial_training + targets if eid not in ref]
        if missing:
            raise ValueError(f"evaluation plan references missing events: {missing[:3]}")
        cases = [case_id for eid in targets for case_id in task_ids_by_event.get(eid, [])]
        scored_events = [eid for eid in targets if task_ids_by_event.get(eid)]
        cutoff = max((ref[eid]["end_at"] for eid in initial_training), default=0)
        return {
            "name": name,
            "target_event_ids": targets,
            "scored_event_ids": scored_events,
            "case_ids": cases,
            "initial_training_event_ids": initial_training,
            "initial_training_cutoff_ms": cutoff,
            "target_era_counts": era_counts(targets),
            "initial_training_era_counts": era_counts(initial_training),
            "blocks": _balanced_blocks(targets, block_count),
        }

    phases = {
        "development": make_phase("development", development, warmup,
                                  min(development_blocks, len(development))),
        "selection": make_phase("selection", selection, warmup + development, 1),
        "final": make_phase("final", final, warmup + development + selection, 1),
        "all": make_phase("all", target_ids, warmup, min(development_blocks, len(target_ids))),
    }

    final_training_ids = phases["final"]["initial_training_event_ids"]
    known_eras = set(era_counts(final_training_ids))
    final_eras = set(era_counts(final))
    unseen_final_eras = sorted(final_eras - known_eras)
    first_unseen_event_id = next(
        (eid for eid in final if str(ref[eid].get("era", "unknown")) in unseen_final_eras),
        None,
    )
    if not final:
        final_role = "not_exposed"
    elif unseen_final_eras and final_eras.isdisjoint(known_eras):
        final_role = "regime_shift_challenge"
    elif unseen_final_eras:
        final_role = "mixed_regime_shift_holdout"
    else:
        final_role = "same_regime_tail_holdout"
    regime_shift = {
        "final_role": final_role,
        "source_era_counts": era_counts(final_training_ids),
        "target_era_counts": era_counts(final),
        "unseen_target_eras": unseen_final_eras,
        "first_unseen_regime_event_id": first_unseen_event_id,
        "prequential_adaptation": bool(first_unseen_event_id),
    }

    body = {
        "schema": "bandoribench-model-eval-plan-v1",
        "api_version": MODEL_API_VERSION,
        "benchmark_id": bundle["benchmark_id"],
        "policy": {
            "split": ("development-only exposed devkit" if is_devkit
                      else "chronological_70_15_15_by_target_event"),
            "development": "tune architecture/hyperparameters; detailed diagnostics allowed",
            "selection": "choose among already-developed candidates; aggregate output only",
            "final": ("one-shot regime-shift challenge when final eras are unseen; "
                      "otherwise one-shot chronological tail holdout"),
            "all": "prequential research diagnostic; do not use as the sole model-selection target",
        },
        "regime_shift": regime_shift,
        "phases": phases,
    }
    body["plan_id"] = digest(body)
    return body


def model_development_bundle(bundle: dict, development_blocks: int = 5) -> dict:
    """Create a self-contained tuning bundle with no selection/final events or truth."""
    verify_bundle(bundle)
    plan = model_evaluation_plan(bundle, development_blocks)
    development = plan["phases"]["development"]
    warmup_ids = list(bundle["protocol"]["warmup_event_ids"])
    target_ids = list(development["target_event_ids"])
    allowed_events = set(warmup_ids + target_ids)
    allowed_cases = set(development["case_ids"])

    protocol = dict(bundle["protocol"])
    protocol["software_version"] = VERSION
    protocol["parent_benchmark_id"] = bundle["benchmark_id"]
    protocol["model_devkit"] = True
    protocol["warmup_event_ids"] = warmup_ids
    protocol["target_event_ids"] = target_ids
    protocol["eligible_target_event_ids"] = [
        eid for eid in target_ids
        if any(task["event_id"] == eid and task["case_id"] in allowed_cases for task in bundle["tasks"])
    ]
    protocol["fully_excluded_target_event_ids"] = [
        eid for eid in target_ids if eid not in protocol["eligible_target_event_ids"]
    ]
    protocol["target_case_count_before_eligibility"] = (
        len(target_ids) * len(protocol["requested_tiers"]) * len(protocol["horizons"])
    )
    tasks = [task for task in bundle["tasks"] if task["case_id"] in allowed_cases]
    truth = {case_id: bundle["truth"][case_id] for case_id in allowed_cases}
    protocol["eligible_case_count"] = len(tasks)
    protocol["excluded_case_count"] = protocol["target_case_count_before_eligibility"] - len(tasks)
    exclusions = [
        row for row in bundle["exclusions"]
        if row.get("stage") == "warmup_scale" or row.get("event") in allowed_events
    ]
    reason_counts = defaultdict(int)
    for row in exclusions:
        if row.get("stage") == "hindcast":
            reason_counts[row["reason"]] += 1
    protocol["excluded_cases_by_reason"] = dict(sorted(reason_counts.items()))
    reference_events = [
        event for event in bundle["reference_events"] if event["event_id"] in allowed_events
    ]

    body = {
        "protocol": protocol,
        "scales": bundle["scales"],
        "tasks": tasks,
        "truth": truth,
        "reference_events": reference_events,
        "exclusions": exclusions,
        "devkit": {
            "schema": "bandoribench-model-devkit-v1",
            "parent_benchmark_id": bundle["benchmark_id"],
            "parent_plan_id": plan["plan_id"],
            "exposed_phase": "development",
            "safe_to_share_for_tuning": True,
            "selection_final_events_included": False,
        },
    }
    if "calendar" in bundle:
        body["calendar"] = bundle["calendar"]
    body["benchmark_id"] = digest(body)
    return body


def model_training_export(bundle: dict, phase: str,
                          development_blocks: int = 5) -> dict:
    if phase not in MODEL_PHASES:
        raise ValueError("unknown model-evaluation phase")
    plan = model_evaluation_plan(bundle, development_blocks)
    info = plan["phases"][phase]
    allowed = set(info["initial_training_event_ids"])
    events = [e for e in bundle["reference_events"] if e["event_id"] in allowed]
    return {
        "schema": "bandoribench-model-training-v1",
        "api_version": MODEL_API_VERSION,
        "benchmark_id": bundle["benchmark_id"],
        "plan_id": plan["plan_id"],
        "phase": phase,
        "training_cutoff_ms": info["initial_training_cutoff_ms"],
        "training_event_ids": info["initial_training_event_ids"],
        "protocol": {
            "server": events[0]["server"] if events else None,
            "requested_tiers": bundle["protocol"]["requested_tiers"],
            "horizons": bundle["protocol"]["horizons"],
            "knowledge_time": bundle["protocol"]["knowledge_time"],
        },
        "calendar": bundle.get("calendar"),
        "events": events,
    }


def _model_exchange(proc: subprocess.Popen, message: dict, expected_type: str) -> dict:
    if proc.stdin is None or proc.stdout is None:
        raise ValueError("model runner pipes are unavailable")
    try:
        proc.stdin.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
        proc.stdin.flush()
    except BrokenPipeError as exc:
        raise ValueError(f"model runner exited before {expected_type}") from exc
    line = proc.stdout.readline()
    if not line:
        code = proc.poll()
        raise ValueError(f"model runner closed stdout before {expected_type}; exit={code}")
    try:
        response = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model runner stdout is not JSON: {line[:200]!r}") from exc
    if not isinstance(response, dict):
        raise ValueError("model runner response must be a JSON object")
    if response.get("type") == "error":
        raise ValueError(f"model runner error: {response.get('message', 'unspecified')}")
    if response.get("type") != expected_type:
        raise ValueError(f"model runner returned {response.get('type')!r}; expected {expected_type!r}")
    return response


def _temporal_diagnostics(rows: list[dict], blocks: list[list[int]]) -> list[dict]:
    out = []
    cumulative_ids: set[int] = set()
    for index, event_ids in enumerate(blocks, start=1):
        event_set = set(event_ids)
        block_rows = [row for row in rows if row["event_id"] in event_set]
        cumulative_ids.update(event_ids)
        cumulative_rows = [row for row in rows if row["event_id"] in cumulative_ids]
        block_loss = macro(block_rows) if block_rows else None
        cumulative_loss = macro(cumulative_rows) if cumulative_rows else None
        out.append({
            "block": index,
            "event_ids": event_ids,
            "n_cases": len(block_rows),
            "score": score_transform(block_loss) if block_loss is not None else None,
            "macro_scaled_loss": block_loss,
            "cumulative_score": score_transform(cumulative_loss) if cumulative_loss is not None else None,
        })
    return out


def run_model_api(bundle: dict, runner: list[str], phase: str = "development",
                  track: str = "point", bootstrap: int = 200,
                  development_blocks: int = 5) -> tuple[dict, dict]:
    """Run an external persistent model process without exposing future/current truth."""
    if phase not in MODEL_PHASES:
        raise ValueError("unknown model-evaluation phase")
    if track not in ("point", "probabilistic"):
        raise ValueError("invalid model-evaluation track")
    runner = list(runner)
    if runner and runner[0] == "--":
        runner = runner[1:]
    if not runner:
        raise ValueError("model-eval requires a runner command after --")
    plan = model_evaluation_plan(bundle, development_blocks)
    info = plan["phases"][phase]
    if not info["case_ids"]:
        raise ValueError(f"model-evaluation phase {phase} has no scored cases")
    reference = {e["event_id"]: e for e in bundle["reference_events"]}
    tasks_by_event_horizon: dict[tuple[int, int], list[dict]] = defaultdict(list)
    selected_cases = set(info["case_ids"])
    for task in bundle["tasks"]:
        if task["case_id"] in selected_cases:
            tasks_by_event_horizon[(task["event_id"], task["horizon_hours"])].append(task)
    for panel in tasks_by_event_horizon.values():
        panel.sort(key=lambda task: task["tier"])

    proc = subprocess.Popen(runner, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1)
    predictions = []
    ready = None
    try:
        init = {
            "type": "init",
            "api_version": MODEL_API_VERSION,
            "context": {
                "benchmark_id": bundle["benchmark_id"],
                "plan_id": plan["plan_id"],
                "phase": phase,
                "track": track,
                "server": bundle["reference_events"][0]["server"],
                "requested_tiers": bundle["protocol"]["requested_tiers"],
                "horizons": bundle["protocol"]["horizons"],
                "quantiles": list(QUANTILES),
                "calendar": bundle.get("calendar"),
                "initial_training_cutoff_ms": info["initial_training_cutoff_ms"],
            },
        }
        ready = _model_exchange(proc, init, "ready")
        if ready.get("api_version") != MODEL_API_VERSION:
            raise ValueError("model runner API version mismatch")
        model_id = ready.get("model_id")
        model_version = ready.get("model_version")
        if not isinstance(model_id, str) or not model_id or not isinstance(model_version, str) or not model_version:
            raise ValueError("model runner must declare non-empty model_id and model_version")

        def observe(event_id: int) -> None:
            response = _model_exchange(
                proc,
                {"type": "observe_event", "event": reference[event_id]},
                "observed",
            )
            if response.get("event_id") != event_id:
                raise ValueError("model runner acknowledged the wrong observed event")

        declared_initial_cutoff = ready.get("training_cutoff_ms")
        replay_cutoff = (declared_initial_cutoff
                         if isinstance(declared_initial_cutoff, int)
                         and not isinstance(declared_initial_cutoff, bool)
                         and declared_initial_cutoff >= 0
                         else 0)
        for event_id in info["initial_training_event_ids"]:
            if reference[event_id]["end_at"] > replay_cutoff:
                observe(event_id)

        for event_id in info["target_event_ids"]:
            for horizon in bundle["protocol"]["horizons"]:
                panel_tasks = tasks_by_event_horizon.get((event_id, horizon), [])
                if not panel_tasks:
                    continue
                panel_id = f"{reference[event_id]['server']}:{event_id}:{horizon}"
                response = _model_exchange(proc, {
                    "type": "forecast_panel",
                    "panel": {
                        "panel_id": panel_id,
                        "event_id": event_id,
                        "horizon_hours": horizon,
                        "tasks": panel_tasks,
                    },
                }, "forecast")
                if response.get("panel_id") != panel_id:
                    raise ValueError("model runner returned forecast for the wrong panel")
                rows = response.get("predictions")
                if not isinstance(rows, list):
                    raise ValueError("model runner predictions must be an array")
                expected_ids = {task["case_id"] for task in panel_tasks}
                returned_ids = {row.get("case_id") for row in rows if isinstance(row, dict)}
                if len(rows) != len(expected_ids) or returned_ids != expected_ids:
                    raise ValueError(f"model runner case IDs do not match panel {panel_id}")
                predictions.extend(rows)
            observe(event_id)
        _model_exchange(proc, {"type": "finish"}, "finished")
    finally:
        if proc.stdin:
            proc.stdin.close()
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    if ready is None:
        raise ValueError("model runner did not initialize")
    submission = {
        "benchmark_id": bundle["benchmark_id"],
        "model_id": ready["model_id"],
        "model_version": ready["model_version"],
        "provenance": "bandoribench_model_api_v1",
        "evaluation_phase": phase,
        "predictions": predictions,
    }
    report = evaluate(bundle, submission, track, bootstrap, selected_cases)
    declared_cutoff = ready.get("training_cutoff_ms")
    declaration_valid = (
        isinstance(declared_cutoff, int) and not isinstance(declared_cutoff, bool)
        and declared_cutoff >= 0
        and declared_cutoff <= info["initial_training_cutoff_ms"]
    )
    rows = list(report.get("case_losses", []))
    diagnostics = _temporal_diagnostics(rows, info["blocks"]) if report["eligible"] else []
    report["model_api"] = {
        "api_version": MODEL_API_VERSION,
        "plan_id": plan["plan_id"],
        "phase": phase,
        "model_id": ready["model_id"],
        "model_version": ready["model_version"],
        "declared_initial_training_cutoff_ms": declared_cutoff,
        "allowed_initial_training_cutoff_ms": info["initial_training_cutoff_ms"],
        "training_declaration_ok": declaration_valid,
        "protocol_eligible": bool(report["eligible"] and declaration_valid),
        "supports_online_update": bool(ready.get("supports_online_update", False)),
        "note": ("training_cutoff_ms means the latest BandoriBench event label included in the initial "
                 "checkpoint; 0 means no benchmark labels. This is a declaration, not a sandbox proof."),
    }
    report["temporal_checkpoints"] = diagnostics
    report["evaluation_plan_policy"] = plan["policy"]
    report["evaluation_role"] = (
        plan["regime_shift"]["final_role"] if phase == "final" else phase
    )

    shift_diag = _phase_regime_shift_diagnostics(report, plan, phase)
    if shift_diag is not None:
        report["regime_shift_diagnostics"] = shift_diag

    if phase in ("selection", "final"):
        report.pop("case_losses", None)
        for key in ("by_horizon_hours", "by_tier", "by_era", "below_current_count",
                    "median_bias", "coverage50", "coverage90",
                    "mean_interval_width50", "mean_interval_width90"):
            report.pop(key, None)
        report["temporal_checkpoints"] = []
    if phase == "selection":
        report["selection_holdout"] = True
    elif phase == "final":
        report["final_holdout"] = True
    elif phase == "all":
        report["research_diagnostic_only"] = True
    return submission, report


def baseline_registry() -> dict:
    return {
        name: {
            **meta,
            "tracks": list(meta["tracks"]),
        }
        for name, meta in BASELINE_REGISTRY.items()
    }


def baseline_suite(bundle: dict, bootstrap: int = 200,
                   models: list[str] | None = None) -> dict:
    """Run the frozen built-in baseline/model registry across every evaluation phase."""
    verify_bundle(bundle)
    if bundle["protocol"].get("schema") != "bandoribench-protocol-v2":
        raise ValueError("baseline-suite requires protocol-v2")
    selected = list(BASELINE_REGISTRY) if models is None else list(models)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("baseline-suite models must be a non-empty unique list")
    unknown = [name for name in selected if name not in BASELINE_REGISTRY]
    if unknown:
        raise ValueError(f"unknown baseline-suite models: {unknown}")
    if bootstrap < 0:
        raise ValueError("invalid bootstrap count")

    plan = model_evaluation_plan(bundle)
    public = public_bundle(bundle)
    detailed_reports = {}
    rows = []
    for model in selected:
        submission = predict(public, model)
        model_reports = {}
        meta = BASELINE_REGISTRY[model]
        for phase in MODEL_PHASES:
            info = plan["phases"][phase]
            if not info["case_ids"]:
                continue
            phase_reports = {}
            selected_cases = set(info["case_ids"])
            phase_submission = {
                **submission,
                "predictions": [
                    row for row in submission["predictions"]
                    if row.get("case_id") in selected_cases
                ],
            }
            for track in meta["tracks"]:
                report = evaluate(bundle, phase_submission, track, bootstrap, selected_cases)
                report["evaluation_phase"] = phase
                report["evaluation_role"] = (
                    plan["regime_shift"]["final_role"] if phase == "final" else phase
                )
                shift_diag = _phase_regime_shift_diagnostics(report, plan, phase)
                if shift_diag is not None:
                    report["regime_shift_diagnostics"] = shift_diag
                phase_reports[track] = report
                rows.append({
                    "model_id": model,
                    "model_class": meta["class"],
                    "source_status": meta["source_status"],
                    "phase": phase,
                    "evaluation_role": report["evaluation_role"],
                    "track": track,
                    "eligible": report["eligible"],
                    "score": report["score"],
                    "coverage": report["coverage"],
                    "n_cases": report["n_expected"],
                    "n_events": report["n_events_expected"],
                    "event_bootstrap_95_interval": report.get("event_bootstrap_95_interval"),
                    "by_horizon_hours": report.get("by_horizon_hours"),
                    "by_tier": report.get("by_tier"),
                    "by_era": report.get("by_era"),
                    "regime_shift_diagnostics": report.get("regime_shift_diagnostics"),
                })
            model_reports[phase] = phase_reports
        detailed_reports[model] = model_reports

    body = {
        "schema": "bandoribench-baseline-suite-v1",
        "software_version": VERSION,
        "benchmark_id": bundle["benchmark_id"],
        "registry": baseline_registry(),
        "evaluation_plan": plan,
        "bootstrap": bootstrap,
        "summary": rows,
        "reports": detailed_reports,
    }
    body["suite_id"] = digest(body)
    return body


def server_value(meta: dict, field: str, server: str) -> int:
    value = meta.get(field)
    if isinstance(value, list):
        value = value[SERVERS[server]] if len(value) > SERVERS[server] else None
    if value is None:
        raise ValueError(f"missing {field} for {server}; supply an explicit window override")
    return integer(int(value), field, 1)


def archive_cutoff(archives: dict, event_id: int, server: str, tier: int) -> float | None:
    """Read Bestdori's archived final cutoff for one fixed tier."""
    event = archives.get(str(event_id))
    if not isinstance(event, dict):
        return None
    cutoff = event.get("cutoff")
    index = SERVERS[server]
    if not isinstance(cutoff, list) or len(cutoff) <= index or not isinstance(cutoff[index], dict):
        return None
    value = cutoff[index].get(str(tier))
    if value is None:
        return None
    return number(value, f"archive cutoff T{tier}", 1)


class PublicClient:
    def __init__(self, directory: Path, delay: float = 0.5):
        self.directory, self.delay = directory, delay
        self.requests: list[dict] = []

    def get(self, url: str) -> Any:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "BandoriBench/" + VERSION})
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise ValueError("API response exceeds 20 MB")
                payload = json.loads(raw)
                sha = hashlib.sha256(raw).hexdigest()
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / (sha + ".json")).write_bytes(raw)
                self.requests.append({"url": url, "retrieved_at": int(time.time() * 1000), "sha256": sha})
                time.sleep(self.delay)
                return payload
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2 or (isinstance(exc, urllib.error.HTTPError) and exc.code not in (429, 500, 502, 503, 504)):
                    self.requests.append({"url": url, "error": str(exc)})
                    raise
                retry = exc.headers.get("Retry-After") if isinstance(exc, urllib.error.HTTPError) else None
                time.sleep(min(120, float(retry)) if retry and retry.isdigit() else 2 ** (attempt + 1))
        raise RuntimeError("unreachable")


def collect(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = PublicClient(out / "raw", args.delay)
    audit, events = [], []
    labels = load(args.labels) if args.labels else {}
    windows = load(args.windows) if args.windows else {}
    try:
        index = client.get("https://bestdori.com/api/events/all.3.json")
        if not isinstance(index, dict):
            raise ValueError("event index must be a dictionary")
        archives = client.get("https://bestdori.com/api/archives/all.5.json")
        if not isinstance(archives, dict):
            raise ValueError("event archive index must be a dictionary")
        transition = server_value(index["310"], "startAt", "cn") if args.server == "cn" and "310" in index else None
        candidates = []
        for eid, meta in index.items():
            try:
                start = server_value(meta, "startAt", args.server)
                if start < int(time.time() * 1000):
                    candidates.append((start, int(eid)))
            except (ValueError, TypeError):
                continue
        candidates.sort(reverse=True)  # Sort timestamps, not IDs.
        attempted = 0
        for _, eid in candidates:
            if attempted >= args.recent:
                break
            try:
                meta = client.get(f"https://bestdori.com/api/events/{eid}.json")
                override = windows.get(f"{args.server}:{eid}")
                start = integer(override["start_at"], "override start") if override else server_value(meta, "startAt", args.server)
                stop = integer(override["end_at"], "override stop") if override else server_value(meta, "endAt", args.server)
                aggregate_end = (
                    integer(override["aggregate_end_at"], "override aggregate_end_at")
                    if override and override.get("aggregate_end_at") is not None
                    else server_value(meta, "aggregateEndAt", args.server)
                )
                if aggregate_end < stop:
                    raise ValueError("aggregateEndAt predates endAt")
                if aggregate_end >= int(time.time() * 1000) - args.settle_hours * HOUR:
                    continue
                attempted += 1
                era = cn_era(eid, start, transition) if args.server == "cn" else "unspecified"
                e = {"server": args.server, "event_id": eid, "start_at": start, "end_at": stop,
                     "aggregate_end_at": aggregate_end,
                     "event_type": meta.get("eventType", "unknown"), "era": era, "tiers": {},
                     "window_source": "manual_override" if override else "bestdori.endAt",
                     "aggregate_window_source": "manual_override" if override and override.get("aggregate_end_at") is not None else "bestdori.aggregateEndAt",
                     "reward_rule_source": "user_supplied_2026-09-26" if args.server == "cn" else "not_classified"}
                for tier in args.tiers:
                    key = f"{args.server}:{eid}:{tier}"
                    base = "https://bestdori.com/api/tracker/data" if args.source == "bestdori" else "https://hhwx.org/api/bandori/tracker/data"
                    url = f"{base}?server={SERVERS[args.server]}&event={eid}&tier={tier}&type=event"
                    try:
                        payload = client.get(url)
                        if payload.get("result") is not True:
                            raise ValueError("API result is not true")
                        points = normalize_points(payload.get("cutoffs"))
                        if not points:
                            raise ValueError("empty history")
                        finals = [p for p in points if p["isFinal"] and p["time"] >= stop]
                        post_end = [p for p in points if stop <= p["time"] <= aggregate_end]
                        post_aggregate = [p for p in points if p["time"] >= aggregate_end]
                        label = labels.get(key)
                        archived = archive_cutoff(archives, eid, args.server, tier)
                        if label is None and archived is not None:
                            label = {
                                "ep": archived,
                                "time": aggregate_end,
                                "quality": "archive_final",
                                "evidence": (
                                    "https://bestdori.com/api/archives/all.5.json"
                                    f"; event={eid}; server={SERVERS[args.server]}; tier={tier}; "
                                    f"aggregateEndAt={aggregate_end}"
                                ),
                            }
                        if label is None and finals:
                            if len({p["ep"] for p in finals}) != 1:
                                raise ValueError("conflicting provider final labels")
                            label = {"ep": finals[-1]["ep"], "time": finals[-1]["time"], "quality": "explicit_final", "evidence": url}
                        if label is None and post_end:
                            terminal_values = {p["ep"] for p in post_end}
                            if len(terminal_values) == 1:
                                terminal = post_end[0]
                                label = {
                                    "ep": terminal["ep"],
                                    "time": terminal["time"],
                                    "quality": "post_end_final",
                                    "evidence": (
                                        f"{url}; stable cutoff observation(s) after endAt={stop} "
                                        f"and no later than aggregateEndAt={aggregate_end}"
                                    ),
                                }
                        if label is None and post_aggregate:
                            terminal_values = {p["ep"] for p in post_aggregate}
                            if len(terminal_values) == 1:
                                terminal = post_aggregate[0]
                                label = {
                                    "ep": terminal["ep"],
                                    "time": terminal["time"],
                                    "quality": "post_aggregate_observation",
                                    "evidence": f"{url}; stable cutoff observed at/after aggregateEndAt={aggregate_end}",
                                }
                        gaps = [(b["time"] - a["time"]) / HOUR for a, b in zip(points, points[1:])]
                        quality = {"key": key, "points": len(points), "median_gap_hours": st.median(gaps) if gaps else None,
                                   "max_gap_hours": max(gaps) if gaps else None, "has_label": label is not None,
                                   "archive_label": archived is not None,
                                   "post_end_points": len(post_end),
                                   "post_end_distinct_values": len({p["ep"] for p in post_end}),
                                   "post_end_lag_minutes": (post_end[0]["time"] - stop) / 60_000 if post_end else None,
                                   "post_aggregate_points": len(post_aggregate),
                                   "last_observation_time": points[-1]["time"]}
                        audit.append(quality)
                        if label is not None:
                            e["tiers"][str(tier)] = {"points": points, "label": label, "source": url}
                    except (ValueError, KeyError, TypeError, OSError) as exc:
                        audit.append({"key": key, "error": str(exc)})
                events.append(e)  # Empty events remain visible; no replacement by easier events.
            except (ValueError, KeyError, TypeError, OSError) as exc:
                attempted += 1
                audit.append({"event": eid, "error": str(exc)})
        data = {"schema": "bandoribench-dataset-v1", "synthetic": False,
                "knowledge_time": "observed_at_only", "requested_tiers": list(args.tiers),
                "acquisition_audit": audit,
                "events": sorted(events, key=lambda e: e["start_at"])}
        validate_dataset(data)
        save(out / "dataset.json", data)
    finally:
        save(out / "acquisition.json", {"requests": client.requests, "audit": audit})
    print(json.dumps({"events": len(events), "labeled_series": sum(len(e["tiers"]) for e in events),
                      "dataset": str(out / "dataset.json")}, ensure_ascii=False))


def synthetic_dataset(count: int = 18) -> dict:
    """Deterministic fake data for tests/demo, never a platform-accuracy result."""
    events = []
    for i in range(count):
        start = 1_600_000_000_000 + i * 240 * HOUR
        e = {"server": "jp", "event_id": i + 1, "start_at": start, "end_at": start + 168 * HOUR,
             "event_type": "synthetic", "era": "unspecified", "tiers": {}}
        for tier in (100, 1000, 2000):
            velocity = (10_000 + (i % 5) * 600) * (1000 / tier) ** 0.4
            points = []
            for h in range(0, 169, 6):
                pt = velocity * (h + (0.5 + (i % 4) * 0.2) * max(h - 144, 0) ** 2 / 24)
                points.append({"time": start + h * HOUR, "ep": round(pt), "isFinal": h == 168})
            e["tiers"][str(tier)] = {"points": points,
                "label": {"ep": points[-1]["ep"], "time": e["end_at"], "quality": "synthetic", "evidence": "synthetic generator; not game data"}}
        events.append(e)
    return {"schema": "bandoribench-dataset-v1", "synthetic": True, "knowledge_time": "synthetic",
            "requested_tiers": [100, 1000, 2000], "events": events}


def write_frozen(out: Path, bundle: dict) -> None:
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be empty; do not overwrite a frozen release")
    save(out / "private" / "benchmark.json", bundle)
    save(out / "public" / "tasks.json", public_bundle(bundle))
    save(out / "manifest.json", {"benchmark_id": bundle["benchmark_id"], "protocol": bundle["protocol"],
                                 "exclusions": bundle["exclusions"], "eligible_cases": len(bundle["tasks"])})


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    runner_argv: list[str] | None = None
    if raw_argv and raw_argv[0] == "model-eval" and "--" in raw_argv:
        separator = raw_argv.index("--")
        runner_argv = raw_argv[separator + 1:]
        raw_argv = raw_argv[:separator]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("collect", help="download/audit public archives; no silent final-score guess")
    c.add_argument("--source", choices=("bestdori", "hhwx"), default="bestdori")
    c.add_argument("--server", choices=tuple(SERVERS), default="jp")
    c.add_argument("--recent", type=int, default=30)
    c.add_argument("--tiers", type=int, nargs="+", choices=TIERS, default=[100, 1000, 2000])
    c.add_argument("--out", required=True)
    c.add_argument("--labels", help="verified labels JSON keyed server:event:tier")
    c.add_argument("--windows", help="explicit start/stop overrides JSON keyed server:event")
    c.add_argument("--settle-hours", type=int, default=24)
    c.add_argument("--delay", type=float, default=0.5)
    cf = commands.add_parser("calendar-fetch", help="materialize a frozen public calendar snapshot")
    cf.add_argument("--server", choices=("cn",), default="cn")
    cf.add_argument("--years", type=int, nargs="+", required=True)
    cf.add_argument("--out", required=True)
    f = commands.add_parser("freeze", help="legacy protocol-v1 fixed calibration/test pilot")
    f.add_argument("dataset")
    f.add_argument("--calibration-events", type=int, default=10)
    f.add_argument("--horizons", type=int, nargs="+", default=[72, 48, 24, 12, 6])
    f.add_argument("--step-hours", type=int, default=6)
    f.add_argument("--stale-hours", type=int, default=3)
    f.add_argument("--out", required=True)
    w = commands.add_parser("freeze-walkforward", help="protocol-v2 raw-history expanding-window hindcast")
    w.add_argument("dataset")
    w.add_argument("--warmup-events", type=int, default=12)
    w.add_argument("--horizons", type=int, nargs="+", default=[72, 48, 24, 12, 6])
    w.add_argument("--stale-hours", type=int, default=3)
    w.add_argument("--calendar", help="bandoribench-calendar-v1 JSON to freeze as public known-future input")
    w.add_argument("--tiers", type=int, nargs="+", choices=TIERS,
                   help="override dataset requested_tiers for this frozen benchmark")
    w.add_argument("--out", required=True)
    mp = commands.add_parser("model-plan", help="show chronological model-development/selection/final phases")
    mp.add_argument("benchmark", help="frozen benchmark directory or private/benchmark.json")
    mp.add_argument("--development-blocks", type=int, default=5)
    mp.add_argument("--out")
    md = commands.add_parser("model-export-devkit", help="export a self-contained development-only tuning benchmark")
    md.add_argument("benchmark", help="frozen benchmark directory or private/benchmark.json")
    md.add_argument("--development-blocks", type=int, default=5)
    md.add_argument("--out", required=True)
    mx = commands.add_parser("model-export-training", help="export only labels allowed before a model-evaluation phase")
    mx.add_argument("benchmark", help="frozen benchmark directory or private/benchmark.json")
    mx.add_argument("--phase", choices=MODEL_PHASES, default="development")
    mx.add_argument("--development-blocks", type=int, default=5)
    mx.add_argument("--out", required=True)
    me = commands.add_parser("model-eval", help="run a persistent external model API runner and score it directly")
    me.add_argument("benchmark", help="frozen benchmark directory or private/benchmark.json")
    me.add_argument("--phase", choices=MODEL_PHASES, default="development")
    me.add_argument("--track", choices=("point", "probabilistic"), default="point")
    me.add_argument("--bootstrap", type=int, default=200)
    me.add_argument("--development-blocks", type=int, default=5)
    me.add_argument("--submission-out")
    me.add_argument("--out", required=True)
    br = commands.add_parser("baseline-registry", help="print metadata for built-in benchmark baselines/models")
    br.add_argument("--out")
    bs = commands.add_parser("baseline-suite", help="run built-in baseline/model registry across model-evaluation phases")
    bs.add_argument("benchmark", help="frozen benchmark directory or private/benchmark.json")
    bs.add_argument("--bootstrap", type=int, default=200)
    bs.add_argument("--models", nargs="+", choices=tuple(BASELINE_REGISTRY))
    bs.add_argument("--out", required=True)
    p = commands.add_parser("predict", help="run a bundled baseline using public inputs only")
    p.add_argument("tasks")
    p.add_argument("--model", choices=tuple(BASELINE_REGISTRY) + ("bestdori-recalibrated",),
                   default="linear24")
    p.add_argument("--out", required=True)
    s = commands.add_parser("score", help="score an external or bundled submission")
    s.add_argument("benchmark")
    s.add_argument("submission")
    s.add_argument("--track", choices=("point", "probabilistic"), default="point")
    s.add_argument("--bootstrap", type=int, default=200)
    s.add_argument("--out", required=True)
    d = commands.add_parser("demo", help="end-to-end SYNTHETIC demonstration")
    d.add_argument("--out", required=True)
    args = parser.parse_args(raw_argv)
    if args.command == "model-eval":
        args.runner = runner_argv or []
    try:
        if args.command == "collect":
            if args.recent <= 0 or args.delay < 0 or args.settle_hours < 0:
                raise ValueError("invalid acquisition parameters")
            collect(args)
        elif args.command == "calendar-fetch":
            from calendar_provider.fetch import fetch_calendar
            out = Path(args.out)
            if out.exists():
                raise ValueError("calendar output already exists; remove it explicitly before regenerating")
            calendar = validate_calendar(fetch_calendar(args.server, args.years), args.server)
            save(out, calendar)
            print(json.dumps({"server": args.server, "years": calendar.get("years", []),
                              "days": len(calendar["days"]), "sha256": digest(calendar)}))
        elif args.command == "freeze":
            bundle = freeze(load(args.dataset), args.calibration_events, tuple(args.horizons), args.step_hours, args.stale_hours)
            write_frozen(Path(args.out), bundle)
            print(json.dumps({"benchmark_id": bundle["benchmark_id"], "cases": len(bundle["tasks"])}))
        elif args.command == "freeze-walkforward":
            calendar = load(args.calendar) if args.calendar else None
            bundle = freeze_walkforward(load(args.dataset), args.warmup_events, tuple(args.horizons),
                                        args.stale_hours, calendar,
                                        tuple(args.tiers) if args.tiers else None)
            write_frozen(Path(args.out), bundle)
            print(json.dumps({"benchmark_id": bundle["benchmark_id"], "cases": len(bundle["tasks"]),
                              "warmup_events": len(bundle["protocol"]["warmup_event_ids"]),
                              "target_events": len(bundle["protocol"]["target_event_ids"]),
                              "eligible_target_events": len(bundle["protocol"]["eligible_target_event_ids"]),
                              "excluded_cases": bundle["protocol"]["excluded_case_count"]}))
        elif args.command == "model-plan":
            bundle = load(_benchmark_path(args.benchmark))
            plan = model_evaluation_plan(bundle, args.development_blocks)
            if args.out:
                save(args.out, plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        elif args.command == "model-export-devkit":
            bundle = load(_benchmark_path(args.benchmark))
            devkit = model_development_bundle(bundle, args.development_blocks)
            write_frozen(Path(args.out), devkit)
            print(json.dumps({"benchmark_id": devkit["benchmark_id"],
                              "parent_benchmark_id": devkit["devkit"]["parent_benchmark_id"],
                              "development_events": len(devkit["protocol"]["target_event_ids"]),
                              "cases": len(devkit["tasks"])}))
        elif args.command == "model-export-training":
            bundle = load(_benchmark_path(args.benchmark))
            export = model_training_export(bundle, args.phase, args.development_blocks)
            save(args.out, export)
            print(json.dumps({"benchmark_id": export["benchmark_id"], "phase": export["phase"],
                              "training_events": len(export["events"]),
                              "training_cutoff_ms": export["training_cutoff_ms"]}))
        elif args.command == "model-eval":
            bundle = load(_benchmark_path(args.benchmark))
            submission, report = run_model_api(bundle, args.runner, args.phase, args.track,
                                               args.bootstrap, args.development_blocks)
            if args.submission_out:
                save(args.submission_out, submission)
            save(args.out, report)
            print(json.dumps({"model_id": report["model_id"], "phase": args.phase,
                              "track": args.track, "eligible": report["eligible"],
                              "protocol_eligible": report["model_api"]["protocol_eligible"],
                              "score": report["score"], "coverage": report["coverage"]}))
            return 0 if report["model_api"]["protocol_eligible"] else 2
        elif args.command == "baseline-registry":
            registry = baseline_registry()
            if args.out:
                save(args.out, registry)
            print(json.dumps(registry, ensure_ascii=False, indent=2))
        elif args.command == "baseline-suite":
            bundle = load(_benchmark_path(args.benchmark))
            suite = baseline_suite(bundle, args.bootstrap, args.models)
            save(args.out, suite)
            print(json.dumps({
                "suite_id": suite["suite_id"],
                "benchmark_id": suite["benchmark_id"],
                "models": len(suite["reports"]),
                "rows": len(suite["summary"]),
            }))
        elif args.command == "predict":
            save(args.out, predict(load(args.tasks), args.model))
        elif args.command == "score":
            report = evaluate(load(args.benchmark), load(args.submission), args.track, args.bootstrap)
            save(args.out, report)
            print(json.dumps({k: report[k] for k in ("model_id", "track", "synthetic", "eligible", "score", "coverage")}))
            return 0 if report["eligible"] else 2
        elif args.command == "demo":
            out = Path(args.out)
            bundle = freeze(synthetic_dataset(), 8)
            write_frozen(out, bundle)
            summary = []
            for model in ("persistence", "linear24", "linear24-quantiles", "bestdori-recalibrated"):
                submission = predict(public_bundle(bundle), model)
                save(out / f"{model}.submission.json", submission)
                tracks = ("point", "probabilistic") if model == "linear24-quantiles" else ("point",)
                for track in tracks:
                    report = evaluate(bundle, submission, track)
                    save(out / f"{model}.{track}.report.json", report)
                    summary.append({k: report[k] for k in ("model_id", "track", "synthetic", "eligible", "score", "coverage")})
            save(out / "summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"BandoriBench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
