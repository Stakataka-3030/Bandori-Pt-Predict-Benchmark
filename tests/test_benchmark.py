"""Offline deterministic regression tests; all event data here is synthetic."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bandoribench as b


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = b.synthetic_dataset()
        cls.bundle = b.freeze(cls.data, 8)
        cls.public = b.public_bundle(cls.bundle)
        cls.submission = b.predict(cls.public)

    def oracle(self):
        sub = copy.deepcopy(self.submission)
        for p in sub["predictions"]:
            p["prediction"] = self.bundle["truth"][p["case_id"]]["ep"]
        return sub

    def test_end_to_end(self):
        r = b.evaluate(self.bundle, self.submission, bootstrap=20)
        self.assertTrue(r["eligible"])
        self.assertEqual(r["n_expected"], 150)
        self.assertTrue(0 < r["score"] < 100)
        self.assertIn("event_bootstrap_95_interval", r)
        self.assertTrue(r["synthetic"])

    def test_oracle_is_100(self):
        self.assertEqual(b.evaluate(self.bundle, self.oracle(), bootstrap=0)["score"], 100)

    def test_larger_error_lowers_score(self):
        scores = []
        for error in (10, 100, 1000):
            s = self.oracle()
            for p in s["predictions"]:
                p["prediction"] += error
            scores.append(b.evaluate(self.bundle, s, bootstrap=0)["score"])
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_score_is_not_accuracy_percent(self):
        self.assertEqual(b.score_transform(0), 100)
        self.assertEqual(b.score_transform(1), 50)
        self.assertAlmostEqual(b.score_transform(2), 100 / 3)

    def test_missing_case_no_score(self):
        s = copy.deepcopy(self.submission)
        s["predictions"].pop()
        r = b.evaluate(self.bundle, s)
        self.assertFalse(r["eligible"])
        self.assertIsNone(r["score"])
        self.assertLess(r["coverage"], 1)

    def test_duplicate_and_unknown_rejected(self):
        for kind in ("duplicate", "unknown"):
            s = copy.deepcopy(self.submission)
            extra = copy.deepcopy(s["predictions"][0])
            if kind == "unknown":
                extra["case_id"] = "unknown"
            s["predictions"].append(extra)
            with self.assertRaises(ValueError):
                b.evaluate(self.bundle, s)

    def test_nonfinite_and_negative_predictions(self):
        for value in (float("nan"), float("inf"), -1, True, "100"):
            s = copy.deepcopy(self.submission)
            s["predictions"][0]["prediction"] = value
            r = b.evaluate(self.bundle, s)
            self.assertIsNone(r["score"])

    def test_foreign_benchmark_rejected(self):
        s = copy.deepcopy(self.submission)
        s["benchmark_id"] = "wrong"
        with self.assertRaises(ValueError):
            b.evaluate(self.bundle, s)

    def test_modified_bundle_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["scales"][next(iter(bundle["scales"]))] *= 2
        with self.assertRaises(ValueError):
            b.evaluate(bundle, self.submission)

    def test_reproducible_freeze_and_bootstrap(self):
        self.assertEqual(b.freeze(self.data, 8)["benchmark_id"], self.bundle["benchmark_id"])
        self.assertEqual(b.evaluate(self.bundle, self.submission, bootstrap=20),
                         b.evaluate(self.bundle, self.submission, bootstrap=20))

    def test_future_mutation_leaves_prefix_and_forecast_unchanged(self):
        e = copy.deepcopy(self.data["events"][8])
        task = b.make_task(e, "1000", 24, self.bundle["protocol"])
        for point in e["tiers"]["1000"]["points"]:
            if point["time"] > task["issued_at"]:
                point["ep"] += 9_000_000
        e["tiers"]["1000"]["label"]["ep"] += 9_000_000
        changed = b.make_task(e, "1000", 24, self.bundle["protocol"])
        self.assertEqual(task, changed)
        self.assertEqual(b.linear24(task), b.linear24(changed))

    def test_test_labels_do_not_change_calibration(self):
        data = copy.deepcopy(self.data)
        for e in data["events"][8:]:
            for series in e["tiers"].values():
                series["label"]["ep"] += 1000
                series["points"][-1]["ep"] += 1000
        changed = b.freeze(data, 8)
        self.assertEqual(changed["scales"], self.bundle["scales"])
        self.assertEqual(changed["calibration"], self.bundle["calibration"])
        self.assertEqual(changed["tasks"], self.bundle["tasks"])
        self.assertNotEqual(changed["benchmark_id"], self.bundle["benchmark_id"])

    def test_no_test_truth_in_public_input(self):
        self.assertNotIn("truth", self.public)
        for task in self.public["tasks"]:
            self.assertNotIn("label", task)
            self.assertTrue(all(p["time"] <= task["issued_at"] for p in task["history"]))
        self.assertLess(max(e["end_at"] for e in self.public["calibration"]["events"]),
                        min(t["start_at"] for t in self.public["tasks"]))

    def test_coarse_sampling_no_interpolation(self):
        points = [{"time": h * b.HOUR, "ep": h} for h in (0, 5, 7)]
        result = b.coarse_history(points, 0, 6 * b.HOUR)
        self.assertEqual(result[-1]["ep"], 5)
        self.assertEqual(result[-1]["time"], 5 * b.HOUR)
        self.assertEqual(result[-1]["slot_time"], 6 * b.HOUR)

    def test_delayed_observation_not_available(self):
        points = [{"time": 0, "ep": 0}, {"time": 6 * b.HOUR, "ep": 6, "available_at": 7 * b.HOUR}]
        result = b.coarse_history(points, 0, 6 * b.HOUR)
        self.assertEqual([p["ep"] for p in result], [0])

    def test_final_flag_never_input(self):
        points = [{"time": 0, "ep": 0}, {"time": 6 * b.HOUR, "ep": 99, "isFinal": True}]
        self.assertEqual(len(b.coarse_history(points, 0, 6 * b.HOUR)), 1)

    def test_no_artificial_zero_or_unlimited_forward_fill(self):
        self.assertEqual(b.coarse_history([], 0, b.HOUR), [])
        points = [{"time": 0, "ep": 1}]
        result = b.coarse_history(points, 0, 24 * b.HOUR)
        self.assertEqual(len(result), 1)

    def test_coarse_reduces_dense_history(self):
        points = [{"time": h * b.HOUR // 60, "ep": h} for h in range(48 * 60 + 1)]
        self.assertEqual(len(b.coarse_history(points, 0, 48 * b.HOUR)), 9)

    def test_pt_and_duplicate_validation(self):
        for points in ([{"time": 1, "ep": 10}, {"time": 2, "ep": 9}],
                       [{"time": 1, "ep": 10}, {"time": 1, "ep": 11}]):
            with self.assertRaises(ValueError):
                b.normalize_points(points)
        self.assertEqual(len(b.normalize_points([{"time": 1, "ep": 10}] * 2)), 1)
        self.assertEqual(b.normalize_points([None, {"time": 1, "ep": 10}]),
                         [{"time": 1, "ep": 10.0, "isFinal": False}])

    def test_final_label_conflict_rejected(self):
        data = copy.deepcopy(self.data)
        data["events"][0]["tiers"]["1000"]["label"]["ep"] += 1
        with self.assertRaises(ValueError):
            b.validate_dataset(data)

    def test_provisional_label_not_silently_accepted(self):
        data = copy.deepcopy(self.data)
        data["events"][0]["tiers"]["1000"]["label"]["quality"] = "last_observation"
        with self.assertRaises(ValueError):
            b.validate_dataset(data)

    def test_synthetic_dataset_cannot_masquerade_as_real(self):
        data = copy.deepcopy(self.data)
        data["synthetic"] = False
        with self.assertRaises(ValueError):
            b.validate_dataset(data)

    def test_label_must_not_predate_end(self):
        data = copy.deepcopy(self.data)
        data["events"][0]["tiers"]["1000"]["label"]["time"] -= b.HOUR
        with self.assertRaises(ValueError):
            b.validate_dataset(data)

    def test_cn_chronology_not_numeric_id(self):
        data = copy.deepcopy(self.data)
        order = [312, 313, 311, 310, 314]
        events = data["events"][:5]
        transition = events[3]["start_at"]
        for e, eid in zip(events, order):
            e.update(server="cn", event_id=eid, era=b.cn_era(eid, e["start_at"], transition))
        data["events"] = list(reversed(events))
        validated = b.validate_dataset(data)
        self.assertEqual([e["event_id"] for e in validated], order)
        bundle = b.freeze(data, 3)
        self.assertEqual(bundle["protocol"]["test_event_ids"], [310, 314])
        self.assertEqual({t["era"] for t in bundle["tasks"]}, {"voice500_1500"})

    def test_cn_early_312_313_and_future_timestamp_rule(self):
        self.assertEqual(b.cn_era(312, 1, 100), "voice1000")
        self.assertEqual(b.cn_era(313, 2, 100), "voice1000")
        self.assertEqual(b.cn_era(311, 99, 100), "voice1000")
        self.assertEqual(b.cn_era(310, 100, 100), "voice500_1500")
        self.assertEqual(b.cn_era(314, 101, None), "voice500_1500")
        self.assertEqual(b.cn_era(400, 50, 100), "voice1000")
        self.assertEqual(b.cn_era(300, 110, 100), "voice500_1500")
        self.assertEqual(b.cn_era(400, 50, None), "unknown")

    def test_calibration_overlap_rejected(self):
        data = copy.deepcopy(self.data)
        data["events"][7]["end_at"] = data["events"][8]["start_at"] + b.HOUR
        for series in data["events"][7]["tiers"].values():
            series["label"]["time"] = data["events"][7]["end_at"]
        with self.assertRaises(ValueError):
            b.freeze(data, 8)

    def test_wis_degenerate_equals_absolute_error(self):
        self.assertAlmostEqual(b.weighted_interval_score({str(q): 90 for q in b.QUANTILES}, 100), 10)

    def test_wis_wide_interval_costs(self):
        perfect = {str(q): 100 for q in b.QUANTILES}
        wide = dict(zip(map(str, b.QUANTILES), (0, 10, 25, 100, 175, 190, 200)))
        self.assertEqual(b.weighted_interval_score(perfect, 100), 0)
        self.assertGreater(b.weighted_interval_score(wide, 100), 0)

    def test_crossing_missing_and_nan_quantiles_rejected(self):
        good = {str(q): 100 for q in b.QUANTILES}
        for bad in ({**good, "0.1": 101}, {"0.5": 100}, {**good, "0.1": math.nan}):
            with self.assertRaises(ValueError):
                b.weighted_interval_score(bad, 100)

    def test_point_not_auto_probabilistic(self):
        r = b.evaluate(self.bundle, self.submission, "probabilistic")
        self.assertIsNone(r["score"])
        self.assertFalse(r["eligible"])

    def test_probability_baseline_and_median_consistency(self):
        s = b.predict(self.public, "linear24-quantiles")
        r = b.evaluate(self.bundle, s, "probabilistic", 0)
        self.assertTrue(r["eligible"])
        self.assertIn("coverage90", r)
        s["predictions"][0]["prediction"] += 1
        self.assertIsNone(b.evaluate(self.bundle, s, "probabilistic", 0)["score"])

    def test_baseline_coefficients_are_calibration_only(self):
        self.assertIn("jp:synthetic:1000", self.public["calibration"]["rates"])
        s = b.predict(self.public, "bestdori-recalibrated")
        self.assertTrue(b.evaluate(self.bundle, s, bootstrap=0)["eligible"])
        self.assertIn("not_platform_archive", s["provenance"])

    def test_missing_rate_does_not_invent_fallback(self):
        public = copy.deepcopy(self.public)
        public["calibration"]["rates"] = {}
        s = b.predict(public, "bestdori-recalibrated")
        self.assertIsNone(b.evaluate(self.bundle, s)["score"])

    def test_macro_cells_not_sample_density(self):
        rows = [{"era": "a", "tier": 1000, "horizon_hours": 24, "scaled_loss": 1},
                {"era": "a", "tier": 1000, "horizon_hours": 6, "scaled_loss": 3}]
        self.assertEqual(b.macro(rows), 2)
        self.assertEqual(b.macro([rows[0]] * 100 + [rows[1]]), 2)

    def test_only_whole_event_tier_excluded(self):
        data = copy.deepcopy(self.data)
        e = data["events"][8]
        e["tiers"]["1000"]["points"] = e["tiers"]["1000"]["points"][:2]
        bundle = b.freeze(data, 8)
        self.assertEqual(len(bundle["tasks"]), 145)
        self.assertTrue(bundle["exclusions"])
        self.assertFalse(any(t["event_id"] == 9 and t["tier"] == 1000 for t in bundle["tasks"]))

    def test_frozen_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "release"
            b.write_frozen(out, self.bundle)
            with self.assertRaises(ValueError):
                b.write_frozen(out, self.bundle)

    def test_cli_freeze_predict_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            b.save(root / "data.json", self.data)
            self.assertEqual(b.main(["freeze", str(root / "data.json"), "--calibration-events", "8", "--out", str(root / "release")]), 0)
            self.assertEqual(b.main(["predict", str(root / "release/public/tasks.json"), "--out", str(root / "pred.json")]), 0)
            self.assertEqual(b.main(["score", str(root / "release/private/benchmark.json"), str(root / "pred.json"), "--bootstrap", "0", "--out", str(root / "result.json")]), 0)
            self.assertTrue(b.load(root / "result.json")["eligible"])

    def test_real_collector_with_mocked_transport(self):
        # No external request is made. The API body is deliberately synthetic.
        start = 1_600_000_000_000
        stop = start + 168 * b.HOUR
        aggregate_end = stop + b.HOUR
        index = {"1": {"startAt": [str(start)]}}
        archives = {"1": {"cutoff": [{}, {}, {}, {}, {}], "board": [[], [], [], [], []]}}
        detail = {"startAt": [str(start)], "endAt": [str(stop)],
                  "aggregateEndAt": [str(aggregate_end)], "eventType": "test"}
        points = [{"time": start + h * b.HOUR, "ep": h * 100} for h in range(0, 169, 6)]
        points.append({"time": stop + 60_000, "ep": 16800})
        def fake_get(client, url):
            if "events/all.3.json" in url:
                return index
            if "archives/all.5.json" in url:
                return archives
            if "/events/1.json" in url:
                return detail
            return {"result": True, "cutoffs": points}
        with tempfile.TemporaryDirectory() as tmp, patch.object(b.PublicClient, "get", fake_get):
            self.assertEqual(b.main(["collect", "--recent", "1", "--tiers", "1000", "--out", tmp]), 0)
            data = b.load(Path(tmp) / "dataset.json")
            self.assertEqual(len(b.validate_dataset(data)), 1)
            event = data["events"][0]
            self.assertEqual(event["end_at"], stop)
            self.assertEqual(event["aggregate_end_at"], aggregate_end)
            self.assertEqual(event["tiers"]["1000"]["label"]["quality"], "post_end_final")
            self.assertEqual(event["tiers"]["1000"]["label"]["ep"], 16800)

    def test_archive_cutoff_reader(self):
        archives = {"7": {"cutoff": [{"100": 123, "1000": 456}, {}, {}, {}, {}]}}
        self.assertEqual(b.archive_cutoff(archives, 7, "jp", 1000), 456)
        self.assertIsNone(b.archive_cutoff(archives, 8, "jp", 1000))
        self.assertIsNone(b.archive_cutoff(archives, 7, "cn", 1000))

    def test_collector_prefers_archive_final(self):
        start = 1_600_000_000_000
        stop = start + 168 * b.HOUR
        aggregate_end = stop + b.HOUR
        index = {"1": {"startAt": [str(start)]}}
        archives = {"1": {"cutoff": [{"1000": 17000}, {}, {}, {}, {}], "board": [[], [], [], [], []]}}
        detail = {"startAt": [str(start)], "endAt": [str(stop)],
                  "aggregateEndAt": [str(aggregate_end)], "eventType": "test"}
        points = [{"time": start + h * b.HOUR, "ep": h * 100} for h in range(0, 169, 6)]
        points.append({"time": stop + 60_000, "ep": 16800})
        def fake_get(client, url):
            if "events/all.3.json" in url:
                return index
            if "archives/all.5.json" in url:
                return archives
            if "/events/1.json" in url:
                return detail
            return {"result": True, "cutoffs": points}
        with tempfile.TemporaryDirectory() as tmp, patch.object(b.PublicClient, "get", fake_get):
            self.assertEqual(b.main(["collect", "--recent", "1", "--tiers", "1000", "--out", tmp]), 0)
            data = b.load(Path(tmp) / "dataset.json")
            self.assertEqual(data["events"][0]["tiers"]["1000"]["label"]["quality"], "archive_final")
            self.assertEqual(data["events"][0]["tiers"]["1000"]["label"]["ep"], 17000)

    def test_collector_rejects_disagreeing_post_end_observations(self):
        start = 1_600_000_000_000
        stop = start + 168 * b.HOUR
        aggregate_end = stop + b.HOUR
        index = {"1": {"startAt": [str(start)]}}
        archives = {"1": {"cutoff": [{}, {}, {}, {}, {}], "board": [[], [], [], [], []]}}
        detail = {"startAt": [str(start)], "endAt": [str(stop)],
                  "aggregateEndAt": [str(aggregate_end)], "eventType": "test"}
        points = [{"time": start + h * b.HOUR, "ep": h * 100} for h in range(0, 169, 6)]
        points += [{"time": stop + 60_000, "ep": 16800},
                   {"time": stop + 30 * 60_000, "ep": 16900}]
        def fake_get(client, url):
            if "events/all.3.json" in url:
                return index
            if "archives/all.5.json" in url:
                return archives
            if "/events/1.json" in url:
                return detail
            return {"result": True, "cutoffs": points}
        with tempfile.TemporaryDirectory() as tmp, patch.object(b.PublicClient, "get", fake_get):
            self.assertEqual(b.main(["collect", "--recent", "1", "--tiers", "1000", "--out", tmp]), 0)
            data = b.load(Path(tmp) / "dataset.json")
            self.assertEqual(data["events"][0]["tiers"], {})

    def test_collector_does_not_guess_pre_aggregate_last_observation(self):
        start = 1_600_000_000_000
        stop = start + 168 * b.HOUR
        aggregate_end = stop + b.HOUR
        index = {"1": {"startAt": [str(start)]}}
        archives = {"1": {"cutoff": [{}, {}, {}, {}, {}], "board": [[], [], [], [], []]}}
        detail = {"startAt": [str(start)], "endAt": [str(stop)],
                  "aggregateEndAt": [str(aggregate_end)], "eventType": "test"}
        points = [{"time": start + h * b.HOUR, "ep": h * 100} for h in range(0, 169, 6)]
        def fake_get(client, url):
            if "events/all.3.json" in url:
                return index
            if "archives/all.5.json" in url:
                return archives
            if "/events/1.json" in url:
                return detail
            return {"result": True, "cutoffs": points}
        with tempfile.TemporaryDirectory() as tmp, patch.object(b.PublicClient, "get", fake_get):
            self.assertEqual(b.main(["collect", "--recent", "1", "--tiers", "1000", "--out", tmp]), 0)
            data = b.load(Path(tmp) / "dataset.json")
            self.assertEqual(len(data["events"]), 1)
            self.assertEqual(data["events"][0]["tiers"], {})

    def test_version_markers_match(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "VERSION").read_text().strip(), b.VERSION)
        self.assertEqual(tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"], b.VERSION)


if __name__ == "__main__":
    unittest.main()
