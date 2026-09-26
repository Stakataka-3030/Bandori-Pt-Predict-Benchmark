# Calendar provider

This package materializes `bandoribench-calendar-v1` snapshots for benchmark freezing.

- Generation is deterministic and standard-library only.
- Prediction and scoring never query a live calendar service.
- CN 2019–2026 is based on State Council General Office annual holiday notices.
- 2019 Labor Day's later adjustment and the nationwide 2020 Spring Festival extension are applied explicitly.
- Every announced override has a causal `known_at`. A revision also carries `previous_type` and `previous_known_at` so hindcasts can reconstruct the schedule known before the revision without leaking even the original notice further backward.
- Unsupported years fail loudly instead of silently falling back to an incomplete holiday list.

Generate the committed CN snapshot with:

```bash
python bandoribench.py calendar-fetch --server cn --years 2019 2020 2021 2022 2023 2024 2025 2026 --out calendars/cn-2019-2026.json
```
