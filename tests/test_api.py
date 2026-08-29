from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from k3ops.app import create_app
from tests.helpers import roadmap_toml, write_task


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.roadmap = self.root / "roadmap.toml"
        self.roadmap.write_text(roadmap_toml(), encoding="utf-8")
        write_task(
            self.tasks,
            "T-00001",
            "DONE",
            title="Visible title",
            body="private-body-marker forbidden-value-marker host-path-marker",
        )
        self.app = create_app(
            {"TESTING": True, "ROADMAP_PATH": str(self.roadmap), "TASKS_DIRECTORY": str(self.tasks)}
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_only_read_endpoints_are_registered(self):
        rules = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertEqual({"/", "/health", "/api/dashboard"}, rules)
        for path in rules:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(405, self.client.open(path, method=method).status_code)

    def test_responses_disable_caching_and_external_content(self):
        response = self.client.get("/")
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertEqual("DENY", response.headers["X-Frame-Options"])

    def test_health_is_liveness_even_with_invalid_sources(self):
        self.roadmap.unlink()
        self.assertEqual(200, self.client.get("/health").status_code)
        self.assertEqual({"status": "ok"}, self.client.get("/health").get_json())

    def test_api_returns_normalized_data_without_body_or_raw_paths(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(200, response.status_code)
        data = response.get_json()
        self.assertEqual("DONE", data["tasks"][0]["status"])
        serialized = json.dumps(data)
        self.assertNotIn("private-body-marker", serialized)
        self.assertNotIn("host-path-marker", serialized)
        self.assertNotIn("forbidden-value-marker", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_invalid_data_returns_warning_without_raw_error(self):
        self.roadmap.write_text("broken = [toml", encoding="utf-8")
        response = self.client.get("/api/dashboard")
        payload = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("warning", payload["data_status"])
        self.assertIn("ROADMAP_TOML", payload["warnings"])
        self.assertNotIn("broken", response.get_data(as_text=True))
        self.assertNotIn(str(self.roadmap), response.get_data(as_text=True))

    def test_reload_reads_latest_file_state(self):
        first = self.client.get("/api/dashboard").get_json()
        self.assertEqual("DONE", first["tasks"][0]["status"])
        write_task(self.tasks, "T-00001", "BLOCKED", title="Updated title")
        second = self.client.get("/api/dashboard").get_json()
        self.assertEqual("BLOCKED", second["tasks"][0]["status"])
        self.assertEqual("Updated title", second["tasks"][0]["title"])

    def test_dashboard_has_summary_roadmap_tasks_and_text_status(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Summary", html)
        self.assertIn("Development Operations Roadmap", html)
        self.assertIn("Tasks", html)
        self.assertIn("DONE", html)
        self.assertIn("PLANNED", html)
        self.assertNotIn("private-body-marker", html)
        self.assertIn('id="roadmap-search"', html)
        self.assertIn('id="task-search"', html)
        self.assertIn('id="refresh-state"', html)
        self.assertIn("setInterval(refresh,60000)", html)
        self.assertIn("visibilitychange", html)
        self.assertIn("if (inFlight) return", html)
        self.assertIn("最終正常表示を維持しています（stale）", html)
        self.assertIn("task-empty').classList.toggle('is-hidden',rows.some", html)
        self.assertIn("nonce=", html)
        self.assertNotIn("private-body-marker", html)

    def test_empty_and_warning_states_render(self):
        for path in self.tasks.iterdir():
            path.unlink()
        self.roadmap.write_text("broken = [toml", encoding="utf-8")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Data warning", html)
        self.assertIn("表示可能なRoadmap Itemはありません", html)
        self.assertIn("表示可能なTask Metadataはありません", html)


if __name__ == "__main__":
    unittest.main()
