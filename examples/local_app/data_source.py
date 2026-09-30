"""Bestdori / HHWX live CN tracker readers with fail-closed validation."""

from __future__ import annotations

import json
import math
import urllib.request

TIERS = (500, 1000, 1500, 2000)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://bestdori.com/tool/eventtracker/",
}


def fetch_json(url, timeout=20):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    return json.loads(data)


def _cn_window(event):
    try:
        start = int(event["startAt"][3])
        end = int(event["endAt"][3])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("活动缺少国服开场/结束时刻") from exc
    if end <= start:
        raise ValueError("活动结束时间不晚于开场")
    return start, end


def active_event_id(now_ms):
    index = fetch_json("https://bestdori.com/api/events/all.3.json")
    candidates = []
    for event_id, meta in index.items():
        try:
            start, end = _cn_window(meta)
            if start <= now_ms < end:
                candidates.append((start, int(event_id)))
        except (ValueError, TypeError):
            continue
    if not candidates:
        raise ValueError("目前没有正在进行的国服活动，请输入活动 ID 或稍后重试")
    return max(candidates)[1]


def event_info(event_id):
    meta = fetch_json(f"https://bestdori.com/api/events/{int(event_id)}.json")
    start, end = _cn_window(meta)
    names = meta.get("eventName") or []
    name = next((names[index] for index in (3, 0)
                 if len(names) > index and isinstance(names[index], str)
                 and names[index].strip()), f"活动{int(event_id)}")
    return {"event_id": int(event_id), "start_at": start, "end_at": end,
            "event_type": meta.get("eventType", "unknown"),
            "event_name": " ".join(name.split()), "server": "cn", "server_name": "国服"}


def tracker(source, event_id, tier, issued_at):
    if source not in ("bestdori", "hhwx"):
        raise ValueError("数据来源只能选 Bestdori 或 HHWX")
    if tier not in TIERS:
        raise ValueError("unsupported tier")
    base = ("https://bestdori.com/api/tracker/data" if source == "bestdori"
            else "https://hhwx.org/api/bandori/tracker/data")
    url = f"{base}?server=3&event={int(event_id)}&tier={tier}&type=event"
    payload = fetch_json(url)
    if payload.get("result") is not True:
        raise ValueError(f"{source} T{tier} 未返回有效档线")
    rows = payload.get("cutoffs")
    if not isinstance(rows, list):
        raise ValueError(f"{source} T{tier} 的档线列表格式错误")
    by_time = {}
    for row in rows:
        if row is None:
            continue
        stamp = int(row["time"])
        available = int(row.get("available_at", stamp))
        value = float(row["ep"])
        if stamp <= issued_at and available <= issued_at:
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{source} T{tier} 出现非法 PT")
            if stamp in by_time and by_time[stamp] != value:
                raise ValueError(f"{source} T{tier} 同一时刻记录冲突")
            by_time[stamp] = value
    history = [{"time": time, "ep": by_time[time]} for time in sorted(by_time)]
    if not history:
        raise ValueError(f"{source} T{tier} 还没有可用档线")
    if any(a["ep"] > b["ep"] for a, b in zip(history, history[1:])):
        raise ValueError(f"{source} T{tier} 出现 PT 倒退，请切换来源")
    return history, url


def live_panel(source, event_id, issued_at):
    info = event_info(event_id)
    if not (info["start_at"] <= issued_at < info["end_at"]):
        raise ValueError("当前活动尚未开场或已经结束")
    tasks, urls = [], []
    for tier in TIERS:
        history, url = tracker(source, event_id, tier, issued_at)
        tasks.append({"server": "cn", "event_id": int(event_id),
                      "start_at": info["start_at"], "end_at": info["end_at"],
                      "event_type": info["event_type"], "era": "voice500_1500",
                      "case_id": f"cn:{event_id}:{tier}:live:{issued_at}",
                      "tier": tier, "horizon_hours":
                      (info["end_at"] - issued_at) / 3600000,
                      "issued_at": issued_at,
                      "input_cutoff_at": history[-1]["time"],
                      "history": history})
        urls.append(url)
    return {"event_id": int(event_id), "event_name": info["event_name"],
            "server": info["server"], "server_name": info["server_name"],
            "horizon_hours":
            (info["end_at"] - issued_at) / 3600000,
            "issued_at": issued_at, "tasks": tasks}, urls
