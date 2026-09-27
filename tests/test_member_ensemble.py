from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "member_ensemble"))
from member_ensemble import EmpiricalMemberEnsemble, N_MEMBERS

HOUR = 3600000


class FakeControl:
    def initialize(self, context):
        pass

    def predict_panel(self, panel):
        return [{"case_id": t["case_id"], "prediction": float(t["history"][-1]["ep"]) * 1.5}
                for t in panel["tasks"]]

    def observe_event(self, event):
        pass


def historical(event_id, extra):
    end = 100 * HOUR
    tiers = {}
    for tier, scale in ((500, 1.0), (1000, .6), (1500, .5), (2000, .4)):
        points = [{"time": hour * HOUR, "ep": scale * (10000 + 1000 * hour +
                   extra * max(0, hour - 88) ** 2)} for hour in range(101)]
        tiers[str(tier)] = {"points": points,
                            "label": {"ep": points[-1]["ep"], "time": end}}
    return {"server": "cn", "event_id": event_id, "start_at": 0, "end_at": end,
            "event_type": "test", "era": "voice500_1500", "tiers": tiers}


def panel(issue, values, future=None):
    end = issue + 12 * HOUR
    tasks = []
    for tier, value in values.items():
        history = [{"time": issue - HOUR, "ep": value - 1000},
                   {"time": issue, "ep": value}]
        if future:
            history.append({"time": issue + HOUR, "ep": future[tier]})
        tasks.append({"case_id": f"cn:999:{tier}:12", "event_id": 999,
                      "tier": tier, "era": "voice500_1500",
                      "issued_at": issue, "input_cutoff_at": issue,
                      "end_at": end, "horizon_hours": 12, "history": history})
    return {"event_id": 999, "horizon_hours": 12, "tasks": tasks}


class EmpiricalMemberTests(unittest.TestCase):
    def make(self):
        model = EmpiricalMemberEnsemble()
        model.control = FakeControl()
        model.initialize({})
        return model

    def test_zero_event_has_no_invented_spread(self):
        model = self.make()
        model.predict_panel(panel(200 * HOUR, {500: 100000, 1000: 60000, 2000: 40000}))
        snap = model.last_snapshot
        self.assertEqual(snap["diagnostics"]["template_count"], 0)
        self.assertEqual(len(snap["members"]), N_MEMBERS)
        for tier in (500, 1000, 2000):
            self.assertEqual({m["terminals"][str(tier)] for m in snap["members"]},
                             {snap["control"][tier]})

    def test_members_stay_within_completed_event_envelope(self):
        model = self.make()
        model.observe_event(historical(1, 0))
        model.observe_event(historical(2, 70))
        self.assertEqual(len(model.templates[12]), 2)
        model.predict_panel(panel(200 * HOUR, {500: 100000, 1000: 60000, 2000: 40000}))
        snap = model.last_snapshot
        self.assertEqual(snap["diagnostics"]["template_event_ids"], [1, 2])
        for tier in (500, 1000, 2000):
            endpoints = [m["terminals"][str(tier)] for m in snap["members"]]
            pure = [m["terminals"][str(tier)] for m in snap["members"][:2]]
            self.assertAlmostEqual(min(endpoints), min(pure))
            self.assertAlmostEqual(max(endpoints), max(pure))
            self.assertTrue(all(m["paths"][str(tier)][0][1] == snap["current"][tier]
                                for m in snap["members"]))

    def test_future_tracker_rows_are_invisible(self):
        first = panel(200 * HOUR, {500: 100000, 1000: 60000, 2000: 40000})
        extra = copy.deepcopy(first)
        for task in extra["tasks"]:
            task["history"].append({"time": task["issued_at"] + HOUR, "ep": 9999999})
        a, b = self.make(), self.make()
        for model in (a, b):
            model.observe_event(historical(1, 0))
            model.observe_event(historical(2, 70))
        self.assertEqual(a.predict_panel(first), b.predict_panel(extra))
        self.assertEqual(a.last_snapshot, b.last_snapshot)


if __name__ == "__main__":
    unittest.main()
