from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import re

import pytest

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database
from code_sentinel_agent.mcp_state import call_tool, detect_backend
from code_sentinel_agent import postgres_state
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


def create_mcp_cycle_project(root: Path) -> Path:
    project = root / "mcp-cycle-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'mcp-cycle-project'\n", encoding="utf-8")
    package = project / "src" / "mcp_cycle_project"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("from mcp_cycle_project.app import main\n\ndef test_main():\n    assert main() == 'ok'\n", encoding="utf-8")
    return project


def seed_takeover_task(db_path: Path, *, run_id: str, affected_file: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            (run_id, "proj-devopshub", "in_progress", "autonomous_cycle"),
        )
        conn.execute(
            """
            insert into findings(id, run_id, project_id, signature, category, severity, file_path, title)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"finding-{run_id}",
                run_id,
                "proj-devopshub",
                f"finding:{run_id}",
                "validation",
                "high",
                affected_file,
                "MCP run-cycle task finding",
            ),
        )
        conn.execute(
            """
            insert into tasks(
              id, finding_id, run_id, project_id, status, priority, title,
              affected_file, task_type, task_signature
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"task-{run_id}",
                f"finding-{run_id}",
                run_id,
                "proj-devopshub",
                "pending",
                50,
                "Fix MCP run-cycle task",
                affected_file,
                "standalone",
                f"standalone:finding:{run_id}",
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

    code, retry_same_lock_after_release = call_tool(
        db_path,
        "state_lock_release",
        {"project_id": "proj-devopshub", "run_id": "run-2", "owner": "agent-b"},
    )
    assert code == 0
    assert retry_same_lock_after_release["lock_released"] is True

    code, reacquired_same_lock = call_tool(
        db_path,
        "state_lock_acquire",
        {"project_id": "proj-devopshub", "run_id": "run-2", "owner": "agent-b"},
    )
    assert code == 0
    assert reacquired_same_lock["lock_acquired"] is True
    assert reacquired_same_lock["lock"]["run_id"] == "run-2"
    assert reacquired_same_lock["lock"]["status"] == "active"
    assert reacquired_same_lock["lock"]["released_at"] is None


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


@pytest.mark.parametrize("tool_name", ["raw_sql", "sql_query", "run_command"])
def test_mcp_state_blocks_raw_sql_and_generic_command_tools(tmp_path: Path, tool_name: str) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)

    code, payload = call_tool(db_path, tool_name, {"query": "select * from projects"})

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "UNKNOWN_MCP_STATE_TOOL"
    assert payload["tool_name"] == tool_name
    assert tool_name not in payload["allowed_tools"]
    assert "state_report_get" in payload["allowed_tools"]


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


def test_mcp_state_records_approved_task_execution_result(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-task-result", "latest_ref": "main@task-result"}),
    )
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_job_create",
        "--payload-json",
        json.dumps(
            {
                "id": "scan-task-result",
                "project_id": "proj-devopshub",
                "run_id": "run-task-result",
                "scanner_name": "agent_analysis",
                "scan_type": "agent_context",
                "status": "completed",
            }
        ),
    )
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_finding_upsert",
        "--payload-json",
        json.dumps(
            {
                "id": "finding-task-result",
                "project_id": "proj-devopshub",
                "run_id": "run-task-result",
                "scan_job_id": "scan-task-result",
                "scanner_name": "agent_analysis",
                "rule_id": "validation_error",
                "signature": "finding:task-result",
                "severity": "high",
                "title": "Validation failed",
                "message": "pytest failed",
                "file_path": "src/app.py",
                "line_number": 5,
            }
        ),
    )
    create_tasks = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_tasks_create_from_findings",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-task-result"}),
    )
    task_id = parse_json(create_tasks)["created_tasks"][0]["id"]
    approval = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_approval_record",
        "--payload-json",
        json.dumps(
            {
                "id": "approval-task-result",
                "run_id": "run-task-result",
                "target_project": "nullsoft8411/devopshub",
                "branch": "main",
                "allowed_paths": ["src/app.py"],
                "allowed_actions": ["file_write"],
                "approved_by": "operator",
                "approval_evidence": "explicit per-run MCP test approval",
            }
        ),
    )
    assert approval.returncode == 0, approval.stderr
    assert parse_json(approval)["approval"]["allowed_paths"] == ["src/app.py"]

    result = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_task_execution_result",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-task-result",
                "write_request": {
                    "target_project": "nullsoft8411/devopshub",
                    "branch": "main",
                    "path": "src/app.py",
                    "action": "file_write",
                },
                "task_execution_result": {
                    "task_id": task_id,
                    "files_modified": ["src/app.py"],
                    "validation_result": {
                        "gate": "task_validation",
                        "command": "python3 -m pytest tests -q",
                        "exit_code": 0,
                        "stdout": "1 passed",
                    },
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["task"]["status"] == "completed"
    assert payload["approval_enforced"] is True
    assert payload["execution_session"]["session_type"] == "task_execution"

    report = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_report_get",
        "--payload-json",
        json.dumps({"run_id": "run-task-result"}),
    )
    report_payload = parse_json(report)
    assert report_payload["counts"]["validation_attempts"] == 1
    assert report_payload["counts"]["execution_sessions"] == 2
    assert report_payload["selected_task_for_agent_takeover"] is None


