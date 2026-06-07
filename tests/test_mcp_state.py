from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import re

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database
from code_sentinel_agent.mcp_state import call_tool, detect_backend
from code_sentinel_agent.postgres_state import bind_literal, build_psql_command, parse_pg_env


RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def seed_project(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-devopshub", "nullsoft8411/devopshub", "main"),
        )
        conn.execute(
            """
            insert into memories(id, project_id, latest_ref, memory_json, stale_memory_decision)
            values (?, ?, ?, ?, ?)
            """,
            (
                "memory-seed",
                "proj-devopshub",
                "main@seed",
                json.dumps(
                    {
                        "next_autonomous_step": "refresh repository truth and rerun qa gates",
                        "bounded_fix_queue": ["src/auth/token.ts"],
                    }
                ),
                "needs_repository_refresh",
            ),
        )
        conn.commit()


def test_mcp_state_reads_project_and_memory(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)

    code, project_payload = call_tool(db_path, "state_project_get", {"project_id": "proj-devopshub"})
    assert code == 0
    assert project_payload["project"]["target"] == "nullsoft8411/devopshub"

    code, memory_payload = call_tool(db_path, "state_memory_get", {"project_id": "proj-devopshub"})
    assert code == 0
    assert memory_payload["memory"]["latest_ref"] == "main@seed"
    assert memory_payload["memory"]["memory_json"]["bounded_fix_queue"] == ["src/auth/token.ts"]
    assert memory_payload["repository_delta"] == "repository truth must be checked before trusting memory"


def test_mcp_state_lock_blocks_parallel_run_until_release(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)

    code, first_lock = call_tool(
        db_path,
        "state_lock_acquire",
        {"project_id": "proj-devopshub", "run_id": "run-1", "owner": "agent-a"},
    )
    assert code == 0
    assert first_lock["lock_acquired"] is True

    code, second_lock = call_tool(
        db_path,
        "state_lock_acquire",
        {"project_id": "proj-devopshub", "run_id": "run-2", "owner": "agent-b"},
    )
    assert code == 2
    assert second_lock["blocker_code"] == "STATE_LOCK_HELD"
    assert second_lock["active_lock"]["run_id"] == "run-1"

    code, release = call_tool(
        db_path,
        "state_lock_release",
        {"project_id": "proj-devopshub", "run_id": "run-1", "owner": "agent-a"},
    )
    assert code == 0
    assert release["lock_released"] is True

    code, retry_lock = call_tool(
        db_path,
        "state_lock_acquire",
        {"project_id": "proj-devopshub", "run_id": "run-2", "owner": "agent-b"},
    )
    assert code == 0
    assert retry_lock["lock"]["run_id"] == "run-2"


def test_mcp_state_run_start_and_append_event(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    call_tool(
        db_path,
        "state_lock_acquire",
        {"project_id": "proj-devopshub", "run_id": "run-1", "owner": "agent-a"},
    )

    code, run_payload = call_tool(
        db_path,
        "state_run_start",
        {"project_id": "proj-devopshub", "run_id": "run-1", "latest_ref": "main@new"},
    )
    assert code == 0
    assert run_payload["state_access_mode"] == "mcp_state_db"
    assert run_payload["run"]["status"] == "in_progress"
    assert run_payload["loaded_memory"]["next_autonomous_step"] == "refresh repository truth and rerun qa gates"
    assert run_payload["next_autonomous_step"] == (
        "preflight main@new before continuing: refresh repository truth and rerun qa gates"
    )

    code, event_payload = call_tool(
        db_path,
        "state_append_event",
        {"run_id": "run-1", "kind": "qa_gate", "path": "runs/run-1/qa.json"},
    )
    assert code == 0
    assert event_payload["event_appended"] is True


def test_mcp_state_cli_dispatches_json_tool_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)

    result = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_project_get",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub"}),
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["project"]["target"] == "nullsoft8411/devopshub"


