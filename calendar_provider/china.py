"""Authoritative China public-holiday calendar snapshots for BandoriBench.

The benchmark never queries a calendar service at prediction time. This module
materializes State Council General Office holiday notices into the
bandoribench-calendar-v1 schema so the result can be committed and frozen.

For notices where only a publication date is retained here, known_at is set to
the next China-local midnight. That deliberately errs on the conservative side:
a hindcast from earlier on the publication day cannot see the new schedule.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

CST = timezone(timedelta(hours=8))
SUPPORTED_YEARS = tuple(range(2019, 2027))

ANNUAL_SCHEDULES = {
    2019: {
        "published": "2018-12-06",
        "source": "https://www.nhc.gov.cn/wjw/gwywj/201812/13af235c34214a9b9df33c1e1083f14f.shtml",
        "holiday_ranges": (
            ("2018-12-30", "2019-01-01"),
            ("2019-02-04", "2019-02-10"),
            ("2019-04-05", "2019-04-07"),
            ("2019-05-01", "2019-05-01"),
            ("2019-06-07", "2019-06-09"),
            ("2019-09-13", "2019-09-15"),
            ("2019-10-01", "2019-10-07"),
        ),
        "makeup_workdays": (
            "2018-12-29", "2019-02-02", "2019-02-03",
            "2019-09-29", "2019-10-12",
        ),
    },
    2020: {
        "published": "2019-11-21",
        "source": "https://app.www.gov.cn/govdata/gov/201911/21/451111/article.html",
        "holiday_ranges": (
            ("2020-01-01", "2020-01-01"),
            ("2020-01-24", "2020-01-30"),
            ("2020-04-04", "2020-04-06"),
            ("2020-05-01", "2020-05-05"),
            ("2020-06-25", "2020-06-27"),
            ("2020-10-01", "2020-10-08"),
        ),
        "makeup_workdays": (
            "2020-01-19", "2020-02-01", "2020-04-26", "2020-05-09",
            "2020-06-28", "2020-09-27", "2020-10-10",
        ),
    },
    2021: {
        "published": "2020-11-25",
        "source": "https://app.www.gov.cn/govdata/gov/202011/25/465322/article.html",
        "holiday_ranges": (
            ("2021-01-01", "2021-01-03"),
            ("2021-02-11", "2021-02-17"),
            ("2021-04-03", "2021-04-05"),
            ("2021-05-01", "2021-05-05"),
            ("2021-06-12", "2021-06-14"),
            ("2021-09-19", "2021-09-21"),
            ("2021-10-01", "2021-10-07"),
        ),
        "makeup_workdays": (
            "2021-02-07", "2021-02-20", "2021-04-25", "2021-05-08",
            "2021-09-18", "2021-09-26", "2021-10-09",
        ),
    },
    2022: {
        "published": "2021-10-25",
        "source": "https://www.nhc.gov.cn/bgt/gwywj2/202110/8d28bb02f9844afa94abaeb2d0292857.shtml",
        "holiday_ranges": (
            ("2022-01-01", "2022-01-03"),
            ("2022-01-31", "2022-02-06"),
            ("2022-04-03", "2022-04-05"),
            ("2022-04-30", "2022-05-04"),
            ("2022-06-03", "2022-06-05"),
            ("2022-09-10", "2022-09-12"),
            ("2022-10-01", "2022-10-07"),
        ),
        "makeup_workdays": (
            "2022-01-29", "2022-01-30", "2022-04-02", "2022-04-24",
            "2022-05-07", "2022-10-08", "2022-10-09",
        ),
    },
    2023: {
        "published": "2022-12-08",
        "source": "https://app.www.gov.cn/govdata/gov/202212/08/495070/article.html",
        "holiday_ranges": (
            ("2022-12-31", "2023-01-02"),
            ("2023-01-21", "2023-01-27"),
            ("2023-04-05", "2023-04-05"),
            ("2023-04-29", "2023-05-03"),
            ("2023-06-22", "2023-06-24"),
            ("2023-09-29", "2023-10-06"),
        ),
        "makeup_workdays": (
            "2023-01-28", "2023-01-29", "2023-04-23", "2023-05-06",
            "2023-06-25", "2023-10-07", "2023-10-08",
        ),
    },
    2024: {
        "published": "2023-10-25",
        "source": "https://www.gov.cn/zhengce/content/202310/content_6911527.htm",
        "holiday_ranges": (
            ("2024-01-01", "2024-01-01"),
            ("2024-02-10", "2024-02-17"),
            ("2024-04-04", "2024-04-06"),
            ("2024-05-01", "2024-05-05"),
            ("2024-06-08", "2024-06-10"),
            ("2024-09-15", "2024-09-17"),
            ("2024-10-01", "2024-10-07"),
        ),
        "makeup_workdays": (
            "2024-02-04", "2024-02-18", "2024-04-07", "2024-04-28",
            "2024-05-11", "2024-09-14", "2024-09-29", "2024-10-12",
        ),
    },
    2025: {
        "published": "2024-11-12",
        "source": "https://www.gov.cn/gongbao/2024/issue_11726/material/gwygb202433.pdf",
        "holiday_ranges": (
            ("2025-01-01", "2025-01-01"),
            ("2025-01-28", "2025-02-04"),
            ("2025-04-04", "2025-04-06"),
            ("2025-05-01", "2025-05-05"),
            ("2025-05-31", "2025-06-02"),
            ("2025-10-01", "2025-10-08"),
        ),
        "makeup_workdays": (
            "2025-01-26", "2025-02-08", "2025-04-27",
            "2025-09-28", "2025-10-11",
        ),
    },
    2026: {
        "published": "2025-11-04",
        "source": "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
        "holiday_ranges": (
            ("2026-01-01", "2026-01-03"),
            ("2026-02-15", "2026-02-23"),
            ("2026-04-04", "2026-04-06"),
            ("2026-05-01", "2026-05-05"),
            ("2026-06-19", "2026-06-21"),
            ("2026-09-25", "2026-09-27"),
            ("2026-10-01", "2026-10-07"),
        ),
        "makeup_workdays": (
            "2026-01-04", "2026-02-14", "2026-02-28",
            "2026-05-09", "2026-09-20", "2026-10-10",
        ),
    },
}

SPECIAL_ADJUSTMENTS = (
    {
        "year": 2019,
        "published": "2019-03-22",
        "source": "https://app.www.gov.cn/govdata/gov/201903/22/436883/article.html",
        "description": "2019 Labor Day adjustment",
        "holiday_ranges": (("2019-05-01", "2019-05-04"),),
        "makeup_workdays": ("2019-04-28", "2019-05-05"),
    },
    {
        "year": 2020,
        "published": "2020-01-27",
        "source": "https://app.www.gov.cn/govdata/gov/202001/27/453442/article.html",
        "description": "2020 Spring Festival nationwide extension through February 2",
        "holiday_ranges": (("2020-01-31", "2020-02-02"),),
        "makeup_workdays": (),
    },
)


def _known_at(publication_date: str) -> int:
    published = date.fromisoformat(publication_date)
    visible = datetime.combine(published + timedelta(days=1), time.min, tzinfo=CST)
    return int(visible.timestamp() * 1000)


def _dates(start: str, end: str):
    current, stop = date.fromisoformat(start), date.fromisoformat(end)
    if stop < current:
        raise ValueError(f"invalid holiday range: {start}..{end}")
    while current <= stop:
        yield current.isoformat()
        current += timedelta(days=1)


def _set_day(days: dict[str, dict], day: str, day_type: str, known_at: int) -> None:
    old = days.get(day)
    if old and old["type"] == day_type:
        if old.get("known_at", known_at) <= known_at:
            return
    entry = {"type": day_type, "known_at": known_at}
    if old and old["type"] != day_type and old.get("known_at", 0) <= known_at:
        entry["previous_type"] = old["type"]
        entry["previous_known_at"] = old.get("known_at")
    days[day] = entry


def _apply_schedule(days: dict[str, dict], schedule: dict) -> None:
    known_at = _known_at(schedule["published"])
    for start, end in schedule["holiday_ranges"]:
        for day in _dates(start, end):
            _set_day(days, day, "holiday", known_at)
    for day in schedule["makeup_workdays"]:
        _set_day(days, day, "makeup_workday", known_at)


def build_china_calendar(years: Iterable[int]) -> dict:
    selected = sorted(set(int(year) for year in years))
    if not selected:
        raise ValueError("at least one calendar year is required")
    unsupported = [year for year in selected if year not in ANNUAL_SCHEDULES]
    if unsupported:
        raise ValueError(
            f"unsupported China calendar year(s): {unsupported}; "
            f"supported range is {SUPPORTED_YEARS[0]}-{SUPPORTED_YEARS[-1]}"
        )

    days: dict[str, dict] = {}
    notices = []
    # Apply selected schedules plus an adjacent next-year schedule when it can
    # classify a late-December date inside the selected coverage.
    schedule_years = set(selected)
    for covered_year in selected:
        if covered_year < SUPPORTED_YEARS[-1]:
            schedule_years.add(covered_year + 1)
    for year in sorted(schedule_years):
        schedule = ANNUAL_SCHEDULES[year]
        _apply_schedule(days, schedule)
        notices.append({
            "year": year,
            "published": schedule["published"],
            "source": schedule["source"],
            "coverage_role": "selected_year" if year in selected else "adjacent_cross_year",
        })

    adjustments = []
    for adjustment in SPECIAL_ADJUSTMENTS:
        if adjustment["year"] not in selected:
            continue
        _apply_schedule(days, adjustment)
        adjustments.append({
            "year": adjustment["year"],
            "published": adjustment["published"],
            "description": adjustment["description"],
            "source": adjustment["source"],
        })

    selected_set = set(selected)
    days = {day: entry for day, entry in days.items()
            if date.fromisoformat(day).year in selected_set}

    return {
        "schema": "bandoribench-calendar-v1",
        "server": "cn",
        "utc_offset_hours": 8.0,
        "days": dict(sorted(days.items())),
        "years": selected,
        "provenance": {
            "authority": "State Council General Office of the People's Republic of China",
            "knowledge_policy": "notice visible from next China-local midnight after publication",
            "annual_notices": notices,
            "special_adjustments": adjustments,
        },
    }