def test_mcp_state_task_execution_result_accepts_static_readback_diff_validation(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-static-validation", "latest_ref": "main@static"}),
    )
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_job_create",
        "--payload-json",
        json.dumps(
            {
                "id": "scan-static-validation",
                "project_id": "proj-devopshub",
                "run_id": "run-static-validation",
                "scanner_name": "agent_analysis",
                "scan_type": "agent_context",
                "status": "completed",
            }
        ),
    )
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_finding_upsert",
        "--payload-json",
        json.dumps(
            {
                "id": "finding-static-validation",
                "project_id": "proj-devopshub",
                "run_id": "run-static-validation",
                "scan_job_id": "scan-static-validation",
                "scanner_name": "agent_analysis",
                "rule_id": "bounded_edit_proof",
                "signature": "finding:static-validation",
                "severity": "medium",
                "title": "Bounded edit proof missing",
                "file_path": "docs/bounded-edit-e2e-proof.md",
                "line_number": 1,
            }
        ),
    )
    create_tasks = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_tasks_create_from_findings",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-static-validation"}),
    )
    task_id = parse_json(create_tasks)["created_tasks"][0]["id"]
    run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_approval_record",
        "--payload-json",
        json.dumps(
            {
                "id": "approval-static-validation",
                "run_id": "run-static-validation",
                "target_project": "nullsoft8411/devopshub",
                "branch": "code-sentinel/static-validation",
                "allowed_paths": ["docs/bounded-edit-e2e-proof.md"],
                "allowed_actions": ["file_write"],
                "approved_by": "operator",
                "approval_evidence": "explicit per-run MCP static validation test approval",
            }
        ),
    )

    result = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_task_execution_result",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-static-validation",
                "write_request": {
                    "target_project": "nullsoft8411/devopshub",
                    "branch": "code-sentinel/static-validation",
                    "path": "docs/bounded-edit-e2e-proof.md",
                    "action": "file_write",
                },
                "task_execution_result": {
                    "task_id": task_id,
                    "files_modified": ["docs/bounded-edit-e2e-proof.md"],
                    "validation_result": {
                        "gate": "task_validation",
                        "command": "read_file + git_diff approved file",
                        "exit_code": 0,
                        "stdout": "readback and diff inspected",
                    },
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["task"]["status"] == "completed"
    assert payload["validation_result"]["validation"]["command"] == "read_file + git_diff approved file"
    assert payload["execution_session"]["files_modified"] == ["docs/bounded-edit-e2e-proof.md"]


def test_mcp_state_qa_review_outcomes_round_trip_through_report(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    start = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-review-outcomes", "latest_ref": "main@review"}),
    )
    assert start.returncode == 0, start.stderr

    for gate in ("reuse", "duplicate_code", "dead_code", "unused_code"):
        result = run_cli(
            "mcp-state",
            "--db",
            str(db_path),
            "--tool",
            "state_qa_gate_process",
            "--payload-json",
            json.dumps(
                {
                    "project_id": "proj-devopshub",
                    "run_id": "run-review-outcomes",
                    "gate": gate,
                    "command": "read_file + git_diff approved file",
                    "exit_code": 0,
                    "stdout": f"{gate} reviewed from live repository evidence",
                }
            ),
        )
        assert result.returncode == 0, result.stderr
        payload = parse_json(result)
        assert payload["gate"] == gate
        assert payload["validation"]["status"] == "passed"

    report = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_report_get",
        "--payload-json",
        json.dumps({"run_id": "run-review-outcomes"}),
    )
    assert report.returncode == 0, report.stderr
    report_payload = parse_json(report)
    outcomes = report_payload["qa_review_outcomes"]
    assert {outcome["gate"] for outcome in outcomes} == {
        "reuse",
        "duplicate_code",
        "dead_code",
        "unused_code",
    }
    assert all(outcome["status"] == "passed" for outcome in outcomes)
    assert report_payload["missing_review_outcomes"] == []


