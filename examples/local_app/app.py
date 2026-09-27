"""Tsukushi local browser app. All inference runs on the user's computer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = (Path(sys._MEIPASS) / "examples" / "local_app"
        if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
MODEL_DIR = HERE.parent / "member_ensemble"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))
from build_member_viewer import build as build_viewer  # noqa: E402
from data_source import active_event_id, live_panel  # noqa: E402
from engine import predict, read_state  # noqa: E402

REPORT_RE = re.compile(r"^/reports/(\d+-\d+)\.(json|html|png)$")
RELEASE_API = "https://api.github.com/repos/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases?per_page=20"
RELEASE_ROOT = "https://github.com/Stakataka-3030/Bandori-Pt-Predict-Benchmark/releases/"


def current_version():
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).resolve().parent / "VERSION"
    else:
        path = HERE.parents[1] / "VERSION"
    return path.read_text(encoding="utf-8").strip()


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else None


def check_update(version, opener=urlopen):
    request = Request(RELEASE_API, headers={"User-Agent": "Tsukushi-local-updater",
                                            "Accept": "application/vnd.github+json"})
    with opener(request, timeout=8) as response:
        releases = json.load(response)
    if not isinstance(releases, list):
        raise ValueError("更新信息格式错误")
    installed = version_tuple(version)
    if installed is None:
        raise ValueError("本地版本号格式错误")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        remote = version_tuple(tag) if isinstance(tag, str) else None
        assets = release.get("assets", [])
        if remote is None or not isinstance(assets, list) or not any(
                isinstance(asset, dict) and re.fullmatch(r"Tsukushi-Windows-.*\.zip", asset.get("name", ""))
                for asset in assets):
            continue
        url = release.get("html_url", "")
        if not isinstance(url, str) or not url.startswith(RELEASE_ROOT):
            continue
        candidates.append((remote, tag, url))
    if not candidates:
        return {"status": "no_release", "current_version": version}
    remote, tag, url = max(candidates)
    if remote > installed:
        return {"status": "update_available", "current_version": version,
                "latest_version": tag, "release_url": url}
    return {"status": "up_to_date", "current_version": version,
            "latest_version": tag}


def home_directory():
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "Tsukushi" if local else Path.home() / ".tsukushi"


def default_state_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "tsukushi-state.json"
    return HERE / "tsukushi-state.json"


def editable_help_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "说明.txt"
    return HERE / "说明.txt"


class LocalApp:
    def __init__(self, state_path: Path, output_dir: Path):
        self.state_path = state_path
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = read_state(state_path)
        self.html = (HERE / "index.html").read_bytes()
        self.lock = threading.Lock()

    def generate(self, source, event_id):
        if source not in ("bestdori", "hhwx"):
            raise ValueError("请选择 Bestdori 或 HHWX")
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有一报正在生成，请稍等")
        try:
            issued = int(time.time() * 1000)
            chosen = int(event_id) if event_id else active_event_id(issued)
            panel, urls = live_panel(source, chosen, issued)
            start = panel["tasks"][0]["start_at"]
            if issued - start < 3 * 3600000:
                raise ValueError("活动开场未满 3 小时，暂不起报")
            snapshot = predict(panel, self.state)
            report_id = f"{chosen}-{issued}"
            files = {ext: self.output_dir / f"{report_id}.{ext}"
                     for ext in ("json", "html", "png")}
            payload = {"event_id": chosen, "model_id": "tsukushi-aoi",
                       "control_model_id": "tsukushi-kaori", "source": source,
                       "source_urls": urls,
                       "model_state_sha256": self.state["fit_sha256"],
                       "generated_at": issued, "snapshots": [snapshot]}
            temp = {ext: path.with_suffix(path.suffix + ".tmp")
                    for ext, path in files.items()}
            temp["json"].write_text(json.dumps(payload, ensure_ascii=False,
                                               separators=(",", ":")) + "\n",
                                    encoding="utf-8")
            build_viewer(temp["json"], temp["html"])
            from plot import render  # Matplotlib is only needed by the packaged app.
            render(snapshot, temp["png"], "Bestdori" if source == "bestdori" else "HHWX")
            for ext in ("json", "html", "png"):
                os.replace(temp[ext], files[ext])
            return {"report_id": report_id, "event_id": chosen,
                    "source": source, "issued_at": issued,
                    "mode": snapshot["forecast_mode"],
                    "remaining_hours": (snapshot["end_at"] - issued) / 3600000,
                    "control": snapshot["control"],
                    "p10": snapshot["member_p10"],
                    "p90": snapshot["member_p90"],
                    "latest_tracker_at": {str(t["tier"]): t["history"][-1]["time"]
                                          for t in panel["tasks"]},
                    "viewer_url": f"/reports/{report_id}.html",
                    "image_url": f"/reports/{report_id}.png"}
        finally:
            self.lock.release()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    @property
    def app(self):
        return self.server.app

    def do_GET(self):
        if self.path == "/":
            return self._send(200, self.app.html, "text/html; charset=utf-8")
        if self.path == "/api/health":
            response = {"status": "ok", "model_ids": self.app.state["model_ids"],
                        "training_cutoff_at": self.app.state["training_cutoff_at"],
                        "state_sha256": self.app.state["fit_sha256"],
                        "app_version": current_version()}
            return self._send(200, json.dumps(response).encode(), "application/json")
        if self.path == "/api/update":
            try:
                response = check_update(current_version())
                return self._send(200, json.dumps(response).encode(), "application/json")
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                return self._send(503, '{"error":"无法获取更新信息，请稍后重试"}'.encode("utf-8"),
                                  "application/json; charset=utf-8")
        if self.path == "/api/help":
            path = editable_help_path()
            body = path.read_bytes() if path.is_file() else "说明尚未添加。".encode("utf-8")
            return self._send(200, body, "text/plain; charset=utf-8")
        match = REPORT_RE.fullmatch(self.path)
        if match:
            report_id, ext = match.groups()
            path = self.app.output_dir / f"{report_id}.{ext}"
            if path.is_file():
                mime = {"json": "application/json; charset=utf-8",
                        "html": "text/html; charset=utf-8",
                        "png": "image/png"}[ext]
                return self._send(200, path.read_bytes(), mime)
        return self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if origin and origin != expected:
            return self._send(403, b"Forbidden", "text/plain")
        if self.path == "/api/quit":
            self._send(200, b'{"status":"stopping"}', "application/json")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path != "/api/generate":
            return self._send(404, b"Not found", "text/plain")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._send(400, '{"error":"请求格式错误"}'.encode("utf-8"),
                              "application/json; charset=utf-8")
        try:
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("请求格式错误")
            response = self.app.generate(request.get("source", "bestdori"),
                                         request.get("event_id"))
            return self._send(200, json.dumps(response, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")
        except (ValueError, KeyError, TimeoutError, OSError) as exc:
            return self._send(422, json.dumps({"error": str(exc)}, ensure_ascii=False)
                              .encode("utf-8"), "application/json; charset=utf-8")
        except Exception:
            traceback.print_exc()
            return self._send(500, '{"error":"生成失败，请查看本机日志"}'.encode("utf-8"),
                              "application/json; charset=utf-8")

    def log_message(self, format_string, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Run the Tsukushi local predictor")
    parser.add_argument("--state", type=Path, default=default_state_path())
    parser.add_argument("--output", type=Path, default=home_directory() / "reports")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    application = LocalApp(args.state, args.output)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = application
    url = f"http://127.0.0.1:{server.server_port}/"
    print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
