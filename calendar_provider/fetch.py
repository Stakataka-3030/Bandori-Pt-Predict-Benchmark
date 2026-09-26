"""Generate frozen BandoriBench calendar JSON files."""

from __future__ import annotations

from .china import build_china_calendar


def fetch_calendar(server: str, years: list[int]) -> dict:
    if server == "cn":
        return build_china_calendar(years)
    raise ValueError(f"calendar provider unavailable for server: {server}")