def test_mcp_state_run_cycle_derives_review_outcomes_from_task_takeover(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    project_path = create_mcp_cycle_project(tmp_path)
    seed_project(db_path)
    seed_takeover_task(
        db_path,
        run_id="run-mcp-cycle-review",
        affected_file="src/mcp_cycle_project/app.py",
    )

    result = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_cycle",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-mcp-cycle-review",
                "latest_ref": "main@mcp-cycle-review",
                "project_path": str(project_path),
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["selected_task_for_agent_takeover"]["id"] == "task-run-mcp-cycle-review"
    package = payload["improvement_work_package"]
    assert package["file_evidence"]["status"] == "passed"
    assert package["file_evidence"]["source_snapshot"]["line_count"] >= 1
    assert [item["gate"] for item in package["qa_review_outcomes"]] == [
        "reuse",
        "duplicate_code",
        "dead_code",
        "unused_code",
    ]
    assert payload["report"]["missing_review_outcomes"] == []
    assert [item["status"] for item in payload["report"]["qa_review_outcomes"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    assert payload["report"]["counts"]["qa_gates"] == 4
    assert payload["lock_released"] is True


def test_mcp_state_analyze_to_state_records_agent_supplied_findings(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-analysis-state", "proj-devopshub", "in_progress", "analysis"),
        )
        conn.commit()

    result = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_analyze_to_state",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-analysis-state",
                "finding_candidates": [
                    {
                        "category": "security",
                        "severity": "high",
                        "file_path": "src/settings.py",
                        "line_number": 1,
                        "title": "Token is hardcoded",
                        "description": "TOKEN='sk-test-should-redact' is stored in source.",
                        "evidence": "TOKEN='sk-test-should-redact'",
                        "source": "agent_reasoning",
                        "rule_id": "secret_assignment",
                    }
                ],
            }
        ),
    )
    payload = parse_json(result)
    serialized = json.dumps(payload, sort_keys=True)

    assert result.returncode == 0, result.stderr
    assert payload["mode"] == "agent_supplied_analysis_persistence"
    assert payload["analysis"]["counts"]["findings"] == 1
    assert payload["task_creation"]["created_tasks"]
    assert payload["selected_task_for_agent_takeover"]["affected_file"] == "src/settings.py"
    assert payload["report"]["counts"]["findings"] == 1
    assert payload["report"]["counts"]["tasks"] == 1
    assert "sk-test-should-redact" not in serialized


def test_mcp_state_analyze_to_state_dedupes_findings_and_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-analysis-dedupe", "proj-devopshub", "in_progress", "analysis"),
        )
        conn.commit()

    payload = {
        "project_id": "proj-devopshub",
        "run_id": "run-analysis-dedupe",
        "latest_ref": "main@analysis-dedupe",
        "finding_candidates": [
            {
                "category": "code_quality",
                "severity": "high",
                "file_path": "src/settings.py",
                "line_number": 1,
                "title": "Duplicate candidate should not duplicate state",
                "description": "The same Agent finding is submitted twice.",
                "evidence": "src/settings.py:1 inspected twice",
                "source": "agent_reasoning",
                "rule_id": "agent_dedupe_review",
            }
        ],
    }

    first = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_analyze_to_state",
        "--payload-json",
        json.dumps(payload),
    )
    second = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_analyze_to_state",
        "--payload-json",
        json.dumps(payload),
    )
    first_payload = parse_json(first)
    second_payload = parse_json(second)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_payload["persisted_findings"][0]["created"] is True
    assert second_payload["persisted_findings"][0]["created"] is False
    assert first_payload["task_creation"]["counts"]["created_tasks"] == 1
    assert second_payload["task_creation"]["counts"]["created_tasks"] == 0
    assert second_payload["report"]["counts"]["findings"] == 1
    assert second_payload["report"]["counts"]["tasks"] == 1


