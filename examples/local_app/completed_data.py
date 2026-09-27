"""Ingest only fully completed CN events with an observed terminal cutoff."""

from __future__ import annotations

import hashlib
import json

import data_source

TIERS = data_source.TIERS
ARCHIVE_GRACE_MS = 5 * 60 * 1000


class PendingFinal(ValueError):
    """The event ended, but a four-tier terminal observation is not verified."""


def eligible_events(state, now_ms):
    """Return not-yet-trained events in real CN start order, never ID order."""
    index = data_source.fetch_json("https://bestdori.com/api/events/all.3.json")
    cutoff = int(state["training_cutoff_at"])
    seen = set(map(int, state["completed_event_ids"]))
    rows = []
    for event_id, metadata in index.items():
        try:
            start, end = data_source._cn_window(metadata)
            event_id = int(event_id)
        except (ValueError, TypeError):
            continue
        if start > cutoff and end < now_ms and event_id not in seen:
            rows.append((start, end, event_id))
    return sorted(rows)


def completed_event(event_id, now_ms):
    """Use stable post-end Bestdori observations, without guessing labels."""
    metadata = data_source.fetch_json(f"https://bestdori.com/api/events/{int(event_id)}.json")
    start, end = data_source._cn_window(metadata)
    try:
        aggregate_end = int(metadata["aggregateEndAt"][3])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PendingFinal(f"活动 {event_id} 缺少国服结算结束时刻") from exc
    if aggregate_end <= end or now_ms < aggregate_end + ARCHIVE_GRACE_MS:
        raise PendingFinal(f"活动 {event_id} 尚未完成收官观测")
    tiers = {}
    evidence = []
    digest = hashlib.sha256()
    for tier in TIERS:
        history, url = data_source.tracker("bestdori", int(event_id), tier, aggregate_end)
        before = [row for row in history if start <= row["time"] <= end]
        after = [row for row in history if end < row["time"] <= aggregate_end]
        if len(before) < 2 or not after:
            raise PendingFinal(f"活动 {event_id} T{tier} 缺少赛中轨迹或收官观测")
        final_values = {row["ep"] for row in after}
        if len(final_values) != 1 or after[0]["ep"] < before[-1]["ep"]:
            raise PendingFinal(f"活动 {event_id} T{tier} 收官观测冲突")
        final = after[0]["ep"]
        tiers[str(tier)] = {
            "points": before,
            "label": {"ep": final, "time": after[0]["time"],
                      "quality": "post_end_final", "evidence": url},
        }
        evidence.append({"tier": tier, "url": url, "time": after[0]["time"],
                         "observation_count": len(after)})
        digest.update(json.dumps({"tier": tier, "points": before,
                                  "post_end": after}, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))
    return {
        "event_id": int(event_id), "server": "cn", "era": "voice500_1500",
        "event_type": metadata.get("eventType", "unknown"),
        "start_at": start, "end_at": end, "aggregate_end_at": aggregate_end,
        "tiers": tiers,
    }, {"source": "bestdori_post_end_tracker", "records": evidence,
        "tracker_sha256": digest.hexdigest()}
