"""The update button only offers a release for a newer packaged Tsukushi app."""

import io
import json
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "examples" / "local_app"
sys.path.insert(0, str(APP_DIR))
from app import check_update, current_version, report_request  # noqa: E402


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def opener_for(releases):
    def open_mock(_request, timeout):
        assert timeout <= 10
        return Response(json.dumps(releases).encode("utf-8"))
    return open_mock


class UpdateTests(unittest.TestCase):
    def test_generated_report_accepts_cache_query(self):
        match = report_request("/reports/324-1790500947044.html?v=1790500947044")
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("324-1790500947044", "html"))
        self.assertIsNone(report_request("/reports/../private/benchmark.json"))

    def test_version_is_repo_version(self):
        self.assertEqual(current_version(),
                         (APP_DIR.parents[1] / "VERSION").read_text().strip())

    def test_newer_app_release_is_offered(self):
        releases = [
            {"tag_name": "v0.3.12", "draft": False, "prerelease": False,
             "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v0.3.12",
             "assets": [{"name": "Tsukushi-Windows-v0.3.12.zip"}]},
            {"tag_name": "v9.0.0", "draft": False, "prerelease": False,
             "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v9.0.0",
             "assets": [{"name": "benchmark.json"}]},
        ]
        result = check_update("0.3.10", opener_for(releases))
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latest_version"], "v0.3.12")
        self.assertTrue(result["release_url"].endswith("/tag/v0.3.12"))

    def test_draft_or_non_app_release_is_not_offered(self):
        releases = [{"tag_name": "v0.3.12", "draft": True, "prerelease": False,
                     "assets": [{"name": "Tsukushi-Windows-v0.3.12.zip"}],
                     "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v0.3.12"},
                    {"tag_name": "v0.3.11", "draft": False, "prerelease": False,
                     "assets": [{"name": "source.zip"}],
                     "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v0.3.11"}]
        self.assertEqual(check_update("0.3.10", opener_for(releases))["status"],
                         "no_release")

    def test_current_release_does_not_show_link(self):
        release = {"tag_name": "v0.3.10", "draft": False, "prerelease": False,
                   "assets": [{"name": "Tsukushi-Windows-v0.3.10.zip"}],
                   "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v0.3.10"}
        result = check_update("0.3.10", opener_for([release]))
        self.assertEqual(result["status"], "up_to_date")
        self.assertNotIn("release_url", result)

    def test_single_file_release_is_offered(self):
        release = {"tag_name": "v0.3.11", "draft": False, "prerelease": False,
                   "assets": [{"name": "Tsukushi-Windows-SingleFile-v0.3.11.exe"}],
                   "html_url": "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/tag/v0.3.11"}
        result = check_update("0.3.10", opener_for([release]))
        self.assertEqual(result["status"], "update_available")


if __name__ == "__main__":
    unittest.main()