def test_mcp_state_expanded_tools_persist_findings_tasks_qa_execution_audit_and_pr(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    start = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-1", "latest_ref": "main@new"}),
    )
    assert start.returncode == 0, start.stderr

    scan_job = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_job_create",
        "--payload-json",
        json.dumps(
            {
                "id": "scan-job-1",
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "scanner_name": "agent_analysis",
                "scan_type": "agent_context",
                "status": "running",
            }
        ),
    )
    assert scan_job.returncode == 0, scan_job.stderr

    finding = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_finding_upsert",
        "--payload-json",
        json.dumps(
            {
                "id": "scan-finding-1",
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "scan_job_id": "scan-job-1",
                "scanner_name": "agent_analysis",
                "rule_id": "validation_error",
                "signature": "finding:mcp-validation",
                "severity": "high",
                "title": "Validation failed",
                "message": "pytest failed",
                "file_path": "src/app.py",
                "line_number": 5,
            }
        ),
    )
    assert finding.returncode == 0, finding.stderr
    assert parse_json(finding)["created"] is True

    create_tasks = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_tasks_create_from_findings",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-1"}),
    )
    assert create_tasks.returncode == 0, create_tasks.stderr
    task_payload = parse_json(create_tasks)
    assert task_payload["counts"]["created_tasks"] == 1
    task_id = task_payload["created_tasks"][0]["id"]

    qa = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_qa_gate_process",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "gate": "validation",
                "command": "pytest tests/test_app.py -q",
                "exit_code": 1,
                "stderr": "src/app.py:5: AssertionError: failed again",
            }
        ),
    )
    assert qa.returncode == 2
    qa_payload = parse_json(qa)
    assert qa_payload["selected_task_for_agent_takeover"]["id"] == task_id

    execution = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_execution_session_record",
        "--payload-json",
        json.dumps(
            {
                "id": "session-manual",
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "task_id": task_id,
                "session_type": "analysis",
                "script_name": "manual",
                "execution_method": "python_executed_from_cloned_repo",
                "command": "PYTHONPATH=src python3 -m code_sentinel_agent.cli report",
                "status": "passed",
                "output": {"status": "passed"},
            }
        ),
    )
    assert execution.returncode == 0, execution.stderr

    audit = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_audit_event_append",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "event_type": "agent_state_write",
                "summary": "MCP state E2E wrote findings/tasks/session state",
                "payload": {"task_id": task_id},
            }
        ),
    )
    assert audit.returncode == 0, audit.stderr

    pr_state = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_pr_state_set",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-1",
                "branch": "code-sentinel/run-1",
                "base_branch": "main",
                "status": "draft",
                "pr_url": "https://github.com/nullsoft8411/devopshub/pull/1",
                "metadata": {"source": "mcp-e2e"},
            }
        ),
    )
    assert pr_state.returncode == 0, pr_state.stderr

    findings = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_findings_list",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-1"}),
    )
    tasks = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_tasks_list",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-1"}),
    )
    report = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_report_get",
        "--payload-json",
        json.dumps({"run_id": "run-1"}),
    )
    audit_list = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_audit_events_list",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-1"}),
    )
    pr_get = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_pr_state_get",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "branch": "code-sentinel/run-1"}),
    )

    assert findings.returncode == 0, findings.stderr
    assert len(parse_json(findings)["findings"]) == 2
    assert tasks.returncode == 0, tasks.stderr
    assert len(parse_json(tasks)["tasks"]) == 2
    assert report.returncode == 0, report.stderr
    report_payload = parse_json(report)
    assert report_payload["counts"]["findings"] == 2
    assert report_payload["counts"]["tasks"] == 2
    assert report_payload["counts"]["qa_gates"] == 1
    assert report_payload["counts"]["execution_sessions"] == 2
    assert audit_list.returncode == 0, audit_list.stderr
    assert parse_json(audit_list)["audit_events"][0]["event_type"] == "agent_state_write"
    assert pr_get.returncode == 0, pr_get.stderr
    assert parse_json(pr_get)["pr_state"]["status"] == "draft"


def test_mcp_state_postgres_dsn_blocks_until_driver_and_adapter_exist() -> None:
    backend = detect_backend("postgresql://localhost/code_sentinel")
    assert backend["type"] == "postgres"

    code, payload = call_tool(
        "postgresql://localhost/code_sentinel",
        "state_project_get",
        {"project_id": "proj-devopshub"},
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["state_backend"] == "postgres"
    assert payload["blocker_code"] in {"POSTGRES_QUERY_FAILED", "PSQL_CLIENT_MISSING"}


def test_postgres_psql_backend_uses_env_not_dsn_arg() -> None:
    dsn = "postgresql://user:secret@localhost:55432/code_sentinel"
    pg_env = parse_pg_env(dsn)
    command = build_psql_command(dsn)

    assert pg_env["PGHOST"] == "localhost"
    assert pg_env["PGPORT"] == "55432"
    assert pg_env["PGDATABASE"] == "code_sentinel"
    assert pg_env["PGUSER"] == "user"
    assert pg_env["PGPASSWORD"] == "secret"
    assert dsn not in command.command


def test_postgres_sql_literal_binding_escapes_quotes() -> None:
    sql = bind_literal("select :project_id", "project_id", "proj-'one")

    assert sql == "select 'proj-''one'"


def test_postgres_mcp_state_schema_documents_locking_contract() -> None:
    sql_path = RUNTIME_ROOT / "migrations" / "postgres" / "001_mcp_state.sql"
    sql = sql_path.read_text(encoding="utf-8")

    for table in ["projects", "runs", "memories", "qa_gate_results", "artifacts", "state_locks"]:
        assert re.search(rf"create table if not exists {table}\b", sql)
    assert "jsonb not null" in sql
    assert "idx_state_locks_one_active_project" in sql
    assert "pg_try_advisory_xact_lock" in sql
    assert "hashtext(:project_id)" in sql
