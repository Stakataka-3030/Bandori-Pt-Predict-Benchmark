#!/usr/bin/env python3
"""Bandori PT terminal-forecast benchmark. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERSION = "0.3.0"
HOUR = 3_600_000
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
            dtype, known_at = raw, None
        elif isinstance(raw, dict):
            dtype = raw.get("type")
            known_at = raw.get("known_at")
        else:
            raise ValueError(f"invalid calendar entry for {day}")
        if dtype not in CALENDAR_DAY_TYPES:
            raise ValueError(f"invalid calendar day type for {day}")
        entry = {"type": dtype}
        if known_at is not None:
            entry["known_at"] = integer(known_at, f"calendar {day} known_at")
        normalized[day] = entry
    return {"schema": "bandoribench-calendar-v1", "server": server,
            "utc_offset_hours": float(offset), "days": dict(sorted(normalized.items()))}


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


def reward_feature_dict(task: dict) -> dict[str, float]:
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
                       stale_hours: int = 3, calendar: dict | None = None) -> dict:
    """Protocol v2: raw tracker prefixes with expanding historical context."""
    events = validate_dataset(data)
    if len({e["server"] for e in events}) != 1:
        raise ValueError("freeze one server per benchmark; compare servers separately")
    server = events[0]["server"]
    calendar = validate_calendar(calendar, server)
    requested_tiers = [int(t) for t in data.get("requested_tiers", [])]
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
        for tier in map(str, requested_tiers):
            series, batch = e["tiers"][tier], []
            try:
                for h in horizons:
                    task = make_raw_task(e, tier, h, protocol)
                    if scale_key(task) not in scales:
                        raise ValueError(f"fewer than 3 warm-up scale cases for {scale_key(task)}")
                    task["history_event_ids"] = allowed
                    batch.append(task)
            except ValueError as exc:
                exclusions.append({"stage": "hindcast", "event": e["event_id"],
                                   "tier": tier, "reason": str(exc)})
                continue
            for task in batch:
                tasks.append(task)
                truth[task["case_id"]] = series["label"]
    if not tasks:
        raise ValueError("no eligible walk-forward cases")
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
    models = ("linear24", "persistence", "calibrated-linear24", "linear24-quantiles",
              "bestdori-recalibrated", "bestdori-hierarchical", "multitier-analog-ensemble",
              "hhwx-instant", "hhwx-24h", "rinko-dpra-replay", "care-s")
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
    if model == "care-s":
        _enforce_care_tier_order(public, predictions)
    return {"benchmark_id": public["benchmark_id"], "model_id": model,
            "model_version": VERSION,
            "provenance": ("care_s_causal_walk_forward" if model == "care-s"
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
    features.update(reward_feature_dict(own))
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
                          "target": math.log(max(final, 1.0) / max(base, 1.0))}
            except (KeyError, ValueError):
                sample = False
            cache[key] = sample
        if sample:
            samples.append(sample)
    return samples


def _care_oos_residuals(samples: list[dict]) -> list[float]:
    residuals = []
    for index in range(2, len(samples)):
        earlier = samples[:index]
        try:
            model = _ridge_fit([row["features"] for row in earlier],
                               [row["target"] for row in earlier])
            predicted = _ridge_predict(model, samples[index]["features"])
        except ValueError:
            continue
        residuals.append(samples[index]["target"] - predicted)
    if len(residuals) < 3 and samples:
        center = st.median(row["target"] for row in samples)
        residuals = [row["target"] - center for row in samples]
    return residuals or [0.0]


def care_s_forecast(public: dict, task: dict, cache: dict | None = None) -> tuple[float, dict[str, float], dict]:
    """CARE-S: analog ensemble + causal learned conditional correction + OOS residuals."""
    if public["protocol"]["schema"] != "bandoribench-protocol-v2":
        raise ValueError("CARE-S requires protocol-v2")
    cache = cache if cache is not None else {}
    values, weights, analog_meta = _multitier_analog_components(public, task)
    samples = _care_training_samples(public, task, cache)
    current_features = care_feature_dict(public, task, analog_meta)
    if len(samples) >= 2:
        model = _ridge_fit([row["features"] for row in samples],
                           [row["target"] for row in samples])
        correction = _ridge_predict(model, current_features)
        feature_count = len(model["keys"])
    elif samples:
        correction = st.median(row["target"] for row in samples)
        feature_count = len(samples[0]["features"])
    else:
        correction, feature_count = 0.0, len(current_features)
    residuals = _care_oos_residuals(samples)
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
            row["care"]["tier_order_constraint"] = True

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


def evaluate(bundle: dict, submission: dict, track: str = "point", bootstrap: int = 200) -> dict:
    verify_bundle(bundle)
    if submission.get("benchmark_id") != bundle["benchmark_id"]:
        raise ValueError("submission targets a different benchmark")
    if track not in ("point", "probabilistic") or bootstrap < 0:
        raise ValueError("invalid evaluation options")
    expected = {t["case_id"]: t for t in bundle["tasks"]}
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
              "n_scored": len(rows), "failures": failures, "dataset_exclusions": bundle["exclusions"],
              "knowledge_time": bundle["protocol"]["knowledge_time"]}
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
    w.add_argument("--out", required=True)
    p = commands.add_parser("predict", help="run a bundled baseline using public inputs only")
    p.add_argument("tasks")
    p.add_argument("--model", choices=("linear24", "persistence", "calibrated-linear24",
                                      "linear24-quantiles", "bestdori-recalibrated",
                                      "bestdori-hierarchical", "multitier-analog-ensemble",
                                      "hhwx-instant", "hhwx-24h", "rinko-dpra-replay",
                                      "care-s"), default="linear24")
    p.add_argument("--out", required=True)
    s = commands.add_parser("score", help="score an external or bundled submission")
    s.add_argument("benchmark")
    s.add_argument("submission")
    s.add_argument("--track", choices=("point", "probabilistic"), default="point")
    s.add_argument("--bootstrap", type=int, default=200)
    s.add_argument("--out", required=True)
    d = commands.add_parser("demo", help="end-to-end SYNTHETIC demonstration")
    d.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            if args.recent <= 0 or args.delay < 0 or args.settle_hours < 0:
                raise ValueError("invalid acquisition parameters")
            collect(args)
        elif args.command == "freeze":
            bundle = freeze(load(args.dataset), args.calibration_events, tuple(args.horizons), args.step_hours, args.stale_hours)
            write_frozen(Path(args.out), bundle)
            print(json.dumps({"benchmark_id": bundle["benchmark_id"], "cases": len(bundle["tasks"])}))
        elif args.command == "freeze-walkforward":
            calendar = load(args.calendar) if args.calendar else None
            bundle = freeze_walkforward(load(args.dataset), args.warmup_events, tuple(args.horizons),
                                        args.stale_hours, calendar)
            write_frozen(Path(args.out), bundle)
            print(json.dumps({"benchmark_id": bundle["benchmark_id"], "cases": len(bundle["tasks"]),
                              "warmup_events": len(bundle["protocol"]["warmup_event_ids"]),
                              "target_events": len(bundle["protocol"]["target_event_ids"])}))
        elif args.command == "predict":
            save(args.out, predict(load(args.tasks), args.model))
        elif args.command == "score":
            report = evaluate(load(args.benchmark), load(args.submission), args.track, args.bootstrap)
            save(args.out, report)
            print(json.dumps({k: report[k] for k in ("model_id", "track", "synthetic", "eligible", "score", "coverage")}))
            return 0 if report["eligible"] else 2
        else:
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
