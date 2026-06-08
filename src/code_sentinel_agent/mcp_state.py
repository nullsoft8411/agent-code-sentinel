from __future__ import annotations

import json
import sqlite3
import importlib.util
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .approvals import approval_check, record_approval
from .analysis_workflow import analyze_to_state
from .audit_events import AuditEventInput, append_audit_event, list_audit_events
from .cycle import next_step_from_memory, row_to_dict, run_autonomous_cycle
from .db import connect, initialize_database
from .execution_sessions import list_execution_sessions, parse_execution_session_payload, record_execution_session
from .postgres_state import (
    initialize_postgres_database,
    postgres_lock_acquire,
    postgres_lock_release,
    postgres_project_get,
    postgres_run_start,
)
from .qg_workflow import process_quality_gate_payload
from .reports import report
from .scan_findings import (
    ScanFindingInput,
    ScanJobInput,
    create_scan_job,
    list_scan_findings,
    update_scan_finding_status,
    upsert_scan_finding,
)
from .task_creation import create_tasks_from_findings
from .task_execution import apply_task_execution_result
from .task_workflow import update_task_status


DEFAULT_OWNER = "workspace-agent"
ALLOWED_SQLITE_TOOLS = [
    "state_project_get",
    "state_memory_get",
    "state_lock_acquire",
    "state_lock_release",
    "state_run_start",
    "state_run_cycle",
    "state_analyze_to_state",
    "state_append_event",
    "state_scan_job_create",
    "state_scan_finding_upsert",
    "state_scan_findings_list",
    "state_scan_finding_status_update",
    "state_tasks_create_from_findings",
    "state_tasks_list",
    "state_task_status_update",
    "state_approval_record",
    "state_task_execution_result",
    "state_qa_gate_process",
    "state_execution_session_record",
    "state_execution_sessions_list",
    "state_report_get",
    "state_audit_event_append",
    "state_audit_events_list",
    "state_pr_state_set",
    "state_pr_state_get",
]


def call_tool(db_path: str | Path, tool_name: str, payload: dict[str, Any] | None = None) -> tuple[int, dict]:
    payload = payload or {}
    backend = detect_backend(db_path)
    if backend["type"] == "postgres":
        return call_postgres_tool(backend["dsn"], tool_name, payload)
    initialize_database(db_path)
    if tool_name == "state_project_get":
        return state_project_get(db_path, require_str(payload, "project_id"))
    if tool_name == "state_memory_get":
        return state_memory_get(db_path, require_str(payload, "project_id"))
    if tool_name == "state_run_start":
        return state_run_start(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            latest_ref=payload.get("latest_ref"),
        )
    if tool_name == "state_run_cycle":
        return state_run_cycle(db_path, payload)
    if tool_name == "state_analyze_to_state":
        return analyze_to_state(db_path, payload)
    if tool_name == "state_lock_acquire":
        return state_lock_acquire(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
            ttl_seconds=int(payload.get("ttl_seconds") or 900),
        )
    if tool_name == "state_lock_release":
        return state_lock_release(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
        )
    if tool_name == "state_append_event":
        return state_append_event(
            db_path,
            run_id=require_str(payload, "run_id"),
            kind=require_str(payload, "kind"),
            path=str(payload.get("path") or ""),
            sha256=payload.get("sha256"),
        )
    payload = unwrap_tool_payload(payload)
    if tool_name == "state_scan_job_create":
        return 0, {"status": "passed", "scan_job": create_scan_job(str(db_path), scan_job_input(payload))}
    if tool_name == "state_scan_finding_upsert":
        return 0, {"status": "passed", **upsert_scan_finding(str(db_path), scan_finding_input(payload))}
    if tool_name == "state_scan_findings_list":
        return 0, {
            "status": "passed",
            "findings": list_scan_findings(
                str(db_path),
                project_id=require_str(payload, "project_id"),
                run_id=payload.get("run_id"),
            ),
        }
    if tool_name == "state_scan_finding_status_update":
        return state_scan_finding_status_update(db_path, payload)
    if tool_name == "state_tasks_create_from_findings":
        return create_tasks_from_findings(
            str(db_path),
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            max_subtasks_per_parent=int(payload.get("max_subtasks_per_parent") or 20),
        )
    if tool_name == "state_tasks_list":
        return state_tasks_list(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=payload.get("run_id"),
        )
    if tool_name == "state_task_status_update":
        return update_task_status(
            str(db_path),
            task_id=require_str(payload, "task_id"),
            status=require_str(payload, "status"),
        )
    if tool_name == "state_approval_record":
        return state_approval_record(db_path, payload)
    if tool_name == "state_task_execution_result":
        return state_task_execution_result(db_path, payload)
    if tool_name == "state_qa_gate_process":
        return process_quality_gate_payload(db_path, payload)
    if tool_name == "state_execution_session_record":
        return 0, {
            "status": "passed",
            "session": record_execution_session(str(db_path), parse_execution_session_payload(payload)),
        }
    if tool_name == "state_execution_sessions_list":
        return 0, {
            "status": "passed",
            "execution_sessions": list_execution_sessions(db_path, run_id=require_str(payload, "run_id")),
        }
    if tool_name == "state_report_get":
        return report(db_path, require_str(payload, "run_id"))
    if tool_name == "state_audit_event_append":
        return state_audit_event_append(db_path, payload)
    if tool_name == "state_audit_events_list":
        return state_audit_events_list(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=payload.get("run_id"),
        )
    if tool_name == "state_pr_state_set":
        return state_pr_state_set(db_path, payload)
    if tool_name == "state_pr_state_get":
        return state_pr_state_get(
            db_path,
            project_id=require_str(payload, "project_id"),
            branch=require_str(payload, "branch"),
        )
    return 2, {
        "status": "blocked",
        "blocker_code": "UNKNOWN_MCP_STATE_TOOL",
        "tool_name": tool_name,
        "allowed_tools": ALLOWED_SQLITE_TOOLS,
    }


