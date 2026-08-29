from __future__ import annotations

from pathlib import Path


def task_markdown(
    task_id: str,
    status: str = "READY",
    *,
    title: str = "Fixture task",
    priority: str = "High",
    source: str = "Feedback `F-00001`",
    dependencies: str = "none",
    body: str = "private-body-marker",
    modern: bool = True,
) -> str:
    metadata_heading = "\n## Metadata\n" if modern else "\n"
    return (
        f"# Task: {task_id} {title}\n"
        f"{metadata_heading}\n"
        f"- Status: `{status}`\n"
        f"- Priority: `{priority}`\n"
        f"- Dependencies: {dependencies}\n"
        f"- Source: {source}\n"
        "- Claimed By: `Fixture Agent`\n"
        "- Claimed At: `2026-08-29T00:00:00+09:00`\n\n"
        "## Intent\n\n"
        f"{body}\n"
    )


def write_task(directory: Path, task_id: str, status: str = "READY", **kwargs) -> Path:
    path = directory / f"{task_id}-fixture.md"
    path.write_text(task_markdown(task_id, status, **kwargs), encoding="utf-8")
    return path


def roadmap_toml(items: list[dict] | None = None) -> str:
    selected = items if items is not None else [
        {
            "id": "safe-mvp",
            "horizon": "now",
            "order": 10,
            "name": "Safe MVP",
            "purpose": "Provide read-only operations visibility",
            "tasks": ["T-00001"],
        },
        {
            "id": "future-work",
            "horizon": "next",
            "order": 20,
            "name": "Future work",
            "purpose": "Remain planned without a fabricated task",
            "tasks": [],
        },
    ]
    lines = ['schema_version = "1.0"', "", "[[workstreams]]", 'id = "operations"', 'name = "Operations"']
    for item in selected:
        task_ids = ", ".join(f'"{task_id}"' for task_id in item["tasks"])
        lines.extend(
            [
                "",
                "[[workstreams.items]]",
                f'id = "{item["id"]}"',
                f'horizon = "{item["horizon"]}"',
                f'display_order = {item["order"]}',
                f'name = "{item["name"]}"',
                f'purpose = "{item["purpose"]}"',
                f"task_ids = [{task_ids}]",
                'references = ["F-00001"]',
            ]
        )
    return "\n".join(lines) + "\n"
