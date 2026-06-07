from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli

from code_sentinel_agent.audit_events import AuditEventInput, append_audit_event, list_audit_events
from code_sentinel_agent.db import initialize_database


def seed_audit_state(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-agent", "proj-agent", "in_progress", "audit"),
        )
        conn.commit()


def test_audit_event_append_and_list(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_audit_state(db_path)

    first = append_audit_event(
        str(db_path),
        AuditEventInput(
            id=None,
            project_id="proj-agent",
            run_id="run-agent",
            event_type="task_status_update",
            summary="Task moved to assigned_to_agent",
            payload={"task_id": "task-1"},
        ),
    )
    second = append_audit_event(
        str(db_path),
        AuditEventInput(
            id=first["id"],
            project_id="proj-agent",
            run_id="run-agent",
            event_type="task_status_update",
            summary="Task moved to assigned_to_agent",
            payload={"task_id": "task-1", "idempotent": True},
        ),
    )
    listed = list_audit_events(str(db_path), project_id="proj-agent", run_id="run-agent")

    assert first["event_type"] == "task_status_update"
    assert second["id"] == first["id"]
    assert len(listed) == 1
    assert listed[0]["payload"]["idempotent"] is True


def test_audit_event_cli_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_audit_state(db_path)

    written = run_cli(
        "audit-event",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "project_id": "proj-agent",
                "run_id": "run-agent",
                "event_type": "cycle_exit",
                "summary": "Cycle persisted state",
                "payload": {"status": "passed"},
            }
        ),
    )
    listed = run_cli("audit-events", "--db", str(db_path), "--project-id", "proj-agent", "--run-id", "run-agent")

    assert written.returncode == 0, written.stderr
    assert parse_json(written)["audit_event"]["payload"] == {"status": "passed"}
    assert listed.returncode == 0, listed.stderr
    assert parse_json(listed)["audit_events"][0]["event_type"] == "cycle_exit"
