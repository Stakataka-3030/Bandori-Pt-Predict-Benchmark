"""Offline deterministic regression tests; all event data here is synthetic."""
import copy
import json
import math
import sys
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
        points = [{"time": start + h * b.HOUR, "ep": h * 100} for h in range(0, 168, 6)]
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

    def test_freeze_splits_only_complete_requested_tier_events(self):
        data = copy.deepcopy(self.data)
        del data["events"][0]["tiers"]["100"]
        del data["events"][3]["tiers"]["2000"]
        bundle = b.freeze(data, 8)
        self.assertNotIn(1, bundle["protocol"]["calibration_event_ids"])
        self.assertNotIn(4, bundle["protocol"]["calibration_event_ids"])
        self.assertEqual(len(bundle["protocol"]["incomplete_events_excluded"]), 2)
        for task in bundle["tasks"]:
            self.assertIn(task["tier"], data["requested_tiers"])


    def test_walkforward_raw_protocol_and_causal_history_ids(self):
        bundle = b.freeze_walkforward(self.data, 8)
        self.assertEqual(bundle["protocol"]["schema"], "bandoribench-protocol-v2")
        self.assertEqual(bundle["protocol"]["mode"], "expanding_walk_forward_raw")
        self.assertEqual(len(bundle["tasks"]), 150)
        first = next(t for t in bundle["tasks"] if t["event_id"] == 9)
        second = next(t for t in bundle["tasks"] if t["event_id"] == 10)
        self.assertEqual(first["history_event_ids"], list(range(1, 9)))
        self.assertEqual(second["history_event_ids"], list(range(1, 10)))
        self.assertTrue(all("slot_time" not in p for p in first["history"]))

    def test_walkforward_excludes_only_bad_event_horizon_panel(self):
        data = copy.deepcopy(self.data)
        event = data["events"][8]  # first target when warmup_events=8
        bad_time = event["end_at"] - 24 * b.HOUR
        points = event["tiers"]["1000"]["points"]
        event["tiers"]["1000"]["points"] = [p for p in points if p["time"] != bad_time]

        bundle = b.freeze_walkforward(data, 8)
        protocol = bundle["protocol"]
        self.assertEqual(protocol["target_case_count_before_eligibility"], 150)
        self.assertEqual(protocol["eligible_case_count"], 147)
        self.assertEqual(protocol["excluded_case_count"], 3)
        self.assertEqual(protocol["fully_excluded_target_event_ids"], [])
        self.assertEqual(protocol["eligible_target_event_ids"], protocol["target_event_ids"])
        self.assertEqual(
            protocol["excluded_cases_by_reason"],
            {"incomplete multi-tier panel": 2, "insufficient or stale raw history": 1},
        )

        excluded = [x for x in bundle["exclusions"]
                    if x.get("stage") == "hindcast" and x["event"] == 9]
        self.assertEqual(len(excluded), 3)
        self.assertTrue(all(x["horizon"] == 24 for x in excluded))
        self.assertTrue(all(x["panel_failures"] == {"1000": "insufficient or stale raw history"}
                            for x in excluded))

        event9 = [t for t in bundle["tasks"] if t["event_id"] == 9]
        self.assertEqual(len(event9), 12)
        self.assertFalse(any(t["horizon_hours"] == 24 for t in event9))
        self.assertEqual({t["tier"] for t in event9 if t["horizon_hours"] == 12},
                         {100, 1000, 2000})

        panels = {}
        for task in bundle["tasks"]:
            panels.setdefault((task["event_id"], task["horizon_hours"]), set()).add(task["tier"])
        self.assertTrue(all(tiers == {100, 1000, 2000} for tiers in panels.values()))

    def test_walkforward_reports_fully_excluded_target_event(self):
        data = copy.deepcopy(self.data)
        event = data["events"][8]
        for tier in ("100", "1000", "2000"):
            points = event["tiers"][tier]["points"]
            event["tiers"][tier]["points"] = points[:2] + [points[-1]]

        bundle = b.freeze_walkforward(data, 8)
        protocol = bundle["protocol"]
        self.assertEqual(protocol["target_case_count_before_eligibility"], 150)
        self.assertEqual(protocol["eligible_case_count"], 135)
        self.assertEqual(protocol["excluded_case_count"], 15)
        self.assertEqual(protocol["fully_excluded_target_event_ids"], [9])
        self.assertNotIn(9, protocol["eligible_target_event_ids"])
        self.assertFalse(any(t["event_id"] == 9 for t in bundle["tasks"]))

    def test_raw_history_preserves_dense_observations(self):
        points = [{"time": m * b.HOUR // 60, "ep": m} for m in range(48 * 60 + 1)]
        raw = b.raw_history(points, 0, 48 * b.HOUR)
        coarse = b.coarse_history(points, 0, 48 * b.HOUR)
        self.assertEqual(len(raw), 48 * 60 + 1)
        self.assertEqual(len(coarse), 9)

    def test_walkforward_baselines_are_full_coverage(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        for model in ("persistence", "linear24", "calibrated-linear24",
                      "linear24-quantiles", "bestdori-hierarchical",
                      "hhwx-instant", "hhwx-24h", "rinko-dpra-replay"):
            submission = b.predict(public, model)
            track = "probabilistic" if model == "linear24-quantiles" else "point"
            report = b.evaluate(bundle, submission, track, bootstrap=0)
            self.assertTrue(report["eligible"], (model, report["failures"][:3]))
            self.assertEqual(report["coverage"], 1.0)

    def test_walkforward_future_reference_label_does_not_change_earlier_baseline(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        target = next(t for t in bundle["tasks"] if t["event_id"] == 9 and t["tier"] == 1000 and t["horizon_hours"] == 24)
        before = {p["case_id"]: p for p in b.predict(public, "calibrated-linear24")["predictions"]}[target["case_id"]]
        changed = copy.deepcopy(public)
        future = next(e for e in changed["reference_events"] if e["event_id"] == 18)
        future["tiers"]["1000"]["label"]["ep"] += 999_999_999
        after = {p["case_id"]: p for p in b.predict(changed, "calibrated-linear24")["predictions"]}[target["case_id"]]
        self.assertEqual(before, after)

    def test_probability_report_has_calibration_diagnostics(self):
        bundle = b.freeze_walkforward(self.data, 8)
        submission = b.predict(b.public_bundle(bundle), "linear24-quantiles")
        report = b.evaluate(bundle, submission, "probabilistic", bootstrap=0)
        for key in ("coverage50", "coverage90", "mean_interval_width50",
                    "mean_interval_width90", "median_bias"):
            self.assertIn(key, report)


    def test_multitier_analog_ensemble_is_causal_and_full_coverage(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        submission = b.predict(public, "multitier-analog-ensemble")
        point = b.evaluate(bundle, submission, "point", bootstrap=0)
        prob = b.evaluate(bundle, submission, "probabilistic", bootstrap=0)
        self.assertTrue(point["eligible"], point["failures"][:3])
        self.assertTrue(prob["eligible"], prob["failures"][:3])
        self.assertEqual(point["coverage"], 1.0)
        self.assertEqual(prob["coverage"], 1.0)
        self.assertIn("ensemble", submission["predictions"][0])

    def test_multitier_analog_ignores_future_reference_truth(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        target = next(t for t in bundle["tasks"] if t["event_id"] == 9 and t["tier"] == 1000 and t["horizon_hours"] == 24)
        before = b.multitier_analog_ensemble(public, target)
        changed = copy.deepcopy(public)
        for e in changed["reference_events"]:
            if e["event_id"] > 9:
                for series in e["tiers"].values():
                    series["label"]["ep"] += 999_999_999
        after = b.multitier_analog_ensemble(changed, target)
        self.assertEqual(before, after)


    def test_hhwx_projection_uses_newest_eligible_reference(self):
        task = {
            "start_at": 0,
            "end_at": 2 * b.HOUR,
            "history": [
                {"time": 5 * 60_000, "ep": 100.0},
                {"time": 10 * 60_000, "ep": 200.0},
                {"time": 20 * 60_000, "ep": 500.0},
            ],
        }
        # Latest=20m, newest point at least 9m45 behind is 10m.
        self.assertEqual(b.hhwx_projection(task, "instant"), 3500.0)

    def test_hhwx_day_projection_uses_2355_window(self):
        task = {
            "start_at": 0,
            "end_at": 72 * b.HOUR,
            "history": [{"time": h * b.HOUR, "ep": h * 100.0} for h in range(49)],
        }
        # Latest=48h; newest point >=23h55 behind is 24h.
        self.assertEqual(b.hhwx_projection(task, "24h"), 7200.0)

    def test_rinko_dpra_replay_is_finite_and_prefix_only(self):
        bundle = b.freeze_walkforward(self.data, 8)
        task = next(t for t in bundle["tasks"]
                    if t["event_id"] == 9 and t["tier"] == 1000 and t["horizon_hours"] == 24)
        value = b.rinko_dpra_replay(task)
        self.assertTrue(math.isfinite(value))
        changed = copy.deepcopy(task)
        changed["end_at"] += 0  # explicit: replay uses only the task prefix plus known event window.
        self.assertEqual(value, b.rinko_dpra_replay(changed))


    def test_calendar_freezes_into_walkforward_bundle(self):
        calendar = {
            "schema": "bandoribench-calendar-v1",
            "server": "jp",
            "utc_offset_hours": 9,
            "days": {"2020-09-21": "holiday"},
        }
        bundle = b.freeze_walkforward(self.data, 8, calendar=calendar)
        self.assertIn("calendar", bundle)
        self.assertEqual(bundle["protocol"]["calendar_sha256"], b.digest(bundle["calendar"]))
        self.assertEqual(b.public_bundle(bundle)["calendar"]["days"]["2020-09-21"]["type"], "holiday")

    def test_calendar_known_at_prevents_late_schedule_leak(self):
        timestamp = 1_600_000_000_000
        local_day = b._local_datetime(timestamp, "jp").strftime("%Y-%m-%d")
        calendar = b.validate_calendar({
            "schema": "bandoribench-calendar-v1",
            "server": "jp",
            "utc_offset_hours": 9,
            "days": {local_day: {"type": "holiday", "known_at": timestamp + b.HOUR}},
        }, "jp")
        fallback = "weekend" if b._local_datetime(timestamp, "jp").weekday() >= 5 else "weekday"
        self.assertEqual(b._calendar_day_type(timestamp, "jp", calendar, timestamp), fallback)
        self.assertEqual(b._calendar_day_type(timestamp, "jp", calendar, timestamp + 2 * b.HOUR), "holiday")

    def test_cn_calendar_provider_has_official_adjustments(self):
        from calendar_provider.china import build_china_calendar
        calendar = build_china_calendar([2019, 2020, 2024, 2026])
        self.assertEqual(calendar["days"]["2019-05-02"]["type"], "holiday")
        self.assertEqual(calendar["days"]["2019-04-28"]["type"], "makeup_workday")
        self.assertEqual(calendar["days"]["2024-02-04"]["type"], "makeup_workday")
        self.assertEqual(calendar["days"]["2026-02-23"]["type"], "holiday")
        revised = calendar["days"]["2020-02-01"]
        self.assertEqual(revised["type"], "holiday")
        self.assertEqual(revised["previous_type"], "makeup_workday")
        self.assertLess(revised["previous_known_at"], revised["known_at"])
        with self.assertRaises(ValueError):
            build_china_calendar([2018])

    def test_calendar_revision_preserves_preannouncement_state(self):
        from calendar_provider.china import build_china_calendar
        calendar = b.validate_calendar(build_china_calendar([2020]), "cn")
        revised = calendar["days"]["2020-02-01"]
        future_day = revised["known_at"] + 4 * 24 * b.HOUR
        self.assertEqual(
            b._calendar_day_type(future_day, "cn", calendar, revised["previous_known_at"] - 1),
            "weekend",
        )
        self.assertEqual(
            b._calendar_day_type(future_day, "cn", calendar, revised["previous_known_at"]),
            "makeup_workday",
        )
        self.assertEqual(
            b._calendar_day_type(future_day, "cn", calendar, revised["known_at"]),
            "holiday",
        )

    def test_calendar_fetch_cli_and_committed_snapshot(self):
        from calendar_provider.china import build_china_calendar
        root = Path(__file__).resolve().parents[1]
        expected = build_china_calendar(range(2019, 2027))
        self.assertEqual(expected["days"]["2022-12-31"]["type"], "holiday")
        self.assertEqual(b.load(root / "calendars" / "cn-2019-2026.json"), expected)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cn.json"
            self.assertEqual(
                b.main(["calendar-fetch", "--server", "cn", "--years", "2024", "2025",
                        "--out", str(out)]),
                0,
            )
            generated = b.load(out)
            self.assertEqual(generated["years"], [2024, 2025])
            self.assertEqual(generated["days"]["2025-10-11"]["type"], "makeup_workday")

    def test_care_s_is_full_coverage_and_probabilistic(self):
        bundle = b.freeze_walkforward(self.data, 8)
        submission = b.predict(b.public_bundle(bundle), "care-s")
        point = b.evaluate(bundle, submission, "point", bootstrap=0)
        prob = b.evaluate(bundle, submission, "probabilistic", bootstrap=0)
        self.assertTrue(point["eligible"], point["failures"][:3])
        self.assertTrue(prob["eligible"], prob["failures"][:3])
        self.assertEqual(point["coverage"], 1.0)
        self.assertEqual(prob["coverage"], 1.0)
        self.assertIn("care", submission["predictions"][0])

    def test_care_s_does_not_use_future_reference_truth(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        target_id = "jp:9:1000:24"
        before = {p["case_id"]: p for p in b.predict(public, "care-s")["predictions"]}[target_id]
        changed = copy.deepcopy(public)
        for event in changed["reference_events"]:
            if event["event_id"] > 9:
                for series in event["tiers"].values():
                    series["label"]["ep"] += 999_999_999
        after = {p["case_id"]: p for p in b.predict(changed, "care-s")["predictions"]}[target_id]
        self.assertEqual(before, after)

    def test_care_s_quantiles_and_tiers_are_ordered(self):
        bundle = b.freeze_walkforward(self.data, 8)
        submission = b.predict(b.public_bundle(bundle), "care-s")
        rows = {p["case_id"]: p for p in submission["predictions"]}
        selected = [next(t for t in bundle["tasks"]
                         if t["event_id"] == 9 and t["horizon_hours"] == 24 and t["tier"] == tier)
                    for tier in (100, 1000, 2000)]
        for task in selected:
            q = [rows[task["case_id"]]["quantiles"][str(x)] for x in b.QUANTILES]
            self.assertEqual(q, sorted(q))
            self.assertGreaterEqual(rows[task["case_id"]]["prediction"], task["history"][-1]["ep"])
        medians = [rows[t["case_id"]]["prediction"] for t in selected]
        self.assertGreaterEqual(medians[0], medians[1])
        self.assertGreaterEqual(medians[1], medians[2])


    def test_walkforward_tier_override_builds_common_panel(self):
        data = copy.deepcopy(self.data)
        data["requested_tiers"] = [100, 1000, 2000]
        for event in data["events"]:
            event["tiers"].pop("2000", None)
        bundle = b.freeze_walkforward(data, 8, requested_tiers_override=(100, 1000))
        self.assertEqual(bundle["protocol"]["requested_tiers"], [100, 1000])
        self.assertTrue(all(task["tier"] in (100, 1000) for task in bundle["tasks"]))

    def test_care_s2_is_full_coverage_and_adaptive(self):
        bundle = b.freeze_walkforward(self.data, 8)
        submission = b.predict(b.public_bundle(bundle), "care-s2")
        point = b.evaluate(bundle, submission, "point", bootstrap=0)
        prob = b.evaluate(bundle, submission, "probabilistic", bootstrap=0)
        self.assertTrue(point["eligible"], point["failures"][:3])
        self.assertTrue(prob["eligible"], prob["failures"][:3])
        self.assertEqual(point["coverage"], 1.0)
        self.assertEqual(prob["coverage"], 1.0)
        metas = [row["care"] for row in submission["predictions"]]
        self.assertTrue(all(meta["correction_shrinkage"] in (0.0, 0.25, 0.5, 0.75, 1.0) for meta in metas))
        self.assertTrue(all(meta["residual_scale"] in (0.0, 0.25, 0.5, 0.75, 1.0) for meta in metas))

    def test_care_s2_future_truth_invariance(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        target_id = "jp:9:1000:24"
        before = {p["case_id"]: p for p in b.predict(public, "care-s2")["predictions"]}[target_id]
        changed = copy.deepcopy(public)
        for event in changed["reference_events"]:
            if event["event_id"] > 9:
                for series in event["tiers"].values():
                    series["label"]["ep"] += 999_999_999
        after = {p["case_id"]: p for p in b.predict(changed, "care-s2")["predictions"]}[target_id]
        self.assertEqual(before, after)


    def test_causal_stack_is_full_coverage_and_probabilistic(self):
        bundle = b.freeze_walkforward(self.data, 8)
        submission = b.predict(b.public_bundle(bundle), "causal-stack")
        point = b.evaluate(bundle, submission, "point", bootstrap=0)
        prob = b.evaluate(bundle, submission, "probabilistic", bootstrap=0)
        self.assertTrue(point["eligible"], point["failures"][:3])
        self.assertTrue(prob["eligible"], prob["failures"][:3])
        self.assertEqual(point["coverage"], 1.0)
        self.assertEqual(prob["coverage"], 1.0)
        self.assertTrue(all(0.0 <= row["stack"]["bestdori_weight"] <= 1.0
                            for row in submission["predictions"]))

    def test_causal_stack_future_truth_invariance(self):
        bundle = b.freeze_walkforward(self.data, 8)
        public = b.public_bundle(bundle)
        target_id = "jp:9:1000:24"
        before = {p["case_id"]: p for p in b.predict(public, "causal-stack")["predictions"]}[target_id]
        changed = copy.deepcopy(public)
        for event in changed["reference_events"]:
            if event["event_id"] > 9:
                for series in event["tiers"].values():
                    series["label"]["ep"] += 999_999_999
        after = {p["case_id"]: p for p in b.predict(changed, "causal-stack")["predictions"]}[target_id]
        self.assertEqual(before, after)

    def test_l1_stack_weight_uses_prior_errors(self):
        records = [
            {"analog_point": 100.0, "bestdori_point": 200.0, "final": 180.0},
            {"analog_point": 100.0, "bestdori_point": 200.0, "final": 160.0},
            {"analog_point": 100.0, "bestdori_point": 200.0, "final": 170.0},
        ]
        self.assertAlmostEqual(b._l1_stack_weight(records), 0.7)

    def test_model_evaluation_plan_and_training_export_are_chronological(self):
        bundle = b.freeze_walkforward(self.data, 8)
        plan = b.model_evaluation_plan(bundle)
        phases = plan["phases"]
        self.assertEqual(len(phases["development"]["target_event_ids"]), 6)
        self.assertEqual(len(phases["selection"]["target_event_ids"]), 2)
        self.assertEqual(len(phases["final"]["target_event_ids"]), 2)
        self.assertEqual(
            phases["development"]["target_event_ids"]
            + phases["selection"]["target_event_ids"]
            + phases["final"]["target_event_ids"],
            bundle["protocol"]["target_event_ids"],
        )
        self.assertEqual(
            phases["final"]["initial_training_event_ids"],
            bundle["protocol"]["warmup_event_ids"]
            + phases["development"]["target_event_ids"]
            + phases["selection"]["target_event_ids"],
        )
        export = b.model_training_export(bundle, "final")
        self.assertEqual(export["training_event_ids"], phases["final"]["initial_training_event_ids"])
        final_ids = set(phases["final"]["target_event_ids"])
        self.assertTrue(final_ids.isdisjoint({e["event_id"] for e in export["events"]}))
        self.assertEqual(export["training_cutoff_ms"], phases["final"]["initial_training_cutoff_ms"])

    def test_model_api_persistent_runner_scores_development_without_future_truth(self):
        bundle = b.freeze_walkforward(self.data, 8)
        root = Path(__file__).resolve().parents[1]
        runner = [sys.executable, str(root / "examples" / "persistence_model.py")]
        submission, report = b.run_model_api(
            bundle, runner, phase="development", track="point", bootstrap=0
        )
        plan = b.model_evaluation_plan(bundle)
        self.assertTrue(report["eligible"], report["failures"][:3])
        self.assertTrue(report["model_api"]["protocol_eligible"])
        self.assertEqual(report["model_id"], "example-persistence")
        self.assertEqual(
            len(submission["predictions"]),
            len(plan["phases"]["development"]["case_ids"]),
        )
        self.assertEqual(len(report["temporal_checkpoints"]), 5)
        self.assertTrue(all(row["n_cases"] > 0 for row in report["temporal_checkpoints"]))

    def test_model_api_final_report_is_redacted(self):
        bundle = b.freeze_walkforward(self.data, 8)
        root = Path(__file__).resolve().parents[1]
        runner = [sys.executable, str(root / "examples" / "persistence_model.py")]
        _, report = b.run_model_api(bundle, runner, phase="final", track="point", bootstrap=0)
        self.assertTrue(report["eligible"])
        self.assertTrue(report["final_holdout"])
        self.assertNotIn("case_losses", report)
        self.assertNotIn("by_tier", report)
        self.assertEqual(report["temporal_checkpoints"], [])

    def test_version_markers_match(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "VERSION").read_text().strip(), b.VERSION)
        self.assertEqual(tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"], b.VERSION)


if __name__ == "__main__":
    unittest.main()
