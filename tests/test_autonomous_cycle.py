from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database


def seed_cycle_state(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-devopshub", "nullsoft8411/devopshub", "main"),
        )
        conn.execute(
            """
            insert into runs(id, project_id, status, current_focus, next_autonomous_step, completed_at)
            values (?, ?, ?, ?, ?, datetime('now'))
            """,
            ("run-prev", "proj-devopshub", "completed", "qa-gate", "rerun qa gates"),
        )
        conn.execute(
            """
            insert into memories(id, project_id, latest_ref, memory_json, stale_memory_decision)
            values (?, ?, ?, ?, ?)
            """,
            (
                "memory-prev",
                "proj-devopshub",
                "main@old",
                json.dumps(
                    {
                        "next_autonomous_step": "rerun qa gates",
                        "bounded_fix_queue": ["src/auth/token.ts"],
                        "qa_gate": {"validation": "blocking"},
                    }
                ),
                "needs_repository_refresh",
            ),
        )
        conn.commit()


def create_cycle_project(root: Path) -> Path:
    project = root / "cycle-project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Local Rules\n\nRun focused checks first.\n", encoding="utf-8")
    (project / "README.md").write_text("# Cycle Project\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'cycle-project'\n", encoding="utf-8")
    package = project / "src" / "cycle_project"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("from cycle_project.app import main\n\ndef test_main():\n    assert main() == 'ok'\n", encoding="utf-8")
    return project


def seed_execution_task(db_path: Path, *, run_id: str, task_id: str = "task-exec") -> None:
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
                f"finding-{task_id}",
                run_id,
                "proj-devopshub",
                f"finding:{task_id}",
                "validation",
                "high",
                "src/app.py",
                "Execution task finding",
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
                task_id,
                f"finding-{task_id}",
                run_id,
                "proj-devopshub",
                "assigned_to_agent",
                90,
                "Fix execution task",
                "src/app.py",
                "standalone",
                f"standalone:finding:{task_id}",
            ),
        )
        conn.commit()


def seed_cycle_approval(db_path: Path, *, run_id: str, allowed_paths: list[str]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into approvals(
              id, run_id, target_project, branch, allowed_paths_json,
              allowed_actions_json, approved_by, approval_evidence
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"approval-{run_id}",
                run_id,
                "nullsoft8411/devopshub",
                "main",
                json.dumps(allowed_paths),
                json.dumps(["file_write"]),
                "operator",
                "explicit per-run test approval",
            ),
        )
        conn.commit()


def test_resume_cycle_loads_memory_creates_run_and_emits_next_step(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)

    result = run_cli(
        "resume-cycle",
        "--db",
        str(db_path),
        "--project-id",
        "proj-devopshub",
        "--run-id",
        "run-next",
        "--latest-ref",
        "main@new",
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["project_id"] == "proj-devopshub"
    assert payload["previous_run"]["id"] == "run-prev"
    assert payload["loaded_memory"]["next_autonomous_step"] == "rerun qa gates"
    assert payload["repository_delta"] == "repository truth must be checked before trusting memory"
    assert payload["stale_memory_decision"] == "needs_repository_refresh"
    assert payload["run"]["id"] == "run-next"
    assert payload["run"]["status"] == "in_progress"
    assert payload["run"]["current_focus"] == "autonomous_cycle"
    assert payload["next_autonomous_step"] == "preflight main@new before continuing: rerun qa gates"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select status, current_focus, next_autonomous_step from runs where id = ?",
            ("run-next",),
        ).fetchone()

    assert row == (
        "in_progress",
        "autonomous_cycle",
        "preflight main@new before continuing: rerun qa gates",
    )


