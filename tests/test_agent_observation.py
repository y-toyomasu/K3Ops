from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


HANDLER_PATH = Path(__file__).parents[1] / "ops" / "codex-agent-observation" / "handler.py"
SPEC = importlib.util.spec_from_file_location("agent_observation_handler", HANDLER_PATH)
assert SPEC and SPEC.loader
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


class AgentObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name) / "runtime" / "agent-observations"
        self.runtime_patch = patch.object(handler, "RUNTIME_DIRECTORY", self.runtime)
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()

    def _records(self):
        return sorted(self.runtime.glob("OBS-*.json"))

    def test_only_model_is_recorded_and_context_is_minimal(self):
        record = handler.observe(
            {
                "model": "gpt-5.6-terra",
                "session_id": "must-not-persist",
                "agent_id": "must-not-persist",
                "prompt": "must-not-persist",
                "reasoning_effort": "must-not-persist",
            }
        )
        self.assertIsNotNone(record)
        persisted = json.loads(self._records()[0].read_text(encoding="utf-8"))
        self.assertEqual("gpt-5.6-terra", persisted["observed_model"])
        self.assertEqual("Codex SubagentStart hook", persisted["observation_source"])
        self.assertEqual("not independently verifiable", persisted["observed_reasoning"])
        self.assertTrue(persisted["observed_at"].endswith("Z"))
        self.assertRegex(persisted["observation_reference"], r"^OBS-[0-9a-f]{32}$")
        serialized = json.dumps(persisted)
        for forbidden in ("session_id", "agent_id", "prompt", "reasoning_effort", "must-not-persist"):
            self.assertNotIn(forbidden, serialized)
        with patch.object(handler, "observe", return_value=record), patch("sys.stdin", io.StringIO('{"model":"gpt-5.6-terra"}')):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, handler.main())
        context = json.loads(output.getvalue())["additional_context"]
        self.assertEqual(
            f"Observation Reference: {record['observation_reference']}\nObserved Model: gpt-5.6-terra",
            context,
        )

    def test_invalid_input_and_runtime_failure_are_non_blocking(self):
        self.assertIsNone(handler.observe({"model": "unsafe model value"}))
        self.assertEqual([], self._records())
        with patch.object(handler, "RUNTIME_DIRECTORY", Path(self.temp.name) / "not-a-directory"):
            (Path(self.temp.name) / "not-a-directory").write_text("blocked", encoding="utf-8")
            self.assertIsNone(handler.observe({"model": "gpt-5.6-terra"}))
        with patch("sys.stdin", io.StringIO("not json")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, handler.main())
        self.assertEqual("", output.getvalue())

    def test_retention_is_atomic_and_limited_by_age_or_count(self):
        self.runtime.mkdir(parents=True)
        old = self.runtime / "OBS-old.json"
        old.write_text("{}", encoding="utf-8")
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(old, (old_time, old_time))
        for index in range(100):
            path = self.runtime / f"OBS-{index:032x}.json"
            path.write_text("{}", encoding="utf-8")
            fixture_time = time.time() - 1_000 + index
            os.utime(path, (fixture_time, fixture_time))
        record = handler.observe({"model": "gpt-5.6-terra"})
        self.assertIsNotNone(record)
        records = self._records()
        self.assertEqual(100, len(records))
        self.assertNotIn(old, records)
        self.assertIn(self.runtime / f"{record['observation_reference']}.json", records)
        self.assertFalse(any(path.name.startswith(".pending-") for path in self.runtime.iterdir()))
        self.assertFalse((self.runtime / ".observation.lock").exists())

    def test_task_sentinel_and_standard_synthetic_inputs_share_the_contract(self):
        standard = handler.observe({"model": "gpt-5.6-terra", "parent": "standard"})
        sentinel = handler.observe({"model": "gpt-5.6-terra", "parent": "task-sentinel"})
        self.assertIsNotNone(standard)
        self.assertIsNotNone(sentinel)
        standard_record = json.loads((self.runtime / f"{standard['observation_reference']}.json").read_text())
        sentinel_record = json.loads((self.runtime / f"{sentinel['observation_reference']}.json").read_text())
        self.assertEqual(
            {key: standard_record[key] for key in standard_record if key != "observation_reference" and key != "observed_at"},
            {key: sentinel_record[key] for key in sentinel_record if key != "observation_reference" and key != "observed_at"},
        )


if __name__ == "__main__":
    unittest.main()
