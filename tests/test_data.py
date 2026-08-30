from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from k3ops.data import LIFECYCLE_STATUSES, build_dashboard, load_roadmap, load_tasks
from tests.helpers import roadmap_toml, task_markdown, write_task


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.roadmap = self.root / "roadmap.toml"
        self.roadmap.write_text(roadmap_toml(), encoding="utf-8")
        self.product_roadmap = self.root / "product.toml"
        self.product_roadmap.write_text(roadmap_toml(), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_single_task_and_planned_item_are_aggregated(self):
        write_task(self.tasks, "T-00001", "DONE", modern=False)
        result = build_dashboard(self.roadmap, self.tasks)
        self.assertEqual("ok", result["data_status"])
        self.assertEqual(2, result["summary"]["roadmap_items"])
        self.assertEqual(1, result["summary"]["completed_items"])
        self.assertEqual(50.0, result["summary"]["completion_percent"])
        self.assertEqual(1, result["summary"]["planned_items"])
        self.assertEqual("DONE", result["roadmap"][0]["items"][0]["tasks"][0]["status"])
        self.assertTrue(result["roadmap"][0]["items"][1]["planned"])

    def test_non_allowlisted_root_metadata_is_not_folded_into_source(self):
        path = write_task(self.tasks, "T-00001", "READY")
        text = path.read_text(encoding="utf-8").replace(
            "- Claimed By:", "- Recommended Codex Model: model-name\n- Claimed By:"
        )
        path.write_text(text, encoding="utf-8")
        task = load_tasks(self.tasks).value["T-00001"]
        self.assertEqual(["Feedback F-00001"], task["source"])
        self.assertNotIn("Recommended", repr(task))

    def test_all_lifecycle_statuses_and_group_counts(self):
        ids = [f"T-{index:05d}" for index in range(1, 9)]
        statuses = sorted(LIFECYCLE_STATUSES)
        for task_id, status in zip(ids, statuses):
            write_task(self.tasks, task_id, status)
        items = [
            {
                "id": "all-statuses",
                "horizon": "ongoing",
                "order": 1,
                "name": "All statuses",
                "purpose": "Verify lifecycle aggregation",
                "tasks": ids,
            }
        ]
        self.roadmap.write_text(roadmap_toml(items), encoding="utf-8")
        result = build_dashboard(self.roadmap, self.tasks)
        self.assertEqual(statuses, sorted(task["status"] for task in result["tasks"]))
        self.assertEqual(2, result["summary"]["running_tasks"])
        self.assertEqual(2, result["summary"]["review_tasks"])
        self.assertEqual(1, result["summary"]["blocked_tasks"])
        self.assertEqual(1, result["roadmap"][0]["items"][0]["done_count"])
        self.assertFalse(result["roadmap"][0]["items"][0]["complete"])

    def test_multiple_done_tasks_complete_only_when_all_done(self):
        write_task(self.tasks, "T-00001", "DONE")
        write_task(self.tasks, "T-00002", "DONE")
        item = {
            "id": "multi",
            "horizon": "now",
            "order": 1,
            "name": "Multiple",
            "purpose": "Multiple task completion",
            "tasks": ["T-00001", "T-00002"],
        }
        self.roadmap.write_text(roadmap_toml([item]), encoding="utf-8")
        result = build_dashboard(self.roadmap, self.tasks)
        normalized = result["roadmap"][0]["items"][0]
        self.assertTrue(normalized["complete"])
        self.assertEqual((2, 2), (normalized["done_count"], normalized["task_count"]))
        self.assertEqual(["DONE", "DONE"], [task["status"] for task in normalized["tasks"]])

    def test_missing_task_is_incomplete_with_normalized_warning(self):
        result = build_dashboard(self.roadmap, self.tasks)
        self.assertIn("TASK_MISSING:T-00001", result["warnings"])
        self.assertEqual("MISSING", result["roadmap"][0]["items"][0]["tasks"][0]["status"])
        self.assertEqual(0, result["summary"]["completed_items"])

    def test_invalid_status_metadata_and_id_mismatch_are_rejected(self):
        write_task(self.tasks, "T-00001", "PLANNED")
        (self.tasks / "T-00002-bad.md").write_text(
            "# Task: T-00002 Bad\n\n- Status: `READY`\n\n## Intent\nbody\n", encoding="utf-8"
        )
        (self.tasks / "T-00003-bad.md").write_text(
            task_markdown("T-00004", "READY"), encoding="utf-8"
        )
        result = load_tasks(self.tasks)
        self.assertEqual({}, result.value)
        self.assertIn("TASK_STATUS:T-00001", result.warnings)
        self.assertIn("TASK_METADATA:T-00002", result.warnings)
        self.assertIn("TASK_ID_MISMATCH:T-00003", result.warnings)

    def test_invalid_toml_and_schema_return_safe_empty_roadmap(self):
        self.roadmap.write_text("not = [valid", encoding="utf-8")
        invalid_toml = load_roadmap(self.roadmap)
        self.assertEqual([], invalid_toml.value["workstreams"])
        self.assertEqual(("ROADMAP_TOML",), invalid_toml.warnings)
        self.roadmap.write_text('schema_version = "1.0"\nworkstreams = []\n', encoding="utf-8")
        invalid_schema = load_roadmap(self.roadmap)
        self.assertEqual(("ROADMAP_SCHEMA",), invalid_schema.warnings)

    def test_roadmap_schema_limits_and_horizon_are_enforced(self):
        invalid_horizon = roadmap_toml(
            [{"id": "bad", "horizon": "someday", "order": 1, "name": "Bad", "purpose": "Bad", "tasks": []}]
        )
        self.roadmap.write_text(invalid_horizon, encoding="utf-8")
        self.assertEqual(("ROADMAP_HORIZON",), load_roadmap(self.roadmap).warnings)
        with patch("k3ops.data.MAX_WORKSTREAMS", 0):
            self.assertEqual(("ROADMAP_WORKSTREAM_LIMIT",), load_roadmap(self.roadmap).warnings)

    def test_all_roadmap_size_and_schema_limits_fail_closed(self):
        valid = roadmap_toml()
        self.roadmap.write_text(valid, encoding="utf-8")
        with patch("k3ops.data.MAX_FILE_BYTES", 20):
            self.assertEqual(("ROADMAP_TOO_LARGE",), load_roadmap(self.roadmap).warnings)
        with patch("k3ops.data.MAX_ITEMS", 1):
            self.assertEqual(("ROADMAP_ITEM_LIMIT",), load_roadmap(self.roadmap).warnings)
        with patch("k3ops.data.MAX_ITEM_TASK_IDS", 0):
            self.assertEqual(("ROADMAP_TASK_ID_LIMIT",), load_roadmap(self.roadmap).warnings)
        with patch("k3ops.data.MAX_ITEM_REFERENCES", 0):
            self.assertEqual(("ROADMAP_REFERENCE_LIMIT",), load_roadmap(self.roadmap).warnings)
        with patch("k3ops.data.MAX_STRING_LENGTH", 3):
            self.assertEqual(("STRING_LIMIT",), load_roadmap(self.roadmap).warnings)

    def test_symlink_directory_special_file_and_traversal_are_not_read(self):
        outside = self.root / "outside.md"
        outside.write_text(task_markdown("T-00001", "DONE", body="outside-secret"), encoding="utf-8")
        symlink = self.tasks / "T-00001-link.md"
        try:
            symlink.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        (self.tasks / "T-00002-directory.md").mkdir()
        nested = self.tasks / "nested"
        nested.mkdir()
        write_task(nested, "T-00004", "DONE", body="nested-private-body")
        fifo_path = self.tasks / "T-00003-fifo.md"
        if hasattr(os, "mkfifo"):
            os.mkfifo(fifo_path)
        result = load_tasks(self.tasks)
        self.assertEqual({}, result.value)
        self.assertIn("TASK_NOT_REGULAR:T-00001", result.warnings)
        self.assertIn("TASK_NOT_REGULAR:T-00002", result.warnings)
        if hasattr(os, "mkfifo"):
            self.assertIn("TASK_NOT_REGULAR:T-00003", result.warnings)
        self.assertNotIn("T-00004", result.value)

    def test_invalid_utf8_is_rejected_without_raw_content(self):
        (self.tasks / "T-00001-invalid.md").write_bytes(b"\xff\xfe\x00")
        result = load_tasks(self.tasks)
        self.assertEqual({}, result.value)
        self.assertEqual(("TASK_ENCODING:T-00001",), result.warnings)

    def test_file_size_and_task_count_limits_fail_closed(self):
        oversized = self.tasks / "T-00001-large.md"
        oversized.write_text(task_markdown("T-00001") + ("x" * 100), encoding="utf-8")
        with patch("k3ops.data.MAX_FILE_BYTES", 50):
            self.assertEqual(("TASK_TOO_LARGE:T-00001",), load_tasks(self.tasks).warnings)
        oversized.unlink()
        write_task(self.tasks, "T-00001")
        write_task(self.tasks, "T-00002")
        with patch("k3ops.data.MAX_TASKS", 1):
            result = load_tasks(self.tasks)
        self.assertEqual({}, result.value)
        self.assertEqual(("TASK_COUNT_LIMIT",), result.warnings)

    def test_host_paths_and_sensitive_assignments_are_not_exposed(self):
        drive_path = "".join((chr(67), chr(58), chr(92), "private", chr(92), "path"))
        write_task(self.tasks, "T-00001", source=f"Feedback F-00001 at {drive_path}")
        result = load_tasks(self.tasks)
        self.assertEqual({}, result.value)
        self.assertEqual(("UNSAFE_METADATA:T-00001",), result.warnings)
        sensitive_assignment = "".join(("to", "ken", "=", "synthetic-value"))
        write_task(self.tasks, "T-00002", source=f"Feedback F-00001 {sensitive_assignment}")
        result = load_tasks(self.tasks)
        self.assertNotIn("T-00002", result.value)
        self.assertIn("UNSAFE_METADATA:T-00002", result.warnings)

    def test_product_and_operations_roadmaps_are_aggregated_independently(self):
        write_task(self.tasks, "T-00001", "DONE")
        product_items = [
            {
                "id": "product-complete",
                "horizon": "now",
                "order": 10,
                "name": "Product complete",
                "purpose": "Product-only delivery",
                "tasks": ["T-00001"],
            },
            {
                "id": "product-planned",
                "horizon": "next",
                "order": 20,
                "name": "Product planned",
                "purpose": "Product-only future",
                "tasks": [],
            },
        ]
        self.product_roadmap.write_text(roadmap_toml(product_items), encoding="utf-8")
        result = build_dashboard(self.roadmap, self.tasks, self.product_roadmap)
        self.assertEqual(2, result["summary"]["roadmap_items"])
        self.assertEqual(2, result["product_summary"]["roadmap_items"])
        self.assertEqual(1, result["product_summary"]["completed_items"])
        self.assertEqual(1, result["product_summary"]["planned_items"])
        self.assertEqual("ok", result["product_data_status"])
        self.assertNotIn("references", repr(result["product_roadmap"]))

    def test_invalid_product_does_not_hide_operations_or_tasks_and_reverse(self):
        write_task(self.tasks, "T-00001", "DONE")
        self.product_roadmap.write_text("broken = [toml", encoding="utf-8")
        product_invalid = build_dashboard(self.roadmap, self.tasks, self.product_roadmap)
        self.assertEqual(2, product_invalid["summary"]["roadmap_items"])
        self.assertEqual("T-00001", product_invalid["tasks"][0]["id"])
        self.assertEqual([], product_invalid["product_roadmap"])
        self.assertEqual(["PRODUCT_ROADMAP_TOML"], product_invalid["product_warnings"])

        self.product_roadmap.write_text(roadmap_toml(), encoding="utf-8")
        self.roadmap.write_text("broken = [toml", encoding="utf-8")
        operations_invalid = build_dashboard(self.roadmap, self.tasks, self.product_roadmap)
        self.assertEqual([], operations_invalid["roadmap"])
        self.assertEqual(2, operations_invalid["product_summary"]["roadmap_items"])
        self.assertEqual("ok", operations_invalid["product_data_status"])

    def test_product_source_failures_are_normalized_and_source_identified(self):
        missing = load_roadmap(self.root / "missing-product.toml", kind="PRODUCT_ROADMAP")
        self.assertEqual(("PRODUCT_ROADMAP_UNAVAILABLE",), missing.warnings)

        directory = self.root / "product-directory"
        directory.mkdir()
        self.assertEqual(
            ("PRODUCT_ROADMAP_NOT_REGULAR",),
            load_roadmap(directory, kind="PRODUCT_ROADMAP").warnings,
        )

        with patch.object(
            Path,
            "lstat",
            return_value=SimpleNamespace(st_mode=stat.S_IFLNK, st_size=0),
        ):
            symlink = load_roadmap(self.product_roadmap, kind="PRODUCT_ROADMAP")
        self.assertEqual(("PRODUCT_ROADMAP_NOT_REGULAR",), symlink.warnings)

        with patch.object(
            Path,
            "lstat",
            return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0),
        ):
            special = load_roadmap(self.product_roadmap, kind="PRODUCT_ROADMAP")
        self.assertEqual(("PRODUCT_ROADMAP_NOT_REGULAR",), special.warnings)

        with patch("k3ops.data.MAX_FILE_BYTES", 20):
            oversized = load_roadmap(self.product_roadmap, kind="PRODUCT_ROADMAP")
        self.assertEqual(("PRODUCT_ROADMAP_TOO_LARGE",), oversized.warnings)


if __name__ == "__main__":
    unittest.main()
