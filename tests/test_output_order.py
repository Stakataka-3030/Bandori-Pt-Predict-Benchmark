"""Output ordering must agree across controls, members and the linear line."""

import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "local_app"))
from output_order import constrain_snapshot, project_ranked  # noqa: E402


TIERS = (500, 1000, 1500, 2000)
CURRENT = {500: 200.0, 1000: 150.0, 1500: 120.0, 2000: 80.0}


def ordered(values):
    return all(values[a] >= values[b] for a, b in zip(TIERS, TIERS[1:]))


def snapshot(mode="Rui"):
    control = {500: 300.0, 1000: 200.0, 1500: 250.0, 2000: 100.0}
    endpoints = ((310.0, 190.0, 220.0, 90.0),
                 (290.0, 210.0, 260.0, 110.0))
    members = []
    for index, terminal in enumerate(endpoints):
        values = dict(zip(TIERS, terminal))
        paths = {str(tier): [[0, CURRENT[tier]], [5, (CURRENT[tier] + values[tier]) / 2],
                             [10, values[tier]]] for tier in TIERS}
        members.append({"member_id": f"m{index}", "weight": .5,
                        "paths": paths,
                        "terminals": {str(tier): values[tier] for tier in TIERS}})
    return {"current": dict(CURRENT), "control": control,
            "control_paths": {str(tier): [[0, CURRENT[tier]], [5, 200.0],
                                         [10, control[tier]]] for tier in TIERS},
            "members": members, "logic_mode": mode,
            "member_p10": dict(control), "member_median": dict(control),
            "member_p90": dict(control), "linear1h": dict(control),
            "linear1h_paths": {tier: [[0, CURRENT[tier]], [10, control[tier]]]
                               for tier in TIERS},
            "linear1h_details": {tier: {"growth_per_hour": 1.0}
                                  for tier in TIERS},
            "issued_at": 0, "end_at": 10}


class OutputOrderTests(unittest.TestCase):
    def test_auxiliary_tier_is_clamped_without_moving_valid_primary_tiers(self):
        raw = {500: 300, 1000: 200, 1500: 250, 2000: 100}
        projected = project_ranked(raw, CURRENT)
        self.assertEqual(projected, {500: 300, 1000: 200,
                                     1500: 200, 2000: 100})
        self.assertEqual(project_ranked({500: 300, 1000: 200,
                                         1500: 150, 2000: 100}, CURRENT)[1500], 150)
        self.assertEqual(project_ranked({500: 300, 1000: 200,
                                         1500: 50, 2000: 100}, CURRENT)[1500], 120)

    def test_primary_tier_fallback_and_current_floor_stay_ordered(self):
        projected = project_ranked({500: 100, 1000: 150, 1500: 200, 2000: 120},
                                   {500: 80, 1000: 70, 1500: 60, 2000: 50})
        self.assertEqual(projected, {500: 125, 1000: 125,
                                     1500: 125, 2000: 120})
        self.assertTrue(ordered(projected))

    def test_rui_paths_members_quantiles_and_linear_are_consistent(self):
        result = constrain_snapshot(snapshot())
        self.assertEqual(result["raw_control"][1500], 250)
        self.assertEqual(result["raw_member_terminals"]["m0"]["1500"], 220)
        self.assertTrue(result["rank_order_adjustment"]["control_changed"])
        self.assertTrue(result["rank_order_adjustment"]["members_changed"])
        self.assertTrue(result["rank_order_adjustment"]["linear1h_changed"])
        self.assertTrue(result["rank_order_adjustment"]["t1500_clamped"])
        for family in ("control", "member_p10", "member_median", "member_p90", "linear1h"):
            self.assertTrue(ordered(result[family]), family)
        for member in result["members"]:
            self.assertTrue(ordered({tier: member["terminals"][str(tier)] for tier in TIERS}))
            for step in (1, 2):
                self.assertTrue(ordered({tier: member["paths"][str(tier)][step][1]
                                         for tier in TIERS}))
        for step in range(33):
            self.assertTrue(ordered({tier: result["linear1h_paths"][tier][step][1]
                                     for tier in TIERS}))

    def test_ordered_mashiro_output_keeps_public_values(self):
        base = snapshot("Mashiro")
        for family in (base["control"], base["linear1h"]):
            family[1500] = 175
        for tier in TIERS:
            base["control_paths"][str(tier)][1][1] = (CURRENT[tier] + base["control"][tier]) / 2
            base["control_paths"][str(tier)][2][1] = base["control"][tier]
            base["linear1h_paths"][tier][-1][1] = base["linear1h"][tier]
        for member in base["members"]:
            for step in (1, 2):
                member["paths"]["1500"][step][1] = min(
                    member["paths"]["1500"][step][1],
                    member["paths"]["1000"][step][1])
            member["terminals"]["1500"] = member["paths"]["1500"][-1][1]
        original = copy.deepcopy(base)
        result = constrain_snapshot(base)
        self.assertEqual(result["control"], original["control"])
        self.assertEqual(result["linear1h"], original["linear1h"])
        self.assertEqual(result["control_paths"], original["control_paths"])
        self.assertEqual(result["rank_order_adjustment"], {
            "control_changed": False, "members_changed": False,
            "linear1h_changed": False, "t1500_clamped": False})


if __name__ == "__main__":
    unittest.main()
