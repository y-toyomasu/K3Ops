"""Fail-closed readers and deterministic aggregation for K3Ops."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MAX_FILE_BYTES = 1024 * 1024
MAX_WORKSTREAMS = 32
MAX_ITEMS = 256
MAX_TASKS = 1024
MAX_ITEM_TASK_IDS = 64
MAX_ITEM_REFERENCES = 64
MAX_STRING_LENGTH = 4096

HORIZONS = frozenset({"now", "next", "later", "ongoing"})
LIFECYCLE_STATUSES = frozenset(
    {
        "DESIGN",
        "READY",
        "CLAIMED",
        "IMPLEMENTING",
        "GUI_REVIEW",
        "ACCEPTANCE_REVIEW",
        "DONE",
        "BLOCKED",
    }
)
RUNNING_STATUSES = frozenset({"CLAIMED", "IMPLEMENTING"})
REVIEW_STATUSES = frozenset({"GUI_REVIEW", "ACCEPTANCE_REVIEW"})

TASK_ID_RE = re.compile(r"T-\d{5}")
TASK_FILE_RE = re.compile(r"^(T-\d{5})(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?\.md$")
TITLE_RE = re.compile(r"^# Task:\s+(T-\d{5})(?:\s+(.+))?$")
METADATA_RE = re.compile(
    r"^- (Status|Priority|Dependencies|Source|Claimed By|Claimed At):(?:\s*(.*))?$"
)
HOST_PATH_RE = re.compile(
    r"(?:^|[\s'\"(])(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home)/)", re.IGNORECASE
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:secret|credential|token|flag|password|api[_ -]?key)\s*[:=]\s*\S+"
)


class DataProblem(Exception):
    """A normalized data-boundary violation without raw source details."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReadResult:
    value: Any
    warnings: tuple[str, ...]


def _warning(code: str, identifier: str | None = None) -> str:
    return f"{code}:{identifier}" if identifier else code


def _clean_markdown(value: str) -> str:
    return value.strip().replace("`", "")


