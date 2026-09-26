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
from pathlib import Path
from typing import Any

VERSION = "0.1.2"
HOUR = 3_600_000
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
TIERS = (1, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 1500,
         2000, 3000, 4000, 5000, 10000, 20000, 30000, 40000, 50000, 70000, 100000)
SERVERS = {"jp": 0, "en": 1, "tw": 2, "cn": 3}
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
            if label.get("quality") not in ("explicit_final", "post_aggregate_observation", "verified", "synthetic"):
                raise ValueError("terminal labels require explicit_final / post_aggregate_observation / verified / synthetic")
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
    if n_calibration < 3 or len(events) <= n_calibration:
        raise ValueError("need >=3 calibration events and >=1 later test event")
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


def verify_bundle(bundle: dict) -> None:
    body = {k: v for k, v in bundle.items() if k != "benchmark_id"}
    if digest(body) != bundle.get("benchmark_id"):
        raise ValueError("benchmark fingerprint mismatch; do not edit a frozen benchmark")


def public_bundle(bundle: dict) -> dict:
    """No test truth or original full test event is included."""
    return {k: bundle[k] for k in ("benchmark_id", "protocol", "tasks", "calibration", "scales")}


def predict(public: dict, model: str = "linear24") -> dict:
    if model not in ("linear24", "persistence", "linear24-quantiles", "bestdori-recalibrated"):
        raise ValueError("unknown baseline")
    predictions = []
    for task in public["tasks"]:
        row = {"case_id": task["case_id"]}
        try:
            value = linear24(task)
            if model == "persistence":
                value = task["history"][-1]["ep"]
            elif model == "bestdori-recalibrated":
                rate = public["calibration"]["rates"].get(rate_key(task))
                if rate is None:
                    raise ValueError("no >=3-event calibration rate for this type/tier")
                value = bestdori_recalibrated(task, rate)
            row["prediction"] = value
            if model == "linear24-quantiles":
                residuals = public["calibration"]["residuals"][scale_key(task)]
                row["quantiles"] = {str(q): max(task["history"][-1]["ep"], value + percentile(residuals, q)) for q in QUANTILES}
                row["prediction"] = row["quantiles"]["0.5"]
        except (ValueError, KeyError) as exc:
            row["error"] = str(exc)
        predictions.append(row)
    return {"benchmark_id": public["benchmark_id"], "model_id": model,
            "model_version": VERSION, "provenance": "offline_coarse_recompute_not_platform_archive",
            "predictions": predictions}


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
                result["covered90"] = q[0.05] <= y <= q[0.95]
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
                  below_current_count=sum(r["below_current"] for r in rows))
    for dimension in ("horizon_hours", "tier", "era"):
        report["by_" + dimension] = {str(value): score_transform(macro([r for r in rows if r[dimension] == value]))
                                      for value in sorted({r[dimension] for r in rows})}
    if track == "probabilistic":
        report["coverage90"] = st.mean(r["covered90"] for r in rows)
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
                        post_aggregate = [p for p in points if p["time"] >= aggregate_end]
                        label = labels.get(key)
                        if label is None and finals:
                            if len({p["ep"] for p in finals}) != 1:
                                raise ValueError("conflicting provider final labels")
                            label = {"ep": finals[-1]["ep"], "time": finals[-1]["time"], "quality": "explicit_final", "evidence": url}
                        if label is None and post_aggregate:
                            terminal = post_aggregate[-1]
                            label = {
                                "ep": terminal["ep"],
                                "time": terminal["time"],
                                "quality": "post_aggregate_observation",
                                "evidence": f"{url}; cutoff observed at/after aggregateEndAt={aggregate_end}",
                            }
                        gaps = [(b["time"] - a["time"]) / HOUR for a, b in zip(points, points[1:])]
                        quality = {"key": key, "points": len(points), "median_gap_hours": st.median(gaps) if gaps else None,
                                   "max_gap_hours": max(gaps) if gaps else None, "has_label": label is not None,
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
                "knowledge_time": "observed_at_only", "acquisition_audit": audit,
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
    return {"schema": "bandoribench-dataset-v1", "synthetic": True, "knowledge_time": "synthetic", "events": events}


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
    f = commands.add_parser("freeze", help="calibrate on early events and lock later test cases")
    f.add_argument("dataset")
    f.add_argument("--calibration-events", type=int, default=10)
    f.add_argument("--horizons", type=int, nargs="+", default=[72, 48, 24, 12, 6])
    f.add_argument("--step-hours", type=int, default=6)
    f.add_argument("--stale-hours", type=int, default=3)
    f.add_argument("--out", required=True)
    p = commands.add_parser("predict", help="run a bundled baseline using public inputs only")
    p.add_argument("tasks")
    p.add_argument("--model", choices=("linear24", "persistence", "linear24-quantiles", "bestdori-recalibrated"), default="linear24")
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
