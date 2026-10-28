"""Causal and output-contract checks for the offline research candidate."""
import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples/local_app"))
from joint_research import pair_prior, predict_research, reconcile
from test_rui_mode import fixture, HOUR


def setup():
    panel, state = fixture(9)
    state.update(training_cutoff_at=-HOUR, completed_event_ids=[1, 2, 3])
    for task in panel["tasks"]:
        task.update(era="voice500_1500", event_id=999)
    history = [{"event_id": i, "server": "cn", "era": "voice500_1500",
                "start_at": -(400 - i * 120) * HOUR, "end_at": -(300 - i * 90) * HOUR,
                "tiers": {"1000": {"label": {"ep": 100000}},
                          "1500": {"label": {"ep": v}}}}
               for i, v in ((1, 80000), (2, 82000), (3, 85000))]
    return panel, state, history


class JointResearchTests(unittest.TestCase):
    def test_crossing_uses_learned_ratio_instead_of_identical_outputs(self):
        _, _, history = setup()
        prior = pair_prior(history, 0, 999)
        raw = {500: 500, 1000: 300, 1500: 400, 2000: 200}
        current = {500: 200, 1000: 150, 1500: 120, 2000: 80}
        got, info = reconcile(raw, current, prior, 100)
        self.assertEqual(info["status"], "crossing_to_prior")
        self.assertAlmostEqual(got[1000], 300)
        self.assertAlmostEqual(got[1500] / got[1000], .82)
        self.assertGreater(got[1000], got[1500])
        self.assertEqual(raw[1500], 400)

    def test_insufficient_evidence_does_not_invent_a_positive_gap(self):
        _, _, history = setup()
        prior = pair_prior(history[:1], 0, 999)
        got, info = reconcile({500: 500, 1000: 300, 1500: 400, 2000: 200}, {}, prior, 100)
        self.assertEqual(info["status"], "insufficient_prior")
        self.assertEqual(got[1500], got[1000])

    def test_prior_uses_real_chronology_and_rejects_current_unfinished_or_duplicates(self):
        _, _, history = setup()
        history[0]["event_id"] = 9999
        self.assertEqual(pair_prior(history[::-1], 0, 999)["event_ids"], [9999, 2, 3])
        for dirty in (history + [history[0]], [dict(history[0], event_id=999)],
                      [dict(history[0], end_at=HOUR)]):
            with self.assertRaises(ValueError):
                pair_prior(dirty, 0, 999)

    def test_delayed_final_is_not_available_for_prior_fit(self):
        _, _, history = setup()
        history[0]["tiers"]["1500"]["label"]["available_at"] = HOUR
        with self.assertRaisesRegex(ValueError, "not available"):
            pair_prior(history, 0, 999)

    def test_legacy_final_values_cannot_change_new_regime_prior(self):
        _, _, history = setup()
        expected = pair_prior(history, 0, 999)
        old = copy.deepcopy(history[0])
        old.update(era="voice1000", event_id=888)
        old["tiers"]["1500"]["label"]["ep"] = 1
        self.assertEqual(expected, pair_prior([old] + history, 0, 999))

    def test_future_target_truth_and_unavailable_points_cannot_change_prediction(self):
        panel, state, history = setup()
        dirty = copy.deepcopy(panel)
        for task in dirty["tasks"]:
            task["label"] = {"ep": 999999999}
            task["history"].extend([{"time": 10 * HOUR, "ep": 999999999},
                                    {"time": 8 * HOUR, "available_at": 10 * HOUR, "ep": 999999999}])
        for mode in ("mashiro", "rui"):
            self.assertEqual(predict_research(panel, state, history, mode),
                             predict_research(dirty, state, history, mode))

    def test_outputs_are_finite_ranked_points_and_inputs_are_immutable(self):
        panel, state, history = setup()
        saved = copy.deepcopy((panel, state, history))
        for mode in ("mashiro", "rui"):
            got = predict_research(panel, state, history, mode)
            vals = [got["control"][t] for t in (500, 1000, 1500, 2000)]
            self.assertTrue(all(math.isfinite(v) and v >= 0 for v in vals))
            self.assertEqual(vals, sorted(vals, reverse=True))
            self.assertTrue(all(got["control"][t] >= got["current"][t] for t in got["control"]))
            self.assertEqual(got["uncertainty"], "point_forecast_only")
            self.assertNotIn("members", got)
            if mode == "rui":
                weights = got["filter_diagnostics"]["family_weights"]
                self.assertAlmostEqual(sum(weights.values()), 1)
                self.assertTrue(all(w > 0 for w in weights.values()))
        self.assertEqual((panel, state, history), saved)

    def test_future_fit_and_mismatched_panels_are_rejected(self):
        panel, state, history = setup()
        with self.assertRaisesRegex(ValueError, "future training"):
            predict_research(panel, dict(state, training_cutoff_at=0), history)
        dirty = copy.deepcopy(panel)
        dirty["tasks"][0]["issued_at"] += 1
        with self.assertRaisesRegex(ValueError, "inconsistent forecast"):
            predict_research(dirty, state, history)
        with self.assertRaisesRegex(ValueError, "distinct tiers"):
            predict_research(dict(panel, tasks=panel["tasks"][:3]), state, history)


if __name__ == "__main__":
    unittest.main()
