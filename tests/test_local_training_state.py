"""Public completed-event refits must be causal, repeatable, and recoverable."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "examples" / "local_app"
sys.path.insert(0, str(APP_DIR))
import completed_data  # noqa: E402
import training_state as S  # noqa: E402

HOUR = 3600000


def seed_state():
    fit = {"tail_pool": {}, "multiplier_samples": {},
           "kaori_ratios": {}, "aoi_templates": {str(h): [] for h in (72, 48, 24, 12, 6)},
           "early_progress_paths": []}
    return {"schema": "tsukushi-local-state-v1",
            "fit": fit, "fit_sha256": S._digest(fit),
            "model_ids": {"control": "tsukushi-kaori", "members": "tsukushi-aoi"},
            "completed_event_ids": [1], "training_cutoff_at": 100 * HOUR,
            "new_era_first_observed_start_at": 0}


def finished_event():
    start, end = 110 * HOUR, 210 * HOUR
    tiers = {}
    for tier in (500, 1000, 1500, 2000):
        scale = 1 + tier / 1000
        rows = [{"time": start + i * HOUR, "ep": (10000 + i * 900) * scale}
                for i in range(0, 100, 4)]
        tiers[str(tier)] = {"points": rows,
                            "label": {"ep": 100000 * scale, "time": end + 1,
                                      "quality": "post_end_final"}}
    return {"event_id": 2, "server": "cn", "era": "voice500_1500",
            "event_type": "story", "start_at": start, "end_at": end,
            "aggregate_end_at": end + 600000, "tiers": tiers}


class TrainingStateTests(unittest.TestCase):
    def test_discovery_uses_start_time_and_excludes_ongoing_event(self):
        def metadata(start, end):
            return {"startAt": [None, None, None, str(start)],
                    "endAt": [None, None, None, str(end)]}
        index = {"311": metadata(300, 350), "310": metadata(200, 250),
                 "314": metadata(400, 700)}
        state = {"training_cutoff_at": 100, "completed_event_ids": []}
        with patch.object(completed_data.data_source, "fetch_json", return_value=index):
            self.assertEqual([row[2] for row in completed_data.eligible_events(state, 500)],
                             [310, 311])

    def test_completed_event_requires_post_end_stability(self):
        event = finished_event()
        end, aggregate = event["end_at"], event["aggregate_end_at"]
        meta = {"startAt": [None, None, None, str(event["start_at"])],
                "endAt": [None, None, None, str(end)],
                "aggregateEndAt": [None, None, None, str(aggregate)],
                "eventType": "story"}
        with patch.object(completed_data.data_source, "fetch_json", return_value=meta):
            with self.assertRaises(completed_data.PendingFinal):
                completed_data.completed_event(2, aggregate)
            def rows(_source, _event_id, tier, _issued):
                points = event["tiers"][str(tier)]["points"]
                return points + [{"time": end + 1, "ep": event["tiers"][str(tier)]["label"]["ep"]}], "public-url"
            with patch.object(completed_data.data_source, "tracker", side_effect=rows):
                learned, evidence = completed_data.completed_event(
                    2, aggregate + completed_data.ARCHIVE_GRACE_MS)
            self.assertEqual(learned["tiers"]["500"]["label"]["quality"], "post_end_final")
            self.assertEqual(len(evidence["records"]), 4)

            def conflict(_source, _event_id, tier, _issued):
                values, url = rows(_source, _event_id, tier, _issued)
                return values + [{"time": end + 2, "ep": values[-1]["ep"] + 1}], url
            with patch.object(completed_data.data_source, "tracker", side_effect=conflict):
                with self.assertRaises(completed_data.PendingFinal):
                    completed_data.completed_event(2, aggregate + completed_data.ARCHIVE_GRACE_MS)

    def test_refit_is_idempotent_and_corrupt_state_is_rebuilt(self):
        event = finished_event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "bundled.json"
            seed.write_text(json.dumps(seed_state()), encoding="utf-8")
            state = root / "model" / "tsukushi-state.json"
            def candidates(current, _now):
                return [] if 2 in current["completed_event_ids"] else [
                    (event["start_at"], event["end_at"], 2)]
            with (patch.object(completed_data, "eligible_events", side_effect=candidates),
                  patch.object(completed_data, "completed_event",
                               return_value=(event, {"source": "synthetic"}))):
                first = S.sync_once(state, seed, event["aggregate_end_at"] + 900000)
                second = S.sync_once(state, seed, event["aggregate_end_at"] + 900000)
            self.assertEqual(first["updated_event_ids"], [2])
            self.assertEqual(second["status"], "up_to_date")
            self.assertEqual(second["state"]["completed_event_ids"], [1, 2])
            self.assertEqual(len(S._read_store(S.training_path(state), seed_state())["updates"]), 1)
            fitted_hash = first["state"]["fit_sha256"]
            state.write_text("{}", encoding="utf-8")
            repaired, _, _ = S.load_or_create(state, seed)
            self.assertEqual(repaired["fit_sha256"], fitted_hash)
            self.assertEqual(repaired["training_cutoff_at"], event["end_at"])
            S.training_path(state).unlink()
            with self.assertRaises(ValueError):
                S.load_or_create(state, seed)
            self.assertEqual(S.read_state(state)["fit_sha256"], fitted_hash)

    def test_corrupt_training_store_never_erases_learned_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "bundled.json"
            seed.write_text(json.dumps(seed_state()), encoding="utf-8")
            state = root / "model" / "tsukushi-state.json"
            S.load_or_create(state, seed)
            original = state.read_bytes()
            S.training_path(state).write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                S.load_or_create(state, seed)
            self.assertEqual(state.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
