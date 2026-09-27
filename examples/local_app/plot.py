"""Render a shareable four-tier PNG from an in-memory Aoi snapshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager

TIERS = (500, 1000, 1500, 2000)
CN = timezone(timedelta(hours=8))


def _date(ms):
    return datetime.fromtimestamp(ms / 1000, CN)


def render(snapshot: dict, output: Path, source: str) -> None:
    # Prediction dictionaries use integer tier keys in memory; exported JSON uses strings.
    snapshot = json.loads(json.dumps(snapshot))
    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        if family in {item.name for item in font_manager.fontManager.ttflist}:
            plt.rcParams["font.sans-serif"] = [family]
            break
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(18, 10.125))
    fig.patch.set_facecolor("#fbfaf7")
    grid = fig.add_gridspec(4, 2, width_ratios=(3.25, 1.42),
                            left=.055, right=.985, top=.80, bottom=.075,
                            wspace=.085, hspace=.12)
    axes = [fig.add_subplot(grid[i, 0]) for i in range(4)]
    bars = fig.add_subplot(grid[:, 1])
    ink, purple, teal, gold = "#27323b", "#80669f", "#6da9a0", "#cda252"
    for i, (ax, tier) in enumerate(zip(axes, TIERS)):
        key = str(tier)
        history = snapshot["visible_history"][key]
        current = snapshot["current"][key]
        end = snapshot["end_at"]
        ax.set_facecolor("#ffffff")
        for member in snapshot["members"]:
            points = member["paths"][key]
            ax.plot([_date(t) for t, _ in points], [v / 10000 for _, v in points],
                    color=teal, alpha=min(.42, .15 + 10 * member["weight"]),
                    linewidth=1.55)
        ax.plot([_date(p["time"]) for p in history],
                [p["ep"] / 10000 for p in history], color=ink, linewidth=3.3)
        control_path = snapshot["control_paths"][key]
        ax.plot([_date(t) for t, _ in control_path],
                [v / 10000 for _, v in control_path], color=purple,
                linewidth=3.4, linestyle=(0, (7, 4)))
        for value, color, size in ((snapshot["member_p10"][key], teal, 64),
                                   (snapshot["member_p90"][key], gold, 64),
                                   (snapshot["control"][key], purple, 78)):
            ax.scatter([_date(end)], [value / 10000], color=color, s=size, zorder=6)
        ax.scatter([_date(snapshot["issued_at"])], [current / 10000],
                   color=ink, s=56, zorder=6)
        ax.axvline(_date(snapshot["issued_at"]), color="#d4d9d4",
                   linewidth=1.2, linestyle=(0, (2, 4)))
        upper = max(snapshot["member_p90"][key],
                    *(member["terminals"][key] for member in snapshot["members"]))
        ax.set_ylim(0, upper / 10000 * 1.13)
        ax.set_xlim(_date(history[0]["time"]), _date(end + 5 * 3600000))
        ax.set_yticks([])
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#d2d8d4")
        ax.text(.016, .84, f"T{tier}", transform=ax.transAxes,
                fontsize=22, fontweight="bold", color=ink, va="top")
        if i < 3:
            ax.tick_params(axis="x", bottom=False, labelbottom=False)
        else:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=CN))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=CN))
            ax.tick_params(axis="x", labelsize=12, colors="#53616a", pad=8)
    bars.set_facecolor("#ffffff")
    bars.set_ylim(-.4, 15.2)
    bars.set_xticks([])
    bars.set_yticks([])
    bars.spines[:].set_visible(False)
    largest = 0
    for i, tier in enumerate(TIERS):
        key = str(tier)
        top = 13.4 - i * 3.65
        bars.text(1, top + .82, f"T{tier}", fontsize=20,
                  fontweight="bold", color=ink, va="center")
        for j, (label, value, color) in enumerate((
                ("10%", snapshot["member_p10"][key], teal),
                ("kaori", snapshot["control"][key], purple),
                ("90%", snapshot["member_p90"][key], gold))):
            y = top - j * .83
            largest = max(largest, value)
            bars.text(1, y, label, va="center", fontsize=15,
                      fontweight="bold", color="#53616a")
            bars.barh(y, value / 10000, left=55, height=.57,
                      color=color, alpha=.93)
            bars.text(58 + value / 10000, y, f"{value / 10000:.1f}万",
                      va="center", fontsize=16, fontweight="bold", color=ink)
        if i < 3:
            bars.axhline(top - 2.86, color="#e8eae6", linewidth=1)
    bars.set_xlim(0, 55 + largest / 10000 * 1.23)
    fig.text(.055, .955, "tsukushi-aoi", ha="left", va="center",
             fontsize=36, fontweight="bold", color=ink)
    fig.text(.055, .905, f"#{snapshot['event_id']} · {source}", ha="left",
             va="center", fontsize=24, fontweight="bold", color="#59666d")
    fig.text(.985, .955, _date(snapshot["issued_at"]).strftime("%Y-%m-%d %H:%M 起报"),
             ha="right", va="center", fontsize=17, color="#59666d")
    fig.text(.985, .905, _date(snapshot["end_at"]).strftime("%Y-%m-%d %H:%M 终点"),
             ha="right", va="center", fontsize=17, color="#59666d")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
