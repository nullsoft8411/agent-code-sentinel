from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = RUNTIME_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from code_sentinel_agent.db import connect, initialize_database
from code_sentinel_agent.scan_findings import (
    ScanFindingInput,
    ScanJobInput,
    create_scan_job,
    list_scan_findings,
    update_scan_finding_status,
    upsert_scan_finding,
)


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
        "scan_jobs",
        "scan_findings",
        "plugin_executions",
        "file_checks",
        "agent_execution_sessions",
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

    assert versions == [
        ("001_init",),
        ("002_mcp_state_locks",),
        ("003_scan_findings",),
        ("004_task_creation",),
        ("005_execution_sessions",),
    ]


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


def test_scan_job_and_scan_finding_repository_dedupe_and_status_sync(tmp_path: Path) -> None:
    db_path = tmp_path / "code_sentinel_agent.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-agent", "proj-agent", "in_progress", "scan"),
        )
        conn.commit()

    job = create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="agent_analysis",
            scan_type="agent_context",
            target_ref="main@test",
            metadata={"source": "pytest"},
        ),
    )

    assert job["id"] == "scan-job-1"
    assert job["run_id"] == "run-agent"
    assert job["project_id"] == "proj-agent"
    assert job["scanner_name"] == "agent_analysis"

    finding = ScanFindingInput(
        id="scan-finding-1",
        project_id="proj-agent",
        run_id="run-agent",
        scan_job_id="scan-job-1",
        scanner_name="agent_analysis",
        rule_id="secret_assignment",
        signature="finding:secret-assignment-src-service",
        severity="high",
        title="Potential hardcoded secret",
        message="Secret-like assignment detected.",
        suggestion="Move the value to an approved secret source.",
        evidence="API_KEY=[REDACTED]",
        file_path="src/service.py",
        line_number=4,
        column_number=1,
        metadata={"category": "security"},
    )

    created = upsert_scan_finding(str(db_path), finding)
    duplicate = upsert_scan_finding(str(db_path), ScanFindingInput(**{**finding.__dict__, "id": "scan-finding-dup"}))
    listed = list_scan_findings(str(db_path), project_id="proj-agent", run_id="run-agent")
    status_update = update_scan_finding_status(
        str(db_path),
        project_id="proj-agent",
        signature="finding:secret-assignment-src-service",
        status="resolved",
    )

    assert created["created"] is True
    assert created["finding"]["scan_job_id"] == "scan-job-1"
    assert created["finding"]["run_id"] == "run-agent"
    assert created["finding"]["compatibility_finding_id"] == "compat-scan-finding-1"
    assert duplicate["created"] is False
    assert duplicate["finding"]["id"] == "scan-finding-1"
    assert len(listed) == 1
    assert listed[0]["signature"] == "finding:secret-assignment-src-service"
    assert status_update == {
        "status": "passed",
        "scan_finding_id": "scan-finding-1",
        "finding_status": "resolved",
    }

    with connect(db_path) as conn:
        row = conn.execute(
            """
            select scan_jobs.findings_count, scan_findings.status, findings.status
            from scan_jobs
            join scan_findings on scan_findings.scan_job_id = scan_jobs.id
            join findings on findings.id = scan_findings.compatibility_finding_id
            where scan_jobs.id = ?
            """,
            ("scan-job-1",),
        ).fetchone()

    assert row == (1, "resolved", "resolved")
