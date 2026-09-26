"""China calendar generation.

The generated file is intentionally frozen before benchmark use.
Runtime forecasting should consume the frozen JSON, not call providers.

This module uses the optional `holidays` package when available. Official
adjusted-workday overrides can be merged later without changing the output
schema.
"""

from __future__ import annotations

from datetime import date


def build_china_calendar(years: list[int]) -> dict:
    try:
        import holidays  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Install optional dependency 'holidays' or provide a frozen calendar JSON"
        ) from exc

    cn = holidays.country_holidays("CN", years=years)
    days = {}
    for d in cn:
        days[d.isoformat()] = {"type": "holiday"}

    for year in years:
        start = date(year, 1, 1)
        # Weekends not explicitly marked remain weekend in benchmark fallback.
        # Makeup workdays should be merged from official annual schedules.
        _ = start

    return {
        "schema": "bandoribench-calendar-v1",
        "server": "cn",
        "utc_offset_hours": 8,
        "days": dict(sorted(days.items())),
    }
