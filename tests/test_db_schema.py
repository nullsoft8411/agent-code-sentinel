from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = RUNTIME_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from code_sentinel_agent.db import connect, initialize_database


def test_initialize_database_creates_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "code_sentinel_agent.db"

    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }

    assert {
        "projects",
        "runs",
        "memories",
        "findings",
        "tasks",
        "qa_gate_results",
        "validation_attempts",
        "approvals",
        "artifacts",
        "state_locks",
        "schema_migrations",
    }.issubset(tables)


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "code_sentinel_agent.db"

    initialize_database(db_path)
    initialize_database(db_path)

    with connect(db_path) as conn:
        versions = conn.execute(
            "select version from schema_migrations order by version"
        ).fetchall()

    assert versions == [("001_init",), ("002_mcp_state_locks",)]


def test_project_run_finding_task_and_gate_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "code_sentinel_agent.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-devopshub", "nullsoft8411/devopshub", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-1", "proj-devopshub", "in_progress", "validation"),
        )
        conn.execute(
            """
            insert into findings(id, run_id, project_id, signature, category, severity, file_path, line_number, title)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "finding-1",
                "run-1",
                "proj-devopshub",
                "src/auth/token.ts:validation:process",
                "validation",
                "blocking",
                "src/auth/token.ts",
                12,
                "process is not defined",
            ),
        )
        conn.execute(
            """
            insert into tasks(id, finding_id, run_id, project_id, status, priority, title, affected_file)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-1",
                "finding-1",
                "run-1",
                "proj-devopshub",
                "pending",
                10,
                "Fix validation blocker",
                "src/auth/token.ts",
            ),
        )
        conn.execute(
            """
            insert into qa_gate_results(id, run_id, gate, status, evidence, next_action)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                "gate-1",
                "run-1",
                "validation",
                "blocking",
                "npm test exited 1",
                "fix validation before completion",
            ),
        )
        conn.commit()

        row = conn.execute(
            """
            select projects.target, runs.status, findings.severity, tasks.status, qa_gate_results.status
            from projects
            join runs on runs.project_id = projects.id
            join findings on findings.run_id = runs.id
            join tasks on tasks.finding_id = findings.id
            join qa_gate_results on qa_gate_results.run_id = runs.id
            where projects.id = ?
            """,
            ("proj-devopshub",),
        ).fetchone()

    assert row == (
        "nullsoft8411/devopshub",
        "in_progress",
        "blocking",
        "pending",
        "blocking",
    )


def test_approval_scope_is_stored_without_secret_values(tmp_path: Path) -> None:
    db_path = tmp_path / "code_sentinel_agent.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            insert into approvals(
              id, run_id, target_project, branch, allowed_paths_json,
              allowed_actions_json, approved_by, approval_evidence
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "approval-1",
                "run-1",
                "nullsoft8411/devopshub",
                "code-sentinel/test",
                '["docs/proof.md"]',
                '["file_write"]',
                "operator",
                "chat approval without secret material",
            ),
        )
        conn.commit()
        row = conn.execute(
            "select target_project, allowed_paths_json, allowed_actions_json, approval_evidence from approvals"
        ).fetchone()

    assert row == (
        "nullsoft8411/devopshub",
        '["docs/proof.md"]',
        '["file_write"]',
        "chat approval without secret material",
    )
