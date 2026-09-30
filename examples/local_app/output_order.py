"""Order-constrained presentation of independent tier forecasts.

The fitted models and their raw predictions are untouched. This module only
projects values shown to users onto the required cutoff-rank order.
"""

from __future__ import annotations

import copy
import math


TIERS = (500, 1000, 1500, 2000)
PRIMARY = (500, 1000, 2000)


def _weighted_quantile(pairs, q):
    ordered = sorted(pairs)
    goal = q * sum(weight for _, weight in ordered)
    total = 0.0
    for value, weight in ordered:
        total += weight
        if total >= goal - 1e-12:
            return value
    return ordered[-1][0]


def _nonincreasing_isotonic(values):
    """Least-squares projection for the primary tiers (unit weights)."""
    blocks = []
    for value in values:
        blocks.append([value, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            right = blocks.pop()
            blocks[-1][0] += right[0]
            blocks[-1][1] += right[1]
    return [total / count for total, count in blocks for _ in range(count)]


def project_ranked(values, current=None):
    """Keep primary tiers unless crossed; clamp auxiliary T1500 between them."""
    raw = {int(tier): float(value) for tier, value in values.items()}
    if not raw or any(tier not in TIERS or not math.isfinite(value) or value < 0
                      for tier, value in raw.items()):
        raise ValueError("invalid tier forecast for rank projection")
    floor_raw = {int(tier): float(value) for tier, value in (current or {}).items()
                 if int(tier) in raw}
    if any(not math.isfinite(value) or value < 0 for value in floor_raw.values()):
        raise ValueError("invalid current cutoff for rank projection")
    # A stale per-tier tracker can itself appear crossed. The cumulative
    # envelope allows future forecasts to stay above every visible cutoff.
    floors = {}
    running = 0.0
    for tier in reversed([tier for tier in TIERS if tier in raw]):
        running = max(running, floor_raw.get(tier, 0.0))
        floors[tier] = running
    primary = [tier for tier in PRIMARY if tier in raw]
    fitted = _nonincreasing_isotonic([raw[tier] for tier in primary])
    out = {tier: max(value, floors[tier]) for tier, value in zip(primary, fitted)}
    if 1500 in raw:
        value = max(raw[1500], floors[1500])
        above = next((out[tier] for tier in (1000, 500) if tier in out), None)
        below = out.get(2000)
        if above is not None:
            value = min(value, above)
        if below is not None:
            value = max(value, below)
        out[1500] = value
    return out


def _correct_paths(paths, current):
    tiers = [tier for tier in TIERS if str(tier) in paths]
    if not tiers:
        return False
    lengths = {len(paths[str(tier)]) for tier in tiers}
    if len(lengths) != 1 or min(lengths) < 2:
        raise ValueError("tier forecast paths have incompatible grids")
    changed = False
    for index in range(1, next(iter(lengths))):
        stamps = {paths[str(tier)][index][0] for tier in tiers}
        if len(stamps) != 1:
            raise ValueError("tier forecast paths have different timestamps")
        values = {tier: paths[str(tier)][index][1] for tier in tiers}
        fixed = project_ranked(values, current)
        for tier in tiers:
            if abs(fixed[tier] - values[tier]) > 1e-8:
                changed = True
                paths[str(tier)][index][1] = fixed[tier]
    return changed


def _path_value(path, stamp):
    left, right = path[0], path[-1]
    if right[0] <= left[0]:
        return float(right[1])
    fraction = (stamp - left[0]) / (right[0] - left[0])
    return float(left[1]) + fraction * (float(right[1]) - float(left[1]))


def constrain_snapshot(snapshot):
    """Return the same snapshot with ranked output and auditable raw terminals."""
    current = snapshot["current"]
    members = snapshot["members"]
    snapshot["raw_control"] = dict(snapshot["control"])
    snapshot["raw_control_paths"] = copy.deepcopy(snapshot["control_paths"])
    snapshot["raw_member_terminals"] = {
        member["member_id"]: dict(member["terminals"]) for member in members}
    snapshot["raw_member_quantiles"] = {
        field: dict(snapshot[field]) for field in ("member_p10", "member_median", "member_p90")}
    snapshot["raw_linear1h"] = dict(snapshot.get("linear1h", {}))

    member_changed = False
    t1500_clamped = False
    for member in members:
        original_1500 = copy.deepcopy(member["paths"].get("1500"))
        member_changed |= _correct_paths(member["paths"], current)
        t1500_clamped |= original_1500 != member["paths"].get("1500")
        member["terminals"] = {str(tier): member["paths"][str(tier)][-1][1]
                               for tier in TIERS if str(tier) in member["paths"]}
    if member_changed:
        for q, field in ((.1, "member_p10"), (.5, "member_median"),
                         (.9, "member_p90")):
            snapshot[field] = {
                tier: _weighted_quantile(
                    [(member["terminals"][str(tier)], member["weight"])
                     for member in members], q)
                for tier in current}

    if snapshot.get("logic_mode") == "Rui" and member_changed:
        # Rui's Kaori line is the weighted median of the same 50 scenarios.
        snapshot["control_paths"] = {
            str(tier): [[members[0]["paths"][str(tier)][step][0],
                         _weighted_quantile(
                             [(member["paths"][str(tier)][step][1], member["weight"])
                              for member in members], .5)]
                        for step in range(len(members[0]["paths"][str(tier)]))]
            for tier in current}
        snapshot["control"] = dict(snapshot["member_median"])
    else:
        _correct_paths(snapshot["control_paths"], current)
        snapshot["control"] = {
            tier: snapshot["control_paths"][str(tier)][-1][1] for tier in current}

    linear = snapshot.get("linear1h", {})
    linear_changed = False
    if linear:
        paths = snapshot["linear1h_paths"]
        issue, end = snapshot["issued_at"], snapshot["end_at"]
        grid = [issue + (end - issue) * step / 32 for step in range(33)]
        projected = []
        for stamp in grid:
            values = {tier: _path_value(paths[tier], stamp) for tier in linear}
            projected.append(project_ranked(values, current))
        if 1500 in linear:
            t1500_clamped |= any(abs(fixed[1500] - _path_value(paths[1500], stamp)) > 1e-8
                                 for stamp, fixed in zip(grid, projected))
        if any(abs(projected[step][tier] - _path_value(paths[tier], grid[step])) > 1e-8
               for step in range(33) for tier in linear):
            linear_changed = True
            for tier in linear:
                paths[tier] = [[stamp, fixed[tier]]
                               for stamp, fixed in zip(grid, projected)]
                linear[tier] = projected[-1][tier]
                snapshot["linear1h_details"][tier]["order_adjusted"] = True

    snapshot["rank_order_adjustment"] = {
        "control_changed": snapshot["control_paths"] != snapshot["raw_control_paths"],
        "members_changed": member_changed,
        "linear1h_changed": linear_changed,
        "t1500_clamped": t1500_clamped or (
            snapshot["control_paths"].get("1500") != snapshot["raw_control_paths"].get("1500")),
    }
    return snapshot