def unwrap_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("payload")
    if isinstance(nested, dict) and set(payload) == {"payload"}:
        return nested
    return payload


def state_project_get(db_path: str | Path, project_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select id, target, default_branch, status from projects where id = ?",
            (project_id,),
        ).fetchone()

    if row is None:
        return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before exposing it through MCP", project_id=project_id)
    return 0, {"status": "passed", "project": row_to_dict(row)}


def state_memory_get(db_path: str | Path, project_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, latest_ref, memory_json, stale_memory_decision, created_at
            from memories
            where project_id = ?
            order by created_at desc
            limit 1
            """,
            (project_id,),
        ).fetchone()

    if row is None:
        return 0, {
            "status": "passed",
            "project_id": project_id,
            "memory": None,
            "repository_delta": "no prior memory",
            "stale_memory_decision": "not_applicable",
        }

    return 0, {
        "status": "passed",
        "project_id": project_id,
        "memory": {
            "id": row["id"],
            "latest_ref": row["latest_ref"],
            "memory_json": json.loads(row["memory_json"]),
            "stale_memory_decision": row["stale_memory_decision"],
            "created_at": row["created_at"],
        },
        "repository_delta": "repository truth must be checked before trusting memory",
    }


def state_run_start(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str | None = None,
) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "select id, target, default_branch, status from projects where id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before starting a run", project_id=project_id)

        lock = active_lock(conn, project_id)
        if lock is not None and lock["run_id"] != run_id:
            return lock_blocked(lock, project_id)

        memory = conn.execute(
            """
            select latest_ref, memory_json, stale_memory_decision
            from memories
            where project_id = ?
            order by created_at desc
            limit 1
            """,
            (project_id,),
        ).fetchone()
        loaded_memory = json.loads(memory["memory_json"]) if memory else None
        prior_step = next_step_from_memory(loaded_memory)
        next_step = f"preflight {latest_ref or 'current ref'} before continuing: {prior_step}"
        run = conn.execute(
            "select id, project_id, status, current_focus, next_autonomous_step from runs where id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            conn.execute(
                """
                insert into runs(id, project_id, status, current_focus, next_autonomous_step)
                values (?, ?, ?, ?, ?)
                """,
                (run_id, project_id, "in_progress", "autonomous_cycle", next_step),
            )
            conn.commit()
            run = conn.execute(
                "select id, project_id, status, current_focus, next_autonomous_step from runs where id = ?",
                (run_id,),
            ).fetchone()

    return 0, {
        "status": "passed",
        "state_access_mode": "mcp_state_db",
        "project": row_to_dict(project),
        "run": row_to_dict(run),
        "loaded_memory": loaded_memory,
        "stale_memory_decision": memory["stale_memory_decision"] if memory else "not_applicable",
        "repository_delta": "repository truth must be checked before trusting memory",
        "next_autonomous_step": run["next_autonomous_step"],
    }


def state_run_cycle(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    try:
        return run_autonomous_cycle(db_path, payload)
    except ValueError as exc:
        return 2, {
            "status": "blocked",
            "blocker_code": "INVALID_CYCLE_PAYLOAD",
            "reason": str(exc),
        }


def state_lock_acquire(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str = DEFAULT_OWNER,
    ttl_seconds: int = 900,
) -> tuple[int, dict]:
    now = utc_now()
    expires_at = now + timedelta(seconds=max(1, ttl_seconds))
    lock_id = f"{project_id}:{run_id}:{owner}"
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute("select id from projects where id = ?", (project_id,)).fetchone()
        if project is None:
            return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before acquiring a lock", project_id=project_id)

        expire_stale_locks(conn, now)
        lock = active_lock(conn, project_id)
        if lock is not None:
            if lock["run_id"] == run_id and lock["owner"] == owner:
                return 0, {"status": "passed", "lock_acquired": True, "lock": row_to_dict(lock), "idempotent": True}
            return lock_blocked(lock, project_id)

        conn.execute(
            """
            insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at, released_at, updated_at)
            values (?, ?, ?, ?, 'active', ?, ?, null, ?)
            on conflict(id) do update set
              status = 'active',
              acquired_at = excluded.acquired_at,
              expires_at = excluded.expires_at,
              released_at = null,
              updated_at = excluded.updated_at
            """,
            (lock_id, project_id, run_id, owner, format_dt(now), format_dt(expires_at), format_dt(now)),
        )
        conn.commit()
        lock = active_lock(conn, project_id)

    return 0, {"status": "passed", "lock_acquired": True, "lock": row_to_dict(lock), "idempotent": False}


def state_lock_release(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str = DEFAULT_OWNER,
) -> tuple[int, dict]:
    now = format_dt(utc_now())
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        lock = active_lock(conn, project_id)
        if lock is None:
            return 0, {"status": "passed", "lock_released": False, "reason": "no active lock"}
        if lock["run_id"] != run_id or lock["owner"] != owner:
            return lock_blocked(lock, project_id)
        conn.execute(
            """
            update state_locks
            set status = 'released', released_at = ?, updated_at = ?
            where id = ?
            """,
            (now, now, lock["id"]),
        )
        conn.commit()

    return 0, {"status": "passed", "lock_released": True, "project_id": project_id, "run_id": run_id}


def state_append_event(
    db_path: str | Path,
    *,
    run_id: str,
    kind: str,
    path: str,
    sha256: str | None = None,
) -> tuple[int, dict]:
    artifact_id = f"{run_id}:{kind}:{path or 'event'}"
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("select id, project_id from runs where id = ?", (run_id,)).fetchone()
        if run is None:
            return mcp_blocked("RUN_NOT_FOUND", "start run state before appending events", run_id=run_id)
        conn.execute(
            """
            insert or replace into artifacts(id, run_id, kind, path, sha256)
            values (?, ?, ?, ?, ?)
            """,
            (artifact_id, run_id, kind, path, sha256),
        )
        conn.commit()

    return 0, {"status": "passed", "event_appended": True, "artifact_id": artifact_id, "run_id": run_id}


def state_tasks_list(db_path: str | Path, *, project_id: str, run_id: str | None = None) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if run_id:
            rows = conn.execute(
                """
                select * from tasks
                where project_id = ? and run_id = ?
                order by priority desc, created_at, subtask_order
                """,
                (project_id, run_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select * from tasks
                where project_id = ?
                order by priority desc, created_at, subtask_order
                """,
                (project_id,),
            ).fetchall()
    return 0, {"status": "passed", "tasks": [task_payload(row) for row in rows]}


