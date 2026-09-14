from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from k3ops.app import create_app
from tests.helpers import roadmap_toml, story_index, write_task


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.roadmap = self.root / "roadmap.toml"
        self.roadmap.write_text(roadmap_toml(), encoding="utf-8")
        self.product_roadmap = self.root / "product.toml"
        self.product_roadmap.write_text(roadmap_toml(), encoding="utf-8")
        self.stories = self.root / "INDEX.md"
        self.stories.write_text(story_index(), encoding="utf-8")
        write_task(
            self.tasks,
            "T-00001",
            "DONE",
            title="Visible title",
            body="private-body-marker forbidden-value-marker host-path-marker",
        )
        self.app = create_app(
            {
                "TESTING": True,
                "ROADMAP_PATH": str(self.roadmap),
                "PRODUCT_ROADMAP_PATH": str(self.product_roadmap),
                "TASKS_DIRECTORY": str(self.tasks),
                "STORIES_INDEX_PATH": str(self.stories),
            }
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
        self.assertIn("product_summary", data)
        self.assertIn("product_roadmap", data)
        self.assertEqual("S-00001", data["stories"][0]["id"])
        self.assertEqual({"total": 1, "active": 1, "paused": 0, "done": 0}, data["story_summary"])
        self.assertEqual([], data["story_warnings"])
        self.assertNotIn("references", serialized)

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
        self.assertIn("Product Summary", html)
        self.assertIn("Development Operations Summary", html)
        self.assertIn("Product Roadmap", html)
        self.assertIn("Development Operations Roadmap", html)
        self.assertIn("Tasks", html)
        self.assertIn("Stories", html)
        self.assertIn('id="stories-section"', html)
        self.assertIn("DONE", html)
        self.assertIn("PLANNED", html)
        self.assertNotIn("private-body-marker", html)

    def test_story_warning_keeps_the_existing_dashboard_visible(self):
        self.stories.write_text("# Wrong\n", encoding="utf-8")
        payload = self.client.get("/api/dashboard").get_json()
        self.assertEqual([], payload["stories"])
        self.assertEqual({"total": 0, "active": 0, "paused": 0, "done": 0}, payload["story_summary"])
        self.assertEqual(["STORIES_HEADER"], payload["story_warnings"])
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="stories-warning"', html)
        self.assertIn("Storyはありません", html)
        self.assertIn("Tasks", html)
        self.assertIn('id="roadmap-search"', html)
        self.assertIn('id="product-roadmap-search"', html)
        self.assertIn('id="product-workstream-filter"', html)
        self.assertIn('id="product-horizon-filter"', html)
        self.assertIn('id="task-search"', html)
        self.assertIn('id="refresh-state"', html)
        self.assertIn("setInterval(refresh,60000)", html)
        self.assertIn("visibilitychange", html)
        self.assertIn("if (inFlight) return", html)
        self.assertIn("最終正常表示を維持しています（stale）", html)
        self.assertIn('id="product-refresh-state"', html)
        self.assertIn("task-empty').classList.toggle('is-hidden',rows.some", html)
        self.assertIn("nonce=", html)
        self.assertNotIn("private-body-marker", html)

    def test_product_and_operations_failures_are_separate(self):
        self.product_roadmap.write_text("broken = [toml", encoding="utf-8")
        product_invalid = self.client.get("/api/dashboard").get_json()
        self.assertEqual("ok", product_invalid["data_status"])
        self.assertEqual("warning", product_invalid["product_data_status"])
        self.assertEqual(["PRODUCT_ROADMAP_TOML"], product_invalid["product_warnings"])
        self.assertEqual(2, product_invalid["summary"]["roadmap_items"])
        self.assertEqual([], product_invalid["product_roadmap"])

        self.product_roadmap.write_text(roadmap_toml(), encoding="utf-8")
        self.roadmap.write_text("broken = [toml", encoding="utf-8")
        operations_invalid = self.client.get("/api/dashboard").get_json()
        self.assertEqual("warning", operations_invalid["data_status"])
        self.assertEqual("ok", operations_invalid["product_data_status"])
        self.assertEqual(2, operations_invalid["product_summary"]["roadmap_items"])

    def test_product_warning_is_rendered_in_its_own_region(self):
        self.product_roadmap.write_text("broken = [toml", encoding="utf-8")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="product-warning"', html)
        self.assertIn("Product data warning", html)
        self.assertNotIn('id="operations-warning"', html)

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
