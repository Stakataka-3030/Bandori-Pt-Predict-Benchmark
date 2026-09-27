"""The displayed one-hour line uses only visible, recent tracker points."""

import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "examples" / "local_app"
sys.path.insert(0, str(APP_DIR))
from engine import HOUR, one_hour_projection, predict  # noqa: E402


class OneHourProjectionTests(unittest.TestCase):
    def test_interpolates_exact_hour_and_anchors_at_latest_observation(self):
        history = [{"time": 0, "ep": 100},
                   {"time": 40 * 60000, "ep": 180},
                   {"time": 90 * 60000, "ep": 280}]
        result = one_hour_projection(history, 90 * 60000, 150 * 60000)
        self.assertAlmostEqual(result["growth_per_hour"], 120)
        self.assertEqual(result["path"][0], [90 * 60000, 280])
        self.assertAlmostEqual(result["terminal"], 400)

    def test_future_or_unavailable_rows_cannot_change_projection(self):
        base = [{"time": 0, "ep": 100},
                {"time": HOUR, "ep": 200}]
        expected = one_hour_projection(base, HOUR, 2 * HOUR)["terminal"]
        contaminated = base + [
            {"time": HOUR + 1, "ep": 100000},
            {"time": HOUR, "ep": 100000, "available_at": HOUR + 1},
        ]
        self.assertEqual(one_hour_projection(contaminated, HOUR, 2 * HOUR)["terminal"],
                         expected)

    def test_requires_full_hour_and_fresh_tracker(self):
        history = [{"time": 30 * 60000, "ep": 100},
                   {"time": HOUR, "ep": 200}]
        self.assertIsNone(one_hour_projection(history, HOUR, 2 * HOUR))
        history.insert(0, {"time": 0, "ep": 50})
        self.assertIsNone(one_hour_projection(history, 2 * HOUR + 1, 3 * HOUR))

    def test_forecast_adds_line_at_24_hours_but_not_before(self):
        state = {"fit": {"tail_pool": {}, "multiplier_samples": {},
                         "kaori_ratios": {},
                         "aoi_templates": {str(h): [] for h in (72, 48, 24, 12, 6)},
                         "early_progress_paths": []}}
        end = 100 * HOUR
        for horizon, expected in ((25, 0), (24, 4)):
            issue = end - horizon * HOUR
            tasks = []
            for tier in (500, 1000, 1500, 2000):
                tasks.append({"case_id": str(tier), "tier": tier,
                              "era": "voice500_1500", "event_type": "story",
                              "start_at": 0, "end_at": end, "issued_at": issue,
                              "input_cutoff_at": issue, "horizon_hours": horizon,
                              "history": [{"time": i * HOUR, "ep": 1000 + 100 * i}
                                          for i in range(101)]})
            snapshot = predict({"event_id": 999, "tasks": tasks}, state)
            self.assertEqual(len(snapshot["linear1h"]), expected)


if __name__ == "__main__":
    unittest.main()
