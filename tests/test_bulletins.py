"""Text formats must share an origin, ordered published data and clamp status."""

import copy
from datetime import datetime, timezone
import json
import locale
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


APP = Path(__file__).resolve().parents[1] / "examples" / "local_app"
sys.path.insert(0, str(APP))
import app  # noqa: E402
import data_source  # noqa: E402
from bulletins import format_bulletins  # noqa: E402


def ms(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def fixture():
    issue, end = ms("2026-09-30T12:29:59+00:00"), ms("2026-09-30T14:59:59+00:00")
    panel = {"event_id": 325, "server": "cn", "server_name": "国服", "event_name": "测试活动",
             "tasks": [{"start_at": issue - 4 * 3600000, "issued_at": issue, "end_at": end}]}
    snap = {"issued_at": issue, "end_at": end,
            "control": {500: 1000.4, 1000: 800.4, 1500: 600.4, 2000: 400.4},
            "member_p10": {500: 900.4, 1000: 700.4, 1500: 500.4, 2000: 300.4},
            "member_p90": {500: 1100.4, 1000: 900.4, 1500: 700.4, 2000: 500.4},
            "rank_order_adjustment": {"t1500_clamped": False}}
    return panel, {"mashiro": copy.deepcopy(snap), "rui": copy.deepcopy(snap)}


class BulletinTests(unittest.TestCase):
    def test_numeric_exact_format_and_readable_beijing_hours(self):
        panel, snapshots = fixture()
        result = format_bulletins(panel, snapshots)
        expected = ["STSTSTST", "325CN", "2026093012", "2026093015", "3",
                    "MASKAORI", "1000", "800", "600", "400", "MASAOI",
                    "900|1100", "700|900", "500|700", "300|500",
                    "RUIKAORI", "1000", "800", "600", "400", "RUIAOI",
                    "900|1100", "700|900", "500|700", "300|500", "EDEDEDED"]
        self.assertEqual(result["numeric"], "\n".join(expected) + "\n")
        self.assertIn("325-测试活动-国服\n", result["readable"])
        self.assertIn("起报时间：2026年09月30日20时", result["readable"])
        self.assertIn("截活时间：2026年09月30日23时", result["readable"])
        self.assertIn("MASHIRO（重建初始场模型）：", result["readable"])
        self.assertIn("RUI（剪枝模型）：", result["readable"])

    def test_clamp_is_once_before_numeric_end_and_at_readable_end(self):
        panel, snapshots = fixture()
        snapshots["rui"]["rank_order_adjustment"]["t1500_clamped"] = True
        result = format_bulletins(panel, snapshots)
        self.assertTrue(result["numeric"].endswith("CLAMPED\nEDEDEDED\n"))
        self.assertEqual(result["numeric"].count("CLAMPED"), 1)
        self.assertTrue(result["readable"].endswith("模型数值已Clamp\n"))

    def test_readable_time_works_under_non_chinese_system_locale(self):
        panel, snapshots = fixture()
        previous = locale.setlocale(locale.LC_TIME)
        try:
            locale.setlocale(locale.LC_TIME, "C")
            readable = format_bulletins(panel, snapshots)["readable"]
            self.assertIn("2026年09月30日20时", readable)
        finally:
            locale.setlocale(locale.LC_TIME, previous)

    def test_time_rounding_rolls_date_and_rejects_mixed_origins(self):
        panel, snapshots = fixture()
        for snap in snapshots.values():
            snap["issued_at"] = ms("2026-09-30T23:45:00+00:00")
            snap["end_at"] = ms("2026-10-01T02:59:59+00:00")
        lines = format_bulletins(panel, snapshots)["numeric"].splitlines()
        self.assertEqual(lines[2:5], ["2026100100", "2026100103", "3"])
        snapshots["rui"]["issued_at"] += 1
        with self.assertRaisesRegex(ValueError, "起报时刻"):
            format_bulletins(panel, snapshots)

    def test_chinese_name_preferred_then_japanese(self):
        meta = {"startAt": [None, None, None, "1"], "endAt": [None, None, None, "2"],
                "eventName": ["日文名称", None, None, "中文名称"]}
        with patch.object(data_source, "fetch_json", return_value=meta):
            self.assertEqual(data_source.event_info(325)["event_name"], "中文名称")
            meta["eventName"][3] = None
            self.assertEqual(data_source.event_info(325)["event_name"], "日文名称")

    def test_backend_reads_tracker_once_and_keeps_three_snapshots(self):
        panel, snapshots = fixture()
        with tempfile.TemporaryDirectory() as directory:
            local = object.__new__(app.LocalApp)
            local.lock = threading.Lock()
            local.state = {"fit_sha256": "test-fit"}
            local.output_dir = Path(directory)
            with patch.object(app, "live_panel", return_value=(panel, ["test-source"])) as reader, \
                    patch.object(app, "predict", side_effect=[snapshots["mashiro"], snapshots["rui"], dict(snapshots["mashiro"], member_p10={}, member_p90={})]) as model, \
                    patch.object(app.time, "time", return_value=snapshots["mashiro"]["issued_at"] / 1000):
                result = local.generate_bulletins("bestdori", 325)
            reader.assert_called_once()
            self.assertEqual([call.kwargs["mode"] for call in model.call_args_list], ["mashiro", "rui", "nanami"])
            stored = json.loads((local.output_dir / (result["report_id"] + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(set(stored["snapshots"]), {"mashiro", "rui", "nanami"})
            self.assertEqual(stored["model_state_sha256"], "test-fit")
            self.assertIn("NANKAORI\n1000\n800\n600\n400\n", result["numeric"])
            self.assertEqual((local.output_dir / (result["report_id"] + ".readable.txt")).read_text(encoding="utf-8"), result["readable"])
            self.assertEqual((local.output_dir / (result["report_id"] + ".numeric.txt")).read_text(encoding="utf-8"), result["numeric"])


if __name__ == "__main__":
    unittest.main()
