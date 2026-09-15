"""Non-blocking SubagentStart observation handler for manual Codex hook installation."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OBSERVATION_SOURCE = "Codex SubagentStart hook"
OBSERVED_REASONING = "not independently verifiable"
OBSERVED_REASONING_UNAVAILABLE_REASON = "SubagentStart does not provide reasoning effort."
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
LOCK_WAIT_SECONDS = 0.25
RUNTIME_DIRECTORY = Path(__file__).resolve().parents[2] / "runtime" / "agent-observations"


def _valid_model(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        return None
    return model


def _acquire_lock(directory: Path) -> Path | None:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    lock_path = directory / ".observation.lock"
    while time.monotonic() < deadline:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(0.01)
            continue
        try:
            os.close(descriptor)
        except OSError:
            pass
        return lock_path
    return None


def _write_record(directory: Path, record: dict[str, str]) -> bool:
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return False
    lock_path = _acquire_lock(directory)
    if lock_path is None:
        return False
    pending_path: Path | None = None
    try:
        reference = record["observation_reference"]
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".pending-", delete=False
        ) as pending:
            json.dump(record, pending, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            pending.write("\n")
            pending.flush()
            os.fsync(pending.fileno())
            pending_path = Path(pending.name)
        os.replace(pending_path, directory / f"{reference}.json")
        pending_path = None
        records = sorted(
            (path for path in directory.glob("OBS-*.json") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
        cutoff = time.time() - (30 * 24 * 60 * 60)
        expired = [path for path in records if path.stat().st_mtime < cutoff]
        current = [path for path in records if path not in expired]
        excess = current[: max(0, len(current) - 100)]
        for path in expired + excess:
            path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
    finally:
        if pending_path is not None:
            try:
                pending_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def observe(payload: Any) -> dict[str, str] | None:
    model = _valid_model(payload)
    if model is None:
        return None
    reference = f"OBS-{uuid.uuid4().hex}"
    record = {
        "observation_reference": reference,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "observed_model": model,
        "observation_source": OBSERVATION_SOURCE,
        "observed_reasoning": OBSERVED_REASONING,
        "observed_reasoning_unavailable_reason": OBSERVED_REASONING_UNAVAILABLE_REASON,
    }
    if not _write_record(RUNTIME_DIRECTORY, record):
        return None
    return record


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        record = observe(payload)
        if record is not None:
            context = (
                f"Observation Reference: {record['observation_reference']}\n"
                f"Observed Model: {record['observed_model']}"
            )
            print(json.dumps({"additional_context": context}, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
