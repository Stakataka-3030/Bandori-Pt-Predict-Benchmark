# Calendar inputs

Protocol v2 can freeze a public known-future calendar:

```bash
python bandoribench.py freeze-walkforward data/cn-80/dataset.json \
  --warmup-events 12 --calendar calendars/cn.json --out runs/cn-v1
```

Calendar files use schema `bandoribench-calendar-v1` and are part of the benchmark fingerprint. Do not silently update a frozen benchmark's calendar.

Example structure:

```json
{
  "schema": "bandoribench-calendar-v1",
  "server": "cn",
  "utc_offset_hours": 8,
  "days": {
    "YYYY-MM-DD": "holiday",
    "YYYY-MM-DD": {"type": "makeup_workday", "known_at": 0}
  }
}
```

Supported types: `weekday`, `weekend`, `holiday`, `makeup_workday`.

Repository examples intentionally do not invent official holiday dates. A real release should populate them from an authoritative schedule, record provenance separately, and use `known_at` when a change was announced after earlier forecast origins.
