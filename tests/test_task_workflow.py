from __future__ import annotations

import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from test_task_creation import seed_findings


def create_seeded_tasks(db_path: Path) -> list[str]:
    seed_findings(db_path)
    result = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db_path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "select id from tasks where task_type = 'subtask' order by subtask_order"
            ).fetchall()
        ]


def test_task_workflow_assigns_and_selects_next_task(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    subtask_ids = create_seeded_tasks(db_path)

    assigned = run_cli("task-assign", "--db", str(db_path), "--task-id", subtask_ids[1])
    selected = run_cli(
        "next-task",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )

    assert assigned.returncode == 0, assigned.stderr
    assert parse_json(assigned)["task"]["status"] == "assigned_to_agent"
    selected_payload = parse_json(selected)["selected_task_for_agent_takeover"]
    assert selected_payload["id"] == subtask_ids[1]
    assert selected_payload["status"] == "assigned_to_agent"


def test_task_workflow_failed_validation_updates_parent_and_attempt_count(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    subtask_ids = create_seeded_tasks(db_path)

    failed = run_cli(
        "task-status",
        "--db",
        str(db_path),
        "--task-id",
        subtask_ids[0],
        "--status",
        "failed_validation",
        "--increment-attempt",
    )

    assert failed.returncode == 0, failed.stderr
    payload = parse_json(failed)
    assert payload["task"]["status"] == "failed_validation"
    assert payload["task"]["attempt_count"] == 1

    with sqlite3.connect(db_path) as conn:
        parent = conn.execute(
            "select status, progress_json from tasks where task_type = 'parent'"
        ).fetchone()

    assert parent[0] == "failed_validation"
    assert '"failed_subtasks": 1' in parent[1]


def test_task_workflow_rejects_invalid_status(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    subtask_ids = create_seeded_tasks(db_path)

    result = run_cli(
        "task-status",
        "--db",
        str(db_path),
        "--task-id",
        subtask_ids[0],
        "--status",
        "done-ish",
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "INVALID_TASK_STATUS"