def _safe_text(value: Any, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise DataProblem("INVALID_STRING")
    text = value.strip()
    if nonempty and not text:
        raise DataProblem("INVALID_STRING")
    if len(text) > MAX_STRING_LENGTH:
        raise DataProblem("STRING_LIMIT")
    if "\x00" in text or HOST_PATH_RE.search(text) or SENSITIVE_ASSIGNMENT_RE.search(text):
        raise DataProblem("UNSAFE_METADATA")
    return text


def _read_regular_utf8(path: Path, kind: str) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DataProblem(f"{kind}_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode):
        raise DataProblem(f"{kind}_NOT_REGULAR")
    if info.st_size > MAX_FILE_BYTES:
        raise DataProblem(f"{kind}_TOO_LARGE")
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise DataProblem(f"{kind}_NOT_REGULAR")
            payload = handle.read(MAX_FILE_BYTES + 1)
    except DataProblem:
        raise
    except OSError as exc:
        raise DataProblem(f"{kind}_UNAVAILABLE") from exc
    if len(payload) > MAX_FILE_BYTES:
        raise DataProblem(f"{kind}_TOO_LARGE")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataProblem(f"{kind}_ENCODING") from exc


def _string_list(value: Any, *, maximum: int, item_code: str) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise DataProblem(item_code)
    return [_safe_text(item, nonempty=True) for item in value]


def _source_problem(code: str, kind: str) -> str:
    if kind == "ROADMAP" or code.startswith(f"{kind}_"):
        return code
    if code.startswith("ROADMAP_"):
        return f"{kind}_{code.removeprefix('ROADMAP_')}"
    return f"{kind}_{code}"


def load_roadmap(path: str | Path, *, kind: str = "ROADMAP") -> ReadResult:
    roadmap_path = Path(path)
    try:
        text = _read_regular_utf8(roadmap_path, kind)
        try:
            raw = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            raise DataProblem(f"{kind}_TOML") from exc
        roadmap = _validate_roadmap(raw)
        return ReadResult(roadmap, ())
    except DataProblem as problem:
        code = _source_problem(problem.code, kind)
        return ReadResult({"schema_version": None, "workstreams": []}, (code,))


def _validate_roadmap(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataProblem("ROADMAP_SCHEMA")
    schema_version = _safe_text(raw.get("schema_version"), nonempty=True)
    workstreams_raw = raw.get("workstreams")
    if not isinstance(workstreams_raw, list) or not workstreams_raw:
        raise DataProblem("ROADMAP_SCHEMA")
    if len(workstreams_raw) > MAX_WORKSTREAMS:
        raise DataProblem("ROADMAP_WORKSTREAM_LIMIT")

    workstream_ids: set[str] = set()
    item_ids: set[str] = set()
    item_total = 0
    workstreams: list[dict[str, Any]] = []
    for raw_workstream in workstreams_raw:
        if not isinstance(raw_workstream, dict):
            raise DataProblem("ROADMAP_SCHEMA")
        workstream_id = _safe_text(raw_workstream.get("id"), nonempty=True)
        name = _safe_text(raw_workstream.get("name"), nonempty=True)
        if workstream_id in workstream_ids:
            raise DataProblem("ROADMAP_DUPLICATE_WORKSTREAM")
        workstream_ids.add(workstream_id)
        items_raw = raw_workstream.get("items")
        if not isinstance(items_raw, list):
            raise DataProblem("ROADMAP_SCHEMA")
        item_total += len(items_raw)
        if item_total > MAX_ITEMS:
            raise DataProblem("ROADMAP_ITEM_LIMIT")
        items: list[dict[str, Any]] = []
        for raw_item in items_raw:
            items.append(_validate_item(raw_item, item_ids))
        workstreams.append({"id": workstream_id, "name": name, "items": items})
    return {"schema_version": schema_version, "workstreams": workstreams}


def _validate_item(raw: Any, item_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataProblem("ROADMAP_SCHEMA")
    item_id = _safe_text(raw.get("id"), nonempty=True)
    if item_id in item_ids:
        raise DataProblem("ROADMAP_DUPLICATE_ITEM")
    item_ids.add(item_id)
    horizon = _safe_text(raw.get("horizon"), nonempty=True)
    if horizon not in HORIZONS:
        raise DataProblem("ROADMAP_HORIZON")
    display_order = raw.get("display_order")
    if isinstance(display_order, bool) or not isinstance(display_order, int) or display_order <= 0:
        raise DataProblem("ROADMAP_DISPLAY_ORDER")
    name = _safe_text(raw.get("name"), nonempty=True)
    purpose = _safe_text(raw.get("purpose"), nonempty=True)
    task_ids = _string_list(
        raw.get("task_ids"), maximum=MAX_ITEM_TASK_IDS, item_code="ROADMAP_TASK_ID_LIMIT"
    )
    if any(TASK_ID_RE.fullmatch(task_id) is None for task_id in task_ids):
        raise DataProblem("ROADMAP_TASK_ID")
    if len(task_ids) != len(set(task_ids)):
        raise DataProblem("ROADMAP_DUPLICATE_TASK_ID")
    references = _string_list(
        raw.get("references"), maximum=MAX_ITEM_REFERENCES, item_code="ROADMAP_REFERENCE_LIMIT"
    )
    return {
        "id": item_id,
        "horizon": horizon,
        "display_order": display_order,
        "name": name,
        "purpose": purpose,
        "task_ids": task_ids,
        "references": references,
    }


def load_tasks(directory: str | Path) -> ReadResult:
    task_directory = Path(directory)
    try:
        directory_info = task_directory.lstat()
        if not stat.S_ISDIR(directory_info.st_mode) or task_directory.is_symlink():
            raise DataProblem("TASK_DIRECTORY_INVALID")
        entries = sorted(os.scandir(task_directory), key=lambda entry: entry.name)
    except (OSError, DataProblem) as exc:
        code = exc.code if isinstance(exc, DataProblem) else "TASK_DIRECTORY_UNAVAILABLE"
        return ReadResult({}, (code,))

    candidates = [entry for entry in entries if entry.name.startswith("T-")]
    if len(candidates) > MAX_TASKS:
        return ReadResult({}, ("TASK_COUNT_LIMIT",))

    tasks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for entry in candidates:
        match = TASK_FILE_RE.fullmatch(entry.name)
        warning_id = match.group(1) if match else "UNKNOWN"
        if not match:
            warnings.append(_warning("TASK_FILENAME", warning_id))
            continue
        task_id = match.group(1)
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise DataProblem("TASK_NOT_REGULAR")
            task = _parse_task(Path(entry.path), task_id)
            if task_id in tasks:
                raise DataProblem("TASK_DUPLICATE_ID")
            tasks[task_id] = task
        except (OSError, DataProblem) as exc:
            code = exc.code if isinstance(exc, DataProblem) else "TASK_UNAVAILABLE"
            warnings.append(_warning(code, task_id))
    return ReadResult(tasks, tuple(warnings))


def _parse_task(path: Path, filename_id: str) -> dict[str, Any]:
    text = _read_regular_utf8(path, "TASK")
    lines = text.splitlines()
    if not lines:
        raise DataProblem("TASK_METADATA")
    title_match = TITLE_RE.fullmatch(lines[0].strip())
    if not title_match:
        raise DataProblem("TASK_METADATA")
    if title_match.group(1) != filename_id:
        raise DataProblem("TASK_ID_MISMATCH")
    title = _safe_text(title_match.group(2) or filename_id, nonempty=True)

    metadata_start = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "## Metadata"),
        0,
    )
    metadata_lines: list[str] = []
    for line in lines[metadata_start + 1 :]:
        if line.startswith("## "):
            break
        metadata_lines.append(line.rstrip())
    metadata = _parse_metadata_lines(metadata_lines)
    status = metadata.get("Status", "")
    if status not in LIFECYCLE_STATUSES:
        raise DataProblem("TASK_STATUS")
    priority = metadata.get("Priority", "")
    if not isinstance(priority, str) or not priority:
        raise DataProblem("TASK_METADATA")
    dependencies = metadata.get("Dependencies")
    sources = metadata.get("Source")
    if not isinstance(dependencies, list) or not isinstance(sources, list) or not sources:
        raise DataProblem("TASK_METADATA")
    return {
        "id": filename_id,
        "title": title,
        "status": status,
        "priority": priority,
        "dependencies": dependencies,
        "source": sources,
        "claimed_by": metadata.get("Claimed By") or None,
        "claimed_at": metadata.get("Claimed At") or None,
    }


def _parse_metadata_lines(lines: Iterable[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    active_list: str | None = None
    for line in lines:
        match = METADATA_RE.fullmatch(line)
        if match:
            key, inline = match.groups()
            if key in metadata:
                raise DataProblem("TASK_METADATA")
            active_list = None
            if key in {"Dependencies", "Source"}:
                metadata[key] = []
                active_list = key
                if inline:
                    value = _safe_text(_clean_markdown(inline), nonempty=True)
                    metadata[key].append(value)
            else:
                metadata[key] = _safe_text(_clean_markdown(inline or ""))
            continue
        stripped = line.strip()
        if active_list and line[:1].isspace() and stripped.startswith("- "):
            value = _safe_text(_clean_markdown(stripped[2:]), nonempty=True)
            metadata[active_list].append(value)
            continue
        if line.startswith("- "):
            active_list = None
    return metadata


def _aggregate_roadmap(
    roadmap_result: ReadResult,
    tasks_by_id: dict[str, dict[str, Any]],
    *,
    initial_warnings: Iterable[str] = (),
    global_task_counts: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings = list(roadmap_result.warnings) + list(initial_warnings)
    workstreams: list[dict[str, Any]] = []
    item_count = 0
    completed_count = 0
    planned_count = 0
    related_task_ids: set[str] = set()

    for workstream in roadmap_result.value["workstreams"]:
        normalized_items: list[dict[str, Any]] = []
        for item in sorted(workstream["items"], key=lambda current: current["display_order"]):
            item_count += 1
            task_ids = item["task_ids"]
            related_task_ids.update(task_ids)
            related_tasks: list[dict[str, str]] = []
            done_count = 0
            for task_id in task_ids:
                task = tasks_by_id.get(task_id)
                if task is None:
                    warnings.append(_warning("TASK_MISSING", task_id))
                    related_tasks.append({"id": task_id, "status": "MISSING"})
                else:
                    related_tasks.append({"id": task_id, "status": task["status"]})
                    if task["status"] == "DONE":
                        done_count += 1
            planned = not task_ids
            complete = bool(task_ids) and done_count == len(task_ids)
            if planned:
                planned_count += 1
            if complete:
                completed_count += 1
            normalized_items.append(
                {
                    "id": item["id"],
                    "horizon": item["horizon"],
                    "display_order": item["display_order"],
                    "name": item["name"],
                    "purpose": item["purpose"],
                    "tasks": related_tasks,
                    "done_count": done_count,
                    "task_count": len(task_ids),
                    "planned": planned,
                    "complete": complete,
                }
            )
        workstreams.append(
            {"id": workstream["id"], "name": workstream["name"], "items": normalized_items}
        )

    status_tasks = (
        list(tasks_by_id.values())
        if global_task_counts
        else [tasks_by_id[task_id] for task_id in related_task_ids if task_id in tasks_by_id]
    )
    running_count = sum(task["status"] in RUNNING_STATUSES for task in status_tasks)
    review_count = sum(task["status"] in REVIEW_STATUSES for task in status_tasks)
    blocked_count = sum(task["status"] == "BLOCKED" for task in status_tasks)
    normalized_warnings = list(dict.fromkeys(warnings))
    completion_percent = round((completed_count / item_count) * 100, 1) if item_count else 0.0
    summary = {
        "roadmap_items": item_count,
        "completed_items": completed_count,
        "completion_percent": completion_percent,
        "planned_items": planned_count,
        "running_tasks": running_count,
        "review_tasks": review_count,
        "blocked_tasks": blocked_count,
        "warning_count": len(normalized_warnings),
    }
    return workstreams, summary, normalized_warnings


def build_dashboard(
    roadmap_path: str | Path,
    tasks_directory: str | Path,
    product_roadmap_path: str | Path | None = None,
) -> dict[str, Any]:
    task_result = load_tasks(tasks_directory)
    tasks_by_id: dict[str, dict[str, Any]] = task_result.value

    roadmap, summary, warnings = _aggregate_roadmap(
        load_roadmap(roadmap_path),
        tasks_by_id,
        initial_warnings=task_result.warnings,
        global_task_counts=True,
    )
    if product_roadmap_path is None:
        product_result = ReadResult({"schema_version": None, "workstreams": []}, ())
    else:
        product_result = load_roadmap(product_roadmap_path, kind="PRODUCT_ROADMAP")
    product_roadmap, product_summary, product_warnings = _aggregate_roadmap(
        product_result, tasks_by_id
    )

    return {
        "data_status": "warning" if warnings else "ok",
        "warnings": warnings,
        "summary": summary,
        "roadmap": roadmap,
        "tasks": sorted(tasks_by_id.values(), key=lambda task: task["id"]),
        "product_data_status": "warning" if product_warnings else "ok",
        "product_warnings": product_warnings,
        "product_summary": product_summary,
        "product_roadmap": product_roadmap,
    }
