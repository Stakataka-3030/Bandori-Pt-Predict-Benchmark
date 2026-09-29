"""Rui replays only observations available at each earlier report."""

import copy
import sys
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "examples" / "local_app"
sys.path.insert(0, str(APP))
from engine import HOUR, predict  # noqa: E402


TIERS = (500, 1000, 1500, 2000)


def fixture(issue_hour):
    paths = []
    for event_id, early_fraction in ((1, .08), (2, .14), (3, .24)):
        curve = [[0.0, 0.001], [.025, early_fraction], [.10, early_fraction * 2],
                 [1.0, 1.0]]
        paths.append({"event_id": event_id, "event_type": "test",
                      "paths": {str(tier): curve for tier in TIERS}})
    state = {"fit": {"early_progress_paths": paths}}
    tasks = []
    for tier in TIERS:
        scale = 500 / tier
        history = [{"time": hour * HOUR, "ep": scale * (1000 + 1700 * hour)}
                   for hour in range(1, issue_hour + 1)]
        tasks.append({"case_id": str(tier), "tier": tier, "start_at": 0,
                      "end_at": 120 * HOUR, "issued_at": issue_hour * HOUR,
                      "history": history})
    return {"event_id": 999, "tasks": tasks}, state


class RuiModeTests(unittest.TestCase):
    def test_first_report_has_equal_weights_then_replay_updates_them(self):
        first_panel, state = fixture(3)
        first = predict(first_panel, state, mode="rui")
        self.assertEqual(first["diagnostics"]["replayed_reports"], 0)
        self.assertEqual({m["weight"] for m in first["members"]}, {1 / 50})
        later_panel, _ = fixture(9)
        later = predict(later_panel, state, mode="rui")
        self.assertGreater(later["diagnostics"]["replayed_reports"], 0)
        self.assertTrue(later["diagnostics"]["assimilated"])
        self.assertEqual(len(later["members"]), 50)
        self.assertAlmostEqual(sum(m["weight"] for m in later["members"]), 1)
        self.assertGreater(max(m["weight"] for m in later["members"]), 1 / 50)
        for tier in TIERS:
            self.assertLessEqual(later["member_p10"][tier], later["control"][tier])
            self.assertLessEqual(later["control"][tier], later["member_p90"][tier])

    def test_future_or_not_yet_available_points_do_not_change_replay(self):
        panel, state = fixture(9)
        clean = predict(panel, state, mode="rui")
        contaminated = copy.deepcopy(panel)
        for task in contaminated["tasks"]:
            task["history"].extend([
                {"time": 10 * HOUR, "ep": 99999999},
                {"time": 9 * HOUR, "available_at": 10 * HOUR, "ep": 99999999}])
        self.assertEqual(clean, predict(contaminated, state, mode="rui"))

    def test_invalid_mode_does_not_change_default(self):
        panel, state = fixture(9)
        with self.assertRaisesRegex(ValueError, "unknown forecast mode"):
            predict(panel, state, mode="not-a-mode")

    def test_replay_crosses_72_hour_boundary_and_keeps_endgame_projection(self):
        panel, state = fixture(100)
        snapshot = predict(panel, state, mode="rui")
        self.assertEqual(snapshot["forecast_mode"], "sequential_pruning")
        self.assertGreater(snapshot["diagnostics"]["replayed_reports"], 0)
        self.assertEqual(len(snapshot["linear1h"]), 4)


if __name__ == "__main__":
    unittest.main()
