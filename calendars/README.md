# Calendar inputs

Protocol v2 can freeze a public known-future calendar. The normalized calendar SHA-256 is included in the benchmark protocol, so a frozen benchmark must never silently change its calendar.

The repository ships:

```text
calendars/cn-2019-2026.json
```

Regenerate it deterministically:

```bash
python bandoribench.py calendar-fetch --server cn --years 2019 2020 2021 2022 2023 2024 2025 2026 --out calendars/cn-2019-2026.json
```

Then freeze CN-Core:

```bash
python bandoribench.py freeze-walkforward data/cn-80/dataset.json \
  --warmup-events 12 --tiers 500 1000 2000 \
  --calendar calendars/cn-2019-2026.json --out runs/cn-core-v1
```

Supported day types are `weekday`, `weekend`, `holiday`, and `makeup_workday`. Ordinary days need not be listed; runtime falls back to weekday/weekend. `known_at`, `previous_type`, and `previous_known_at` preserve the schedule actually knowable at each hindcast origin.