def state_scan_finding_status_update(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    result = update_scan_finding_status(
        str(db_path),
        project_id=require_str(payload, "project_id"),
        signature=require_str(payload, "signature"),
        status=require_str(payload, "status"),
    )
    return (0 if result["status"] == "passed" else 2), result


def state_approval_record(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    run_id = require_str(payload, "run_id")
    target_project = require_str(payload, "target_project")
    approval = record_approval(
        db_path,
        approval_id=str(payload.get("id") or f"approval-{digest(run_id, target_project)}"),
        run_id=run_id,
        target_project=target_project,
        branch=str(payload.get("branch") or "").strip() or None,
        allowed_paths=require_str_list(payload, "allowed_paths"),
        allowed_actions=require_str_list(payload, "allowed_actions"),
        approved_by=require_str(payload, "approved_by"),
        approval_evidence=require_str(payload, "approval_evidence"),
        expires_at=str(payload.get("expires_at") or "").strip() or None,
    )
    return 0, {"status": "passed", "approval": approval}


def state_task_execution_result(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    approval_result = None
    write_request = payload.get("write_request") if isinstance(payload.get("write_request"), dict) else None
    if write_request:
        approval_code, approval_result = approval_check(
            db_path,
            run_id=require_str(payload, "run_id"),
            target_project=require_str(write_request, "target_project"),
            branch=require_str(write_request, "branch"),
            path=require_str(write_request, "path"),
            action=require_str(write_request, "action"),
        )
        if approval_code != 0:
            return approval_code, approval_result

    execution_payload = payload.get("task_execution_result")
    if not isinstance(execution_payload, dict):
        return 2, {
            "status": "blocked",
            "blocker_code": "TASK_EXECUTION_RESULT_REQUIRED",
            "next_action": "provide task_execution_result with task_id and validation_result",
        }
    return apply_task_execution_result(
        db_path,
        project_id=require_str(payload, "project_id"),
        run_id=require_str(payload, "run_id"),
        execution_result=execution_payload,
        approval_result=approval_result,
    )


def state_audit_event_append(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    event = AuditEventInput(
        id=payload.get("id") if isinstance(payload.get("id"), str) else None,
        project_id=require_str(payload, "project_id"),
        run_id=payload.get("run_id") if isinstance(payload.get("run_id"), str) else None,
        event_type=require_str(payload, "event_type"),
        summary=require_str(payload, "summary"),
        payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    )
    return 0, {"status": "passed", "audit_event": append_audit_event(str(db_path), event)}


def state_audit_events_list(db_path: str | Path, *, project_id: str, run_id: str | None = None) -> tuple[int, dict]:
    return 0, {"status": "passed", "audit_events": list_audit_events(str(db_path), project_id=project_id, run_id=run_id)}


def state_pr_state_set(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict]:
    project_id = require_str(payload, "project_id")
    branch = require_str(payload, "branch")
    run_id = payload.get("run_id")
    state_id = str(payload.get("id") or f"pr-{digest(project_id, branch)}")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            insert into pr_states(id, project_id, run_id, branch, base_branch, status, pr_url, metadata_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(project_id, branch) do update set
              run_id = excluded.run_id,
              base_branch = excluded.base_branch,
              status = excluded.status,
              pr_url = excluded.pr_url,
              metadata_json = excluded.metadata_json,
              updated_at = datetime('now')
            """,
            (
                state_id,
                project_id,
                run_id,
                branch,
                payload.get("base_branch"),
                require_str(payload, "status"),
                payload.get("pr_url"),
                json.dumps(metadata, sort_keys=True),
            ),
        )
        conn.commit()
        row = conn.execute(
            "select * from pr_states where project_id = ? and branch = ?",
            (project_id, branch),
        ).fetchone()
    return 0, {"status": "passed", "pr_state": pr_state_payload(row)}


def state_pr_state_get(db_path: str | Path, *, project_id: str, branch: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from pr_states where project_id = ? and branch = ?",
            (project_id, branch),
        ).fetchone()
    if row is None:
        return mcp_blocked("PR_STATE_NOT_FOUND", "create PR state before reading it", project_id=project_id, branch=branch)
    return 0, {"status": "passed", "pr_state": pr_state_payload(row)}


def active_lock(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        select id, project_id, run_id, owner, status, acquired_at, expires_at, released_at
        from state_locks
        where project_id = ? and status = 'active' and released_at is null
        order by acquired_at desc
        limit 1
        """,
        (project_id,),
    ).fetchone()


def expire_stale_locks(conn: sqlite3.Connection, now: datetime) -> None:
    now_text = format_dt(now)
    conn.execute(
        """
        update state_locks
        set status = 'expired', released_at = ?, updated_at = ?
        where status = 'active'
          and released_at is null
          and expires_at is not null
          and expires_at <= ?
        """,
        (now_text, now_text, now_text),
    )


def lock_blocked(lock: sqlite3.Row, project_id: str) -> tuple[int, dict]:
    return 2, {
        "status": "blocked",
        "blocker_code": "STATE_LOCK_HELD",
        "project_id": project_id,
        "reason": "another run owns the active project state lock",
        "active_lock": row_to_dict(lock),
        "next_action": "retry after the active lock is released or expires",
    }


def mcp_blocked(blocker_code: str, reason: str, **extra: Any) -> tuple[int, dict]:
    payload = {"status": "blocked", "blocker_code": blocker_code, "reason": reason}
    payload.update(extra)
    return 2, payload


def scan_job_input(payload: dict[str, Any]) -> ScanJobInput:
    return ScanJobInput(
        id=require_str(payload, "id"),
        project_id=require_str(payload, "project_id"),
        run_id=payload.get("run_id"),
        scanner_name=require_str(payload, "scanner_name"),
        scan_type=require_str(payload, "scan_type"),
        status=str(payload.get("status") or "running"),
        target_ref=payload.get("target_ref"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def scan_finding_input(payload: dict[str, Any]) -> ScanFindingInput:
    return ScanFindingInput(
        id=require_str(payload, "id"),
        project_id=require_str(payload, "project_id"),
        run_id=payload.get("run_id"),
        scan_job_id=payload.get("scan_job_id"),
        scanner_name=require_str(payload, "scanner_name"),
        rule_id=require_str(payload, "rule_id"),
        signature=require_str(payload, "signature"),
        severity=require_str(payload, "severity"),
        title=require_str(payload, "title"),
        file_path=payload.get("file_path"),
        line_number=payload.get("line_number"),
        column_number=payload.get("column_number"),
        status=str(payload.get("status") or "open"),
        message=payload.get("message"),
        suggestion=payload.get("suggestion"),
        evidence=payload.get("evidence"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def task_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "finding_id": row["finding_id"],
        "run_id": row["run_id"],
        "project_id": row["project_id"],
        "parent_task_id": row["parent_task_id"],
        "status": row["status"],
        "priority": row["priority"],
        "title": row["title"],
        "affected_file": row["affected_file"],
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "task_type": row["task_type"],
        "task_signature": row["task_signature"],
        "subtask_order": row["subtask_order"],
        "progress": json.loads(row["progress_json"] or "{}"),
    }


def pr_state_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "branch": row["branch"],
        "base_branch": row["base_branch"],
        "status": row["status"],
        "pr_url": row["pr_url"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def require_str_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a non-empty string list")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise ValueError(f"{key} must be a non-empty string list")
    return result


def detect_backend(db_path: str | Path) -> dict:
    value = str(db_path)
    if value.startswith(("postgresql://", "postgres://")):
        return {
            "type": "postgres",
            "dsn": value,
            "driver_available": postgres_driver_available(),
        }
    return {"type": "sqlite", "path": value, "driver_available": True}


def postgres_driver_available() -> bool:
    return importlib.util.find_spec("psycopg") is not None or importlib.util.find_spec("psycopg2") is not None


def postgres_not_ready(tool_name: str, backend: dict) -> tuple[int, dict]:
    if backend["driver_available"]:
        blocker_code = "POSTGRES_BACKEND_NOT_IMPLEMENTED"
        reason = "Postgres driver is available, but MCP-state Postgres SQL adapter is not implemented yet"
    else:
        blocker_code = "POSTGRES_DRIVER_MISSING"
        reason = "Postgres DSN was requested, but no psycopg/psycopg2 driver is installed"
    return 2, {
        "status": "blocked",
        "blocker_code": blocker_code,
        "state_backend": "postgres",
        "tool_name": tool_name,
        "reason": reason,
        "next_action": "install/configure a Postgres driver and implement the Postgres SQL adapter before using this backend",
    }


def call_postgres_tool(dsn: str, tool_name: str, payload: dict[str, Any]) -> tuple[int, dict]:
    if tool_name == "state_project_get":
        return postgres_project_get(dsn, require_str(payload, "project_id"))
    if tool_name == "state_lock_acquire":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_lock_acquire(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
            ttl_seconds=int(payload.get("ttl_seconds") or 900),
        )
    if tool_name == "state_lock_release":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_lock_release(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
        )
    if tool_name == "state_run_start":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_run_start(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            latest_ref=payload.get("latest_ref"),
        )
    return postgres_not_ready(tool_name, {"type": "postgres", "dsn": dsn, "driver_available": postgres_driver_available()})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
