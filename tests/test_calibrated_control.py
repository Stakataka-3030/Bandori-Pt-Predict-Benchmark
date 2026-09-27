from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "member_ensemble"))
from calibrated_control import CalibratedControl

HOUR = 3600000


class FakeBase:
    def initialize(self, context):
        pass

    def predict_panel(self, panel):
        return [{"case_id": task["case_id"],
                 "prediction": float(task["history"][-1]["ep"]) * 1.5}
                for task in panel["tasks"]]

    def observe_event(self, event):
        pass


def completed(event_id, era="voice500_1500"):
    offset = event_id * 110 * HOUR
    tiers = {}
    for tier, scale in ((500, 1), (1000, .6), (1500, .5), (2000, .4)):
        points = [{"time": offset + hour * HOUR,
                   "ep": scale * (10000 + 1000 * hour + 350 * max(0, hour - 88) ** 2)}
                  for hour in range(101)]
        tiers[str(tier)] = {"points": points,
                            "label": {"ep": points[-1]["ep"]}}
    return {"event_id": event_id, "start_at": offset,
            "end_at": offset + 100 * HOUR, "event_type": "test",
            "era": era, "tiers": tiers}


def target_panel(future=False):
    issue = 2000 * HOUR
    tasks = []
    for tier, value in ((500, 100000), (1000, 60000), (1500, 50000), (2000, 40000)):
        history = [{"time": issue - HOUR, "ep": value - 1000},
                   {"time": issue, "ep": value}]
        if future:
            history.append({"time": issue + HOUR, "ep": 9999999})
        tasks.append({"case_id": f"cn:999:{tier}:12", "tier": tier,
                      "era": "voice500_1500", "event_id": 999,
                      "issued_at": issue, "input_cutoff_at": issue,
                      "end_at": issue + 12 * HOUR, "horizon_hours": 12,
                      "history": history})
    return {"event_id": 999, "horizon_hours": 12, "tasks": tasks}


class CalibratedControlTests(unittest.TestCase):
    def make(self):
        model = CalibratedControl(prior_strength=4,
                                  adjusted_horizons=(12,), min_events=6)
        model.base = FakeBase()
        model.initialize({})
        return model

    def test_six_completed_new_era_events_gate_the_correction(self):
        model = self.make()
        model.observe_event(completed(1, "voice1000"))
        self.assertEqual(len(model.ratios[(500, 12)]), 0)
        for event_id in range(2, 8):
            model.observe_event(completed(event_id))
            output = {int(row["case_id"].split(":")[2]): row["prediction"]
                      for row in model.predict_panel(target_panel())}
            if event_id < 7:
                self.assertEqual(output[500], 150000)
                self.assertEqual(output[1500], 75000)
            else:
                self.assertGreater(output[500], 150000)
                self.assertGreater(output[1500], 75000)
            self.assertEqual(output[1000], 90000)
            self.assertEqual(output[2000], 60000)

    def test_future_tracker_row_cannot_change_prediction(self):
        a, b = self.make(), self.make()
        for event_id in range(1, 7):
            a.observe_event(completed(event_id))
            b.observe_event(completed(event_id))
        self.assertEqual(a.predict_panel(target_panel()),
                         b.predict_panel(target_panel(future=True)))


if __name__ == "__main__":
    unittest.main()