def test_local_agent_analysis_to_cycle_e2e(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    project_path = create_mcp_cycle_project(tmp_path)
    (project_path / "AGENTS.md").write_text(
        "# Repo rules\n- Preserve validation evidence before claiming completion.\n",
        encoding="utf-8",
    )
    seed_project(db_path)

    context = run_cli("project-context", "--project", str(project_path), "--max-files", "20")
    context_payload = parse_json(context)
    assert context.returncode == 0, context.stderr
    assert context_payload["status"] == "passed"
    assert context_payload["project_rules"]["agents_md_count"] >= 1
    assert context_payload["context_summary"]["has_project_rules"] is True
    assert "python" in context_payload["check_detection"]["signals"]
    selected_context_file = next(
        item
        for item in context_payload["file_inventory"]["files"]
        if item["path"] == "src/mcp_cycle_project/app.py"
    )

    contract = run_cli(
        "analysis-contract",
        "--project-id",
        "proj-devopshub",
        "--run-id",
        "run-analysis-cycle",
        "--target-project",
        str(project_path),
        "--file",
        selected_context_file["path"],
        "--validation-command",
        "python3 -m pytest tests -q",
    )
    contract_payload = parse_json(contract)
    assert contract.returncode == 0, contract.stderr
    assert contract_payload["mode"] == "agent_supplied_analysis_contract"
    assert contract_payload["analysis_payload_template"]["finding_candidates"] == []

    start = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-analysis-cycle",
                "latest_ref": "main@analysis-cycle",
            }
        ),
    )
    assert start.returncode == 0, start.stderr

    analysis_payload = {
        **contract_payload["analysis_payload_template"],
        "latest_ref": "main@analysis-cycle",
        "finding_candidates": [
            {
                "category": "code_quality",
                "severity": "high",
                "file_path": selected_context_file["path"],
                "line_number": 1,
                "title": "Main return needs review",
                "description": "Agent reasoning selected this file for a bounded review task.",
                "evidence": f"{selected_context_file['path']}:1 selected from project_context file_inventory",
                "source": "agent_reasoning",
                "rule_id": "agent_bounded_review",
            }
        ],
    }
    analyze = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_analyze_to_state",
        "--payload-json",
        json.dumps(analysis_payload),
    )
    analyze_payload = parse_json(analyze)
    assert analyze.returncode == 0, analyze.stderr
    assert analyze_payload["mode"] == "agent_supplied_analysis_persistence"
    assert analyze_payload["task_creation"]["counts"]["created_tasks"] == 1

    cycle = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_cycle",
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-analysis-cycle",
                "latest_ref": "main@analysis-cycle",
                "project_path": str(project_path),
            }
        ),
    )
    cycle_payload = parse_json(cycle)

    assert cycle.returncode == 0, cycle.stderr
    assert cycle_payload["status"] == "passed"
    assert cycle_payload["selected_task_for_agent_takeover"]["affected_file"] == "src/mcp_cycle_project/app.py"
    assert cycle_payload["improvement_work_package"]["file_evidence"]["status"] == "passed"
    assert cycle_payload["report"]["counts"]["findings"] == 1
    assert cycle_payload["report"]["counts"]["tasks"] == 1
    assert cycle_payload["lock_released"] is True


