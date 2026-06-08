from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .approvals import approval_check
from .audit_events import AuditEventInput, append_audit_event
from .db import connect
from .execution_sessions import ExecutionSessionInput, record_execution_session
from .file_inventory import FileCheckInput, build_file_inventory, record_file_check
from .improvement_work import prepare_improvement_work_package
from .plugin_executions import PluginExecutionInput, record_plugin_execution
from .project_context import project_context
from .qg_workflow import process_quality_gate_payload, selected_task_for_agent_takeover
from .reports import report
from .scan_jobs import ScanJobInput, create_scan_job, update_scan_job_progress
from .task_execution import apply_task_execution_result

DEFAULT_CYCLE_OWNER = "workspace-agent-cycle"


def resume_cycle(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str,
) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "select id, target, default_branch from projects where id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            return 2, {
                "status": "blocked",
                "blocker_code": "PROJECT_NOT_FOUND",
                "project_id": project_id,
                "next_action": "initialize project state before resuming an autonomous cycle",
            }

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
        previous_run = conn.execute(
            """
            select id, status, current_focus, next_autonomous_step
            from runs
            where project_id = ? and id != ?
            order by created_at desc
            limit 1
            """,
            (project_id, run_id),
        ).fetchone()

        loaded_memory = json.loads(memory["memory_json"]) if memory else None
        prior_step = next_step_from_memory(loaded_memory)
        next_autonomous_step = f"preflight {latest_ref} before continuing: {prior_step}"

        existing = conn.execute(
            "select id, status, current_focus, next_autonomous_step from runs where id = ?",
            (run_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                insert into runs(id, project_id, status, current_focus, next_autonomous_step)
                values (?, ?, ?, ?, ?)
                """,
                (run_id, project_id, "in_progress", "autonomous_cycle", next_autonomous_step),
            )
            conn.commit()
            run = conn.execute(
                "select id, status, current_focus, next_autonomous_step from runs where id = ?",
                (run_id,),
            ).fetchone()
        else:
            run = existing

    return 0, {
        "status": "passed",
        "project_id": project_id,
        "project": {
            "id": project["id"],
            "target": project["target"],
            "default_branch": project["default_branch"],
        },
        "previous_run": row_to_dict(previous_run),
        "loaded_memory": loaded_memory,
        "repository_delta": "repository truth must be checked before trusting memory",
        "stale_memory_decision": memory["stale_memory_decision"] if memory else "not_applicable",
        "latest_ref": latest_ref,
        "run": row_to_dict(run),
        "next_autonomous_step": run["next_autonomous_step"],
    }


def run_autonomous_cycle(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    project_id = _required_cycle_value(payload, "project_id")
    run_id = _required_cycle_value(payload, "run_id")
    latest_ref = str(payload.get("latest_ref") or "current ref")
    owner = str(payload.get("owner") or DEFAULT_CYCLE_OWNER)

    lock_code, lock_payload = _acquire_cycle_lock(db_path, project_id=project_id, run_id=run_id, owner=owner)
    if lock_code != 0:
        return lock_code, lock_payload

    try:
        start_code, start_payload = resume_cycle(
            db_path,
            project_id=project_id,
            run_id=run_id,
            latest_ref=latest_ref,
        )
        if start_code != 0:
            return start_code, start_payload

        approval_result = None
        write_request = payload.get("write_request") if isinstance(payload.get("write_request"), dict) else None
        if write_request:
            approval_code, approval_result = approval_check(
                db_path,
                run_id=run_id,
                target_project=_required_cycle_value(write_request, "target_project"),
                branch=_required_cycle_value(write_request, "branch"),
                path=_required_cycle_value(write_request, "path"),
                action=_required_cycle_value(write_request, "action"),
            )
            if approval_code != 0:
                _update_run_blocker(
                    db_path,
                    run_id=run_id,
                    blocker=approval_result["blocker_code"],
                    next_step=approval_result["next_action"],
                )
                return approval_code, {
                    "status": "blocked",
                    "project_id": project_id,
                    "run_id": run_id,
                    "current_focus": "approval",
                    "approval": approval_result,
                    "next_autonomous_step": approval_result["next_action"],
                    "lock_released": True,
                }

        validation_payload = payload.get("validation_result") if isinstance(payload.get("validation_result"), dict) else None
        validation_result = None
        if validation_payload:
            validation_payload = {**validation_payload, "project_id": project_id, "run_id": run_id}
            validation_code, validation_result = process_quality_gate_payload(db_path, validation_payload)
            if validation_code != 0 and validation_result.get("status") != "blocking":
                return validation_code, validation_result

        project_evidence = _collect_project_evidence(
            db_path,
            payload,
            project_id=project_id,
            run_id=run_id,
            latest_ref=latest_ref,
        )

        selected_task = selected_task_for_agent_takeover(db_path, project_id=project_id, run_id=run_id)
        work_package_code, improvement_work_package = prepare_improvement_work_package(
            str(db_path),
            project_id=project_id,
            run_id=run_id,
            selected_task=selected_task,
            project_path=str(payload.get("project_path") or "").strip() or None,
        )
        if work_package_code != 0:
            return work_package_code, improvement_work_package or {
                "status": "blocked",
                "blocker_code": "IMPROVEMENT_WORK_PACKAGE_FAILED",
            }
        if improvement_work_package:
            selected_task = improvement_work_package["task"]

        task_execution_payload = (
            payload.get("task_execution_result")
            if isinstance(payload.get("task_execution_result"), dict)
            else None
        )
        task_execution_result = None
        if task_execution_payload:
            task_execution_code, task_execution_result = apply_task_execution_result(
                db_path,
                project_id=project_id,
                run_id=run_id,
                execution_result=task_execution_payload,
                approval_result=approval_result,
            )
            if task_execution_code != 0 and task_execution_result.get("status") != "blocking":
                _update_run_blocker(
                    db_path,
                    run_id=run_id,
                    blocker=task_execution_result["blocker_code"],
                    next_step=task_execution_result["next_action"],
                )
                return task_execution_code, {
                    "status": "blocked",
                    "project_id": project_id,
                    "run_id": run_id,
                    "current_focus": "task_execution",
                    "task_execution_result": task_execution_result,
                    "next_autonomous_step": task_execution_result["next_action"],
                    "lock_released": True,
                }
            selected_task = selected_task_for_agent_takeover(db_path, project_id=project_id, run_id=run_id)
        if selected_task:
            _update_run_next_step(
                db_path,
                run_id=run_id,
                current_focus="task_takeover",
                next_step=(
                    improvement_work_package["next_autonomous_step"]
                    if improvement_work_package
                    else f"take over {selected_task['task_type']} {selected_task['id']}: {selected_task['title']}"
                ),
            )
        else:
            _update_run_next_step(
                db_path,
                run_id=run_id,
                current_focus="analysis",
                next_step="analyze project context and create findings/tasks before editing",
            )

        cycle_session = record_execution_session(
            db_path,
            ExecutionSessionInput(
                id=f"cycle-{run_id}",
                run_id=run_id,
                project_id=project_id,
                task_id=selected_task["id"] if selected_task else None,
                session_type="autonomous_cycle",
                script_name="run-cycle",
                execution_method="agent_runtime_cli",
                command="cs-agent run-cycle",
                status=_cycle_status(project_evidence, validation_result, task_execution_result),
                output={
                    "latest_ref": latest_ref,
                    "selected_task_id": selected_task["id"] if selected_task else None,
                    "validation_status": validation_result.get("status") if validation_result else None,
                    "task_execution_status": (
                        task_execution_result.get("status") if task_execution_result else None
                    ),
                    "project_evidence_status": project_evidence["status"],
                    "scan_job_id": project_evidence.get("scan_job", {}).get("id"),
                    "file_checks_count": project_evidence.get("file_checks_count", 0),
                    "improvement_work_package_status": (
                        improvement_work_package.get("status") if improvement_work_package else None
                    ),
                },
                files_modified=[],
                attempt_log=[
                    {"step": "lock_acquired", "status": "passed"},
                    {"step": "run_state_loaded", "status": "passed"},
                    {"step": "project_evidence", "status": project_evidence["status"]},
                    {
                        "step": "improvement_work_package",
                        "status": improvement_work_package["status"] if improvement_work_package else "not_available",
                    },
                    {
                        "step": "task_execution_result",
                        "status": task_execution_result["status"] if task_execution_result else "not_available",
                    },
                    {"step": "selected_task", "status": "passed" if selected_task else "not_available"},
                ],
                completed_at=_utc_now(),
            ),
        )
        status = _cycle_status(project_evidence, validation_result, task_execution_result)
        audit_event = append_audit_event(
            str(db_path),
            AuditEventInput(
                id=f"audit-cycle-{run_id}",
                project_id=project_id,
                run_id=run_id,
                event_type="autonomous_cycle",
                summary=f"Cycle exited with status {status}",
                payload={
                    "latest_ref": latest_ref,
                    "selected_task_id": selected_task["id"] if selected_task else None,
                    "current_focus": "task_takeover" if selected_task else "analysis",
                    "validation_status": validation_result.get("status") if validation_result else None,
                    "task_execution_status": (
                        task_execution_result.get("status") if task_execution_result else None
                    ),
                    "project_evidence_status": project_evidence["status"],
                    "scan_job_id": project_evidence.get("scan_job", {}).get("id"),
                    "file_checks_count": project_evidence.get("file_checks_count", 0),
                    "improvement_work_package_status": (
                        improvement_work_package.get("status") if improvement_work_package else None
                    ),
                    "plugin_execution_ids": [
                        item["id"] for item in project_evidence.get("plugin_executions", [])
                    ],
                },
            ),
        )
        report_code, report_payload = report(db_path, run_id)
        if report_code != 0:
            return report_code, report_payload
        return (2 if status == "blocking" else 0), {
            "status": status,
            "blocker_code": project_evidence.get("blocker_code") if status == "blocking" else None,
            "project_id": project_id,
            "run_id": run_id,
            "latest_ref": latest_ref,
            "loaded_memory": start_payload["loaded_memory"],
            "stale_memory_decision": start_payload["stale_memory_decision"],
            "repository_delta": start_payload["repository_delta"],
            "selected_task_for_agent_takeover": selected_task,
            "validation_result": validation_result,
            "task_execution_result": task_execution_result,
            "project_evidence": project_evidence,
            "improvement_work_package": improvement_work_package,
            "cycle_session": cycle_session,
            "audit_event": audit_event,
            "report": report_payload,
            "next_autonomous_step": report_payload["run"]["next_autonomous_step"],
            "lock_released": True,
        }
    finally:
        _release_cycle_lock(db_path, project_id=project_id, run_id=run_id, owner=owner)


def next_step_from_memory(loaded_memory: dict | None) -> str:
    if not loaded_memory:
        return "run repository preflight"
    value = loaded_memory.get("next_autonomous_step")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "run repository preflight"


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _cycle_status(
    project_evidence: dict[str, Any],
    validation_result: dict[str, Any] | None,
    task_execution_result: dict[str, Any] | None,
) -> str:
    if project_evidence.get("status") == "blocking":
        return "blocking"
    if validation_result and validation_result.get("status") == "blocking":
        return "blocking"
    if task_execution_result and task_execution_result.get("status") == "blocking":
        return "blocking"
    return "passed"


def _collect_project_evidence(
    db_path: str | Path,
    payload: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    latest_ref: str,
) -> dict[str, Any]:
    project_path = str(payload.get("project_path") or "").strip()
    if not project_path:
        return {"status": "not_requested", "project_path": None, "file_checks_count": 0}

    db_path_text = str(db_path)
    scan_job = _ensure_cycle_scan_job(
        db_path_text,
        project_id=project_id,
        run_id=run_id,
        latest_ref=latest_ref,
        project_path=project_path,
    )
    context_code, context_payload = project_context(project_path)
    context_execution = record_plugin_execution(
        db_path_text,
        PluginExecutionInput(
            id=_stable_id("plugin", run_id, "project_context"),
            project_id=project_id,
            run_id=run_id,
            scan_job_id=scan_job["id"],
            plugin_name="project_context",
            status="passed" if context_code == 0 else "blocking",
            exit_code=context_code,
            stdout_summary=context_payload.get("status"),
            stderr_summary=context_payload.get("blocker_code"),
            metadata={"source": "autonomous_cycle"},
        ),
    )

    inventory_code, inventory_payload = build_file_inventory(project_path)
    inventory_execution = record_plugin_execution(
        db_path_text,
        PluginExecutionInput(
            id=_stable_id("plugin", run_id, "file_inventory"),
            project_id=project_id,
            run_id=run_id,
            scan_job_id=scan_job["id"],
            plugin_name="file_inventory",
            status="passed" if inventory_code == 0 else "blocking",
            exit_code=inventory_code,
            stdout_summary=inventory_payload.get("status"),
            stderr_summary=inventory_payload.get("blocker_code"),
            metadata={"source": "autonomous_cycle"},
        ),
    )

    if context_code != 0 or inventory_code != 0:
        blocker = context_payload.get("blocker_code") or inventory_payload.get("blocker_code") or "PROJECT_EVIDENCE_BLOCKED"
        scan_job = update_scan_job_progress(
            db_path_text,
            scan_job_id=scan_job["id"],
            status="failed",
            error_message=str(blocker),
        )
        return {
            "status": "blocking",
            "blocker_code": blocker,
            "project_path": project_path,
            "project_context": context_payload,
            "file_inventory": inventory_payload,
            "scan_job": scan_job,
            "plugin_executions": [context_execution, inventory_execution],
            "file_checks_count": 0,
        }

    file_checks_count = 0
    for file_record in inventory_payload["files"]:
        record_file_check(
            db_path_text,
            FileCheckInput(
                id=_stable_id("file-check", run_id, scan_job["id"], file_record["path"]),
                project_id=project_id,
                run_id=run_id,
                scan_job_id=scan_job["id"],
                file_path=file_record["path"],
                content_sha256=file_record.get("content_sha256"),
                language=file_record.get("language"),
                status="passed",
                checks=[{"name": "inventory", "status": "passed"}],
                metadata={"bytes": file_record.get("bytes"), "source": "autonomous_cycle"},
            ),
        )
        file_checks_count += 1

    scan_job = update_scan_job_progress(
        db_path_text,
        scan_job_id=scan_job["id"],
        status="completed",
        files_total=file_checks_count + int(inventory_payload.get("skipped_count") or 0),
        files_scanned=file_checks_count,
        files_skipped=int(inventory_payload.get("skipped_count") or 0),
    )
    return {
        "status": "passed",
        "project_path": project_path,
        "project_context": {
            "context_summary": context_payload["context_summary"],
            "project_rules": context_payload["project_rules"],
            "check_detection": context_payload["check_detection"],
            "git_truth": context_payload["git_truth"],
            "next_autonomous_step": context_payload["next_autonomous_step"],
        },
        "file_inventory": {
            "status": inventory_payload["status"],
            "files_count": file_checks_count,
            "skipped_count": inventory_payload["skipped_count"],
        },
        "scan_job": scan_job,
        "plugin_executions": [context_execution, inventory_execution],
        "file_checks_count": file_checks_count,
    }


def _ensure_cycle_scan_job(
    db_path: str,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str,
    project_path: str,
) -> dict[str, Any]:
    scan_job_id = _stable_id("scan", run_id, project_path)
    try:
        return create_scan_job(
            db_path,
            ScanJobInput(
                id=scan_job_id,
                project_id=project_id,
                run_id=run_id,
                scanner_name="agent_autonomous_cycle",
                scan_type="project_context",
                status="running",
                target_ref=latest_ref,
                metadata={"project_path": project_path, "source": "run-cycle"},
            ),
        )
    except sqlite3.IntegrityError:
        return update_scan_job_progress(db_path, scan_job_id=scan_job_id, status="running")


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _acquire_cycle_lock(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str,
) -> tuple[int, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)
    lock_id = f"{project_id}:{run_id}:{owner}"
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _expire_locks(conn, now)
        lock = _active_lock(conn, project_id)
        if lock and (lock["run_id"] != run_id or lock["owner"] != owner):
            return 2, {
                "status": "blocked",
                "blocker_code": "STATE_LOCK_HELD",
                "project_id": project_id,
                "active_lock": row_to_dict(lock),
                "next_action": "retry after the active lock is released or expires",
            }
        if not lock:
            conn.execute(
                """
                insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at)
                values (?, ?, ?, ?, 'active', ?, ?)
                """,
                (lock_id, project_id, run_id, owner, _format_dt(now), _format_dt(expires_at)),
            )
            conn.commit()
    return 0, {"status": "passed", "lock_acquired": True, "project_id": project_id, "run_id": run_id}


def _release_cycle_lock(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str,
) -> None:
    now = _format_dt(datetime.now(timezone.utc))
    with connect(db_path) as conn:
        conn.execute(
            """
            update state_locks
            set status = 'released', released_at = ?, updated_at = ?
            where project_id = ? and run_id = ? and owner = ?
              and status = 'active' and released_at is null
            """,
            (now, now, project_id, run_id, owner),
        )
        conn.commit()


def _active_lock(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
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


def _expire_locks(conn: sqlite3.Connection, now: datetime) -> None:
    now_text = _format_dt(now)
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


def _update_run_blocker(db_path: str | Path, *, run_id: str, blocker: str, next_step: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            update runs
            set current_focus = 'approval', blocker = ?, next_autonomous_step = ?, updated_at = datetime('now')
            where id = ?
            """,
            (blocker, next_step, run_id),
        )
        conn.commit()


def _update_run_next_step(db_path: str | Path, *, run_id: str, current_focus: str, next_step: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            update runs
            set current_focus = ?, next_autonomous_step = ?, updated_at = datetime('now')
            where id = ?
            """,
            (current_focus, next_step, run_id),
        )
        conn.commit()


def _required_cycle_value(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
