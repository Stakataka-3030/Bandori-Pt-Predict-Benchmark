"""Materialize frozen BandoriBench calendar inputs."""

from __future__ import annotations

from .china import build_china_calendar


def fetch_calendar(server: str, years: list[int]) -> dict:
    """Return a deterministic provider snapshot; never query live services."""
    if server == "cn":
        return build_china_calendar(years)
    raise ValueError(f"calendar provider unavailable for server: {server}")