def test_run_cycle_selects_existing_task_and_releases_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-next", "proj-devopshub", "in_progress", "autonomous_cycle"),
        )
        conn.execute(
            """
            insert into findings(id, run_id, project_id, signature, category, severity, file_path, title)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "finding-cycle",
                "run-next",
                "proj-devopshub",
                "finding:cycle",
                "validation",
                "high",
                "src/app.py",
                "Cycle task",
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
                "task-cycle",
                "finding-cycle",
                "run-next",
                "proj-devopshub",
                "pending",
                30,
                "Fix Cycle task",
                "src/app.py",
                "standalone",
                "standalone:finding:cycle",
            ),
        )
        conn.commit()

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps({"project_id": "proj-devopshub", "run_id": "run-next", "latest_ref": "main@new"}),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["selected_task_for_agent_takeover"]["id"] == "task-cycle"
    assert payload["selected_task_for_agent_takeover"]["status"] == "assigned_to_agent"
    assert payload["improvement_work_package"]["status"] == "assigned_to_agent"
    assert payload["improvement_work_package"]["task"]["id"] == "task-cycle"
    assert payload["improvement_work_package"]["finding"]["id"] == "finding-cycle"
    assert payload["improvement_work_package"]["file_evidence"]["status"] == "not_available"
    assert payload["improvement_work_package"]["approval_required"] is True
    assert payload["cycle_session"]["session_type"] == "autonomous_cycle"
    assert payload["cycle_session"]["output"]["improvement_work_package_status"] == "assigned_to_agent"
    assert payload["audit_event"]["event_type"] == "autonomous_cycle"
    assert payload["audit_event"]["payload"]["selected_task_id"] == "task-cycle"
    assert payload["audit_event"]["payload"]["improvement_work_package_status"] == "assigned_to_agent"
    assert payload["report"]["counts"]["execution_sessions"] == 1
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]
        run_row = conn.execute(
            "select current_focus, next_autonomous_step from runs where id = ?",
            ("run-next",),
        ).fetchone()
        task_status = conn.execute("select status from tasks where id = ?", ("task-cycle",)).fetchone()[0]

    assert active_locks == 0
    assert run_row[0] == "task_takeover"
    assert "task-cycle" in run_row[1]
    assert task_status == "assigned_to_agent"


def test_run_cycle_blocks_when_project_lock_is_held_by_another_writer(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = now + timedelta(minutes=15)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at)
            values (?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "lock-existing",
                "proj-devopshub",
                "run-other",
                "agent-other",
                now.isoformat().replace("+00:00", "Z"),
                expires_at.isoformat().replace("+00:00", "Z"),
            ),
        )
        conn.commit()

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-lock-contender",
                "latest_ref": "main@lock-contender",
                "owner": "agent-contender",
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "STATE_LOCK_HELD"
    assert payload["active_lock"]["run_id"] == "run-other"
    assert payload["active_lock"]["owner"] == "agent-other"

    with sqlite3.connect(db_path) as conn:
        contender_run = conn.execute(
            "select count(*) from runs where id = ?",
            ("run-lock-contender",),
        ).fetchone()[0]
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert contender_run == 0
    assert active_locks == 1


def test_run_cycle_collects_project_scan_file_plugin_and_audit_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    project_path = create_cycle_project(tmp_path)
    seed_cycle_state(db_path)

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-project-evidence",
                "latest_ref": "main@project-evidence",
                "project_path": str(project_path),
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["project_evidence"]["status"] == "passed"
    assert payload["project_evidence"]["project_context"]["project_rules"]["agents_md_count"] >= 1
    assert payload["project_evidence"]["file_checks_count"] >= 4
    assert payload["project_evidence"]["scan_job"]["status"] == "completed"
    assert payload["project_evidence"]["scan_job"]["files_scanned"] == payload["project_evidence"]["file_checks_count"]
    assert payload["improvement_work_package"] is None
    assert {item["plugin_name"] for item in payload["project_evidence"]["plugin_executions"]} == {
        "file_inventory",
        "project_context",
    }
    assert payload["cycle_session"]["output"]["project_evidence_status"] == "passed"
    assert payload["audit_event"]["payload"]["project_evidence_status"] == "passed"
    assert payload["audit_event"]["payload"]["scan_job_id"] == payload["project_evidence"]["scan_job"]["id"]
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        scan_row = conn.execute(
            "select status, files_scanned from scan_jobs where run_id = ?",
            ("run-project-evidence",),
        ).fetchone()
        file_checks = conn.execute(
            "select count(*) from file_checks where run_id = ?",
            ("run-project-evidence",),
        ).fetchone()[0]
        plugin_executions = conn.execute(
            "select count(*) from plugin_executions where run_id = ?",
            ("run-project-evidence",),
        ).fetchone()[0]
        audit_count = conn.execute(
            "select count(*) from audit_events where run_id = ?",
            ("run-project-evidence",),
        ).fetchone()[0]
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert scan_row == ("completed", file_checks)
    assert plugin_executions == 2
    assert audit_count == 1
    assert active_locks == 0


def test_run_cycle_blocks_when_project_evidence_collection_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    missing_project = tmp_path / "missing-project"

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-project-evidence-blocked",
                "latest_ref": "main@project-evidence-blocked",
                "project_path": str(missing_project),
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocking"
    assert payload["blocker_code"] == "PROJECT_PATH_MISSING"
    assert payload["project_evidence"]["status"] == "blocking"
    assert payload["project_evidence"]["scan_job"]["status"] == "failed"
    assert payload["project_evidence"]["file_checks_count"] == 0
    assert payload["cycle_session"]["output"]["project_evidence_status"] == "blocking"
    assert payload["audit_event"]["payload"]["project_evidence_status"] == "blocking"
    assert payload["audit_event"]["payload"]["file_checks_count"] == 0
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        scan_row = conn.execute(
            "select status, error_message from scan_jobs where run_id = ?",
            ("run-project-evidence-blocked",),
        ).fetchone()
        plugin_statuses = [
            row[0]
            for row in conn.execute(
                "select status from plugin_executions where run_id = ? order by plugin_name",
                ("run-project-evidence-blocked",),
            ).fetchall()
        ]
        file_checks = conn.execute(
            "select count(*) from file_checks where run_id = ?",
            ("run-project-evidence-blocked",),
        ).fetchone()[0]
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert scan_row == ("failed", "PROJECT_PATH_MISSING")
    assert plugin_statuses == ["blocking", "blocking"]
    assert file_checks == 0
    assert active_locks == 0


def test_run_cycle_work_package_includes_readable_file_evidence_and_validation_command(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    project_path = create_cycle_project(tmp_path)
    seed_cycle_state(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-readable-file", "proj-devopshub", "in_progress", "autonomous_cycle"),
        )
        conn.execute(
            """
            insert into findings(id, run_id, project_id, signature, category, severity, file_path, title)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "finding-readable-file",
                "run-readable-file",
                "proj-devopshub",
                "finding:readable-file",
                "validation",
                "high",
                "src/cycle_project/app.py",
                "Cycle readable file task",
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
                "task-readable-file",
                "finding-readable-file",
                "run-readable-file",
                "proj-devopshub",
                "pending",
                30,
                "Fix readable file task",
                "src/cycle_project/app.py",
                "standalone",
                "standalone:finding:readable-file",
            ),
        )
        conn.commit()

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-readable-file",
                "latest_ref": "main@readable-file",
                "project_path": str(project_path),
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    package = payload["improvement_work_package"]
    assert package["task"]["id"] == "task-readable-file"
    assert package["task"]["status"] == "assigned_to_agent"
    assert package["file_evidence"]["status"] == "passed"
    assert package["file_evidence"]["path"] == "src/cycle_project/app.py"
    assert package["file_evidence"]["content_sha256"]
    assert package["file_evidence"]["source_snapshot"]["line_count"] >= 1
    assert [item["gate"] for item in package["qa_review_outcomes"]] == [
        "reuse",
        "duplicate_code",
        "dead_code",
        "unused_code",
    ]
    assert [item["status"] for item in package["qa_review_outcomes"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    assert package["validation_commands"] == ["python3 -m pytest tests -q"]
    assert package["proposed_fix_plan"]["approval_required"] is True
    assert payload["report"]["missing_review_outcomes"] == []
    assert [item["status"] for item in payload["report"]["qa_review_outcomes"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    assert payload["report"]["counts"]["qa_gates"] == 4


def test_run_cycle_blocks_write_without_approval_and_releases_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-write",
                "latest_ref": "main@new",
                "write_request": {
                    "target_project": "nullsoft8411/devopshub",
                    "branch": "main",
                    "path": "src/app.py",
                    "action": "file_write",
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["current_focus"] == "approval"
    assert payload["approval"]["blocker_code"] == "WRITE_APPROVAL_MISSING"
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]
        blocker = conn.execute("select blocker from runs where id = ?", ("run-write",)).fetchone()[0]

    assert active_locks == 0
    assert blocker == "WRITE_APPROVAL_MISSING"


def test_run_cycle_validation_failure_creates_takeover_task_and_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-validation",
                "latest_ref": "main@new",
                "validation_result": {
                    "gate": "validation",
                    "command": "pytest tests/test_app.py -q",
                    "exit_code": 1,
                    "stderr": "src/app.py:5: AssertionError: validation failed",
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocking"
    assert payload["validation_result"]["validation"]["exit_code"] == 1
    assert payload["selected_task_for_agent_takeover"]["affected_file"] == "src/app.py"
    assert payload["report"]["counts"]["findings"] == 1
    assert payload["report"]["counts"]["tasks"] == 1
    assert payload["report"]["counts"]["validation_attempts"] == 1
    assert payload["report"]["counts"]["execution_sessions"] == 2
    assert payload["audit_event"]["summary"] == "Cycle exited with status blocking"
    assert payload["audit_event"]["payload"]["validation_status"] == "blocking"
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert active_locks == 0


def test_run_cycle_blocks_task_execution_result_file_change_without_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    seed_execution_task(db_path, run_id="run-task-no-approval")

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-task-no-approval",
                "latest_ref": "main@task-no-approval",
                "task_execution_result": {
                    "task_id": "task-exec",
                    "files_modified": ["src/app.py"],
                    "validation_result": {
                        "command": "python3 -m pytest tests -q",
                        "exit_code": 0,
                        "stdout": "1 passed",
                    },
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["current_focus"] == "task_execution"
    assert payload["task_execution_result"]["blocker_code"] == "WRITE_APPROVAL_REQUIRED_FOR_TASK_RESULT"
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        task_status = conn.execute("select status from tasks where id = ?", ("task-exec",)).fetchone()[0]
        session_count = conn.execute(
            "select count(*) from agent_execution_sessions where run_id = ?",
            ("run-task-no-approval",),
        ).fetchone()[0]
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert task_status == "assigned_to_agent"
    assert session_count == 0
    assert active_locks == 0


def test_run_cycle_records_approved_task_execution_result_and_completes_task(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    seed_execution_task(db_path, run_id="run-task-approved")
    seed_cycle_approval(db_path, run_id="run-task-approved", allowed_paths=["src/app.py"])

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-task-approved",
                "latest_ref": "main@task-approved",
                "write_request": {
                    "target_project": "nullsoft8411/devopshub",
                    "branch": "main",
                    "path": "src/app.py",
                    "action": "file_write",
                },
                "task_execution_result": {
                    "task_id": "task-exec",
                    "execution_method": "agent_native_python",
                    "script_name": "agent-result-ingest",
                    "files_modified": ["src/app.py"],
                    "validation_result": {
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
    assert payload["task_execution_result"]["status"] == "passed"
    assert payload["task_execution_result"]["task"]["status"] == "completed"
    assert payload["task_execution_result"]["approval_enforced"] is True
    assert payload["selected_task_for_agent_takeover"] is None
    assert payload["report"]["counts"]["validation_attempts"] == 1
    assert payload["report"]["counts"]["execution_sessions"] == 3
    assert payload["cycle_session"]["output"]["task_execution_status"] == "passed"
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        task_row = conn.execute(
            "select status, attempt_count from tasks where id = ?",
            ("task-exec",),
        ).fetchone()
        execution_row = conn.execute(
            """
            select status, files_modified_json
            from agent_execution_sessions
            where run_id = ? and session_type = 'task_execution'
            """,
            ("run-task-approved",),
        ).fetchone()
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert task_row == ("completed", 0)
    assert json.loads(execution_row[1]) == ["src/app.py"]
    assert execution_row[0] == "passed"
    assert active_locks == 0


def test_run_cycle_records_failed_task_execution_result_and_increments_attempt(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)
    seed_execution_task(db_path, run_id="run-task-failed")

    result = run_cli(
        "run-cycle",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-devopshub",
                "run_id": "run-task-failed",
                "latest_ref": "main@task-failed",
                "task_execution_result": {
                    "task_id": "task-exec",
                    "validation_result": {
                        "gate": "task_validation",
                        "command": "python3 -m pytest tests -q",
                        "exit_code": 1,
                        "stderr": "src/app.py:7: AssertionError: still failing",
                    },
                },
            }
        ),
    )
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocking"
    assert payload["task_execution_result"]["status"] == "blocking"
    assert payload["task_execution_result"]["task"]["status"] == "failed_validation"
    assert payload["task_execution_result"]["task"]["attempt_count"] == 1
    assert payload["cycle_session"]["output"]["task_execution_status"] == "blocking"
    assert payload["audit_event"]["payload"]["task_execution_status"] == "blocking"
    assert payload["report"]["counts"]["validation_attempts"] == 1
    assert payload["report"]["counts"]["execution_sessions"] == 3
    assert payload["lock_released"] is True

    with sqlite3.connect(db_path) as conn:
        task_row = conn.execute(
            "select status, attempt_count from tasks where id = ?",
            ("task-exec",),
        ).fetchone()
        active_locks = conn.execute(
            "select count(*) from state_locks where status = 'active' and released_at is null"
        ).fetchone()[0]

    assert task_row == ("failed_validation", 1)
    assert active_locks == 0