def test_mcp_state_expanded_tools_accept_nested_payload_wrapper(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    seed_project(db_path)
    start = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_run_start",
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-wrapper", "latest_ref": "main@wrapper"}),
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
                "payload": {
                    "id": "scan-wrapper",
                    "project_id": "proj-devopshub",
                    "run_id": "run-wrapper",
                    "scanner_name": "agent_analysis",
                    "scan_type": "agent_context",
                    "status": "completed",
                }
            }
        ),
    )
    assert scan_job.returncode == 0, scan_job.stderr
    assert parse_json(scan_job)["scan_job"]["id"] == "scan-wrapper"

    finding = run_cli(
        "mcp-state",
        "--db",
        str(db_path),
        "--tool",
        "state_scan_finding_upsert",
        "--payload-json",
        json.dumps(
            {
                "payload": {
                    "id": "finding-wrapper",
                    "project_id": "proj-devopshub",
                    "run_id": "run-wrapper",
                    "scan_job_id": "scan-wrapper",
                    "scanner_name": "agent_analysis",
                    "rule_id": "validation_error",
                    "signature": "finding:wrapper",
                    "severity": "high",
                    "title": "Validation failed",
                    "message": "pytest failed",
                    "file_path": "src/app.py",
                    "line_number": 5,
                }
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
        json.dumps({"payload": {"project_id": "proj-devopshub", "run_id": "run-wrapper"}}),
    )
    assert create_tasks.returncode == 0, create_tasks.stderr
    assert parse_json(create_tasks)["counts"]["created_tasks"] == 1


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


@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("state_analyze_to_state", {"project_id": "proj-devopshub", "run_id": "run-1", "finding_candidates": []}),
        ("state_run_cycle", {"project_id": "proj-devopshub", "run_id": "run-1", "latest_ref": "main@test"}),
    ],
)
def test_postgres_backend_blocks_unsupported_local_e2e_state_tools(tool_name: str, payload: dict) -> None:
    code, result = call_tool("postgresql://localhost/code_sentinel", tool_name, payload)

    assert code == 2
    assert result["status"] == "blocked"
    assert result["state_backend"] == "postgres"
    assert result["tool_name"] == tool_name
    assert result["blocker_code"] in {"POSTGRES_BACKEND_NOT_IMPLEMENTED", "POSTGRES_DRIVER_MISSING"}


def test_postgres_memory_get_builds_latest_memory_readback_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run_psql_json(dsn: str, sql: str) -> tuple[int, dict]:
        captured["dsn"] = dsn
        captured["sql"] = sql
        return 0, {"status": "passed", "state_backend": "postgres", "project_id": "proj-devopshub"}

    monkeypatch.setattr(postgres_state, "run_psql_json", fake_run_psql_json)

    code, payload = postgres_state.postgres_memory_get(
        "postgresql://localhost/code_sentinel",
        "proj-devopshub",
    )

    assert code == 0
    assert payload["state_backend"] == "postgres"
    assert captured["dsn"] == "postgresql://localhost/code_sentinel"
    assert "from memories" in captured["sql"]
    assert "order by created_at desc" in captured["sql"]
    assert "PROJECT_NOT_FOUND" in captured["sql"]
    assert "next_autonomous_step" in captured["sql"]
    assert ":project_id" not in captured["sql"]
    assert "'proj-devopshub'" in captured["sql"]


def test_postgres_report_get_builds_partial_report_readback_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run_psql_json(dsn: str, sql: str) -> tuple[int, dict]:
        captured["dsn"] = dsn
        captured["sql"] = sql
        return 0, {"status": "passed", "state_backend": "postgres", "partial_report": True}

    monkeypatch.setattr(postgres_state, "run_psql_json", fake_run_psql_json)

    code, payload = postgres_state.postgres_report_get(
        "postgresql://localhost/code_sentinel",
        "run-postgres",
    )

    assert code == 0
    assert payload["partial_report"] is True
    assert captured["dsn"] == "postgresql://localhost/code_sentinel"
    assert "from runs" in captured["sql"]
    assert "join projects" in captured["sql"]
    assert "qa_gate_results" in captured["sql"]
    assert "artifacts" in captured["sql"]
    assert "unsupported_counts" in captured["sql"]
    assert "RUN_NOT_FOUND" in captured["sql"]
    assert ":run_id" not in captured["sql"]
    assert "'run-postgres'" in captured["sql"]


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
