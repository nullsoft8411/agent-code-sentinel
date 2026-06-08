from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import connect, initialize_database
from code_sentinel_agent.scan_findings import ScanFindingInput, ScanJobInput, create_scan_job, upsert_scan_finding


def seed_findings(db_path: Path) -> None:
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-agent", "proj-agent", "in_progress", "task_creation"),
        )
        conn.commit()

    create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="agent_analysis",
            scan_type="agent_context",
        ),
    )
    for finding in [
        ScanFindingInput(
            id="scan-finding-single",
            project_id="proj-agent",
            run_id="run-agent",
            scan_job_id="scan-job-1",
            scanner_name="agent_analysis",
            rule_id="validation_error",
            signature="finding:single-validation",
            severity="high",
            title="Validation command failed",
            message="pytest failed",
            file_path="src/other.py",
            line_number=3,
        ),
        ScanFindingInput(
            id="scan-finding-a",
            project_id="proj-agent",
            run_id="run-agent",
            scan_job_id="scan-job-1",
            scanner_name="agent_analysis",
            rule_id="secret_assignment",
            signature="finding:service-secret",
            severity="critical",
            title="Potential hardcoded secret",
            message="Secret-like assignment detected",
            file_path="src/service.py",
            line_number=1,
        ),
        ScanFindingInput(
            id="scan-finding-b",
            project_id="proj-agent",
            run_id="run-agent",
            scan_job_id="scan-job-1",
            scanner_name="agent_analysis",
            rule_id="type_error",
            signature="finding:service-type",
            severity="medium",
            title="Type check failed",
            message="mypy failed",
            file_path="src/service.py",
            line_number=8,
        ),
    ]:
        upsert_scan_finding(str(db_path), finding)


def test_create_tasks_cli_e2e_groups_findings_and_skips_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_findings(db_path)

    first = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )
    second = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )

    assert first.returncode == 0, first.stderr
    first_payload = parse_json(first)
    assert first_payload["status"] == "passed"
    assert first_payload["counts"] == {
        "created_tasks": 4,
        "skipped_duplicates": 0,
        "grouped_files": 2,
    }

    assert second.returncode == 0, second.stderr
    second_payload = parse_json(second)
    assert second_payload["counts"] == {
        "created_tasks": 0,
        "skipped_duplicates": 0,
        "grouped_files": 0,
    }

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, parent_task_id, task_type, task_signature, status,
                   affected_file, subtask_order, progress_json
            from tasks
            order by affected_file, task_type, subtask_order
            """
        ).fetchall()

    task_types = [row["task_type"] for row in rows]
    assert task_types.count("standalone") == 1
    assert task_types.count("parent") == 1
    assert task_types.count("subtask") == 2
    assert len({row["task_signature"] for row in rows}) == 4

    parent = next(row for row in rows if row["task_type"] == "parent")
    subtasks = [row for row in rows if row["task_type"] == "subtask"]
    assert parent["affected_file"] == "src/service.py"
    assert parent["status"] == "pending"
    assert [row["subtask_order"] for row in subtasks] == [1, 2]
    assert all(row["parent_task_id"] == parent["id"] for row in subtasks)


def test_task_status_updates_parent_progress(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_findings(db_path)
    create = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )
    assert create.returncode == 0, create.stderr

    with sqlite3.connect(db_path) as conn:
        subtask_ids = [
            row[0]
            for row in conn.execute(
                "select id from tasks where task_type = 'subtask' order by subtask_order"
            ).fetchall()
        ]

    first = run_cli("task-status", "--db", str(db_path), "--task-id", subtask_ids[0], "--status", "completed")
    second = run_cli("task-status", "--db", str(db_path), "--task-id", subtask_ids[1], "--status", "completed")

    assert first.returncode == 0, first.stderr
    first_payload = parse_json(first)
    assert first_payload["task"]["status"] == "completed"
    assert second.returncode == 0, second.stderr

    with sqlite3.connect(db_path) as conn:
        parent = conn.execute(
            "select status, progress_json from tasks where task_type = 'parent'"
        ).fetchone()

    assert parent[0] == "completed"
    assert parent[1] == (
        '{"blocked_subtasks": 0, "completed_subtasks": 2, '
        '"failed_subtasks": 0, "total_subtasks": 2}'
    )


@pytest.mark.parametrize(
    ("subtask_status", "expected_parent_status", "expected_progress"),
    [
        (
            "blocked_approval_required",
            "blocked",
            {
                "blocked_subtasks": 1,
                "completed_subtasks": 1,
                "failed_subtasks": 0,
                "total_subtasks": 2,
            },
        ),
        (
            "failed_validation",
            "failed_validation",
            {
                "blocked_subtasks": 0,
                "completed_subtasks": 1,
                "failed_subtasks": 1,
                "total_subtasks": 2,
            },
        ),
    ],
)
def test_task_status_updates_parent_progress_for_blocked_and_failed_subtasks(
    tmp_path: Path,
    subtask_status: str,
    expected_parent_status: str,
    expected_progress: dict[str, int],
) -> None:
    db_path = tmp_path / "runtime.db"
    seed_findings(db_path)
    create = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
    )
    assert create.returncode == 0, create.stderr

    with sqlite3.connect(db_path) as conn:
        subtask_ids = [
            row[0]
            for row in conn.execute(
                "select id from tasks where task_type = 'subtask' order by subtask_order"
            ).fetchall()
        ]

    completed = run_cli("task-status", "--db", str(db_path), "--task-id", subtask_ids[0], "--status", "completed")
    changed = run_cli("task-status", "--db", str(db_path), "--task-id", subtask_ids[1], "--status", subtask_status)

    assert completed.returncode == 0, completed.stderr
    assert changed.returncode == 0, changed.stderr
    assert parse_json(changed)["task"]["status"] == subtask_status

    with sqlite3.connect(db_path) as conn:
        parent = conn.execute(
            "select status, progress_json from tasks where task_type = 'parent'"
        ).fetchone()

    assert parent[0] == expected_parent_status
    assert json.loads(parent[1]) == expected_progress


def test_create_tasks_blocks_when_subtask_limit_exceeded(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_findings(db_path)

    result = run_cli(
        "create-tasks",
        "--db",
        str(db_path),
        "--project-id",
        "proj-agent",
        "--run-id",
        "run-agent",
        "--max-subtasks-per-parent",
        "1",
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "MAX_SUBTASKS_EXCEEDED"
    assert payload["groups"] == {"src/service.py": 2}
