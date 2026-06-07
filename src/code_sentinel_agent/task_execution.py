from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .execution_sessions import ExecutionSessionInput, record_execution_session
from .qg_workflow import process_quality_gate_payload
from .task_workflow import update_task_status, workflow_task_payload


def apply_task_execution_result(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    execution_result: dict[str, Any],
    approval_result: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    task_id = _required_execution_value(execution_result, "task_id")
    code, task_payload = _load_task(db_path, project_id=project_id, run_id=run_id, task_id=task_id)
    if code != 0:
        return code, task_payload

    files_modified = _string_list(execution_result.get("files_modified"))
    if files_modified:
        code, approval_payload = _check_file_write_approval(files_modified, approval_result)
        if code != 0:
            return code, approval_payload

    validation_payload = execution_result.get("validation_result")
    if not isinstance(validation_payload, dict):
        return 2, {
            "status": "blocked",
            "blocker_code": "TASK_VALIDATION_RESULT_REQUIRED",
            "reason": "task_execution_result.validation_result is required",
            "next_action": "return task_execution_result.validation_result with command and exit_code",
        }

    validation_input = {
        **validation_payload,
        "project_id": project_id,
        "run_id": run_id,
        "gate": str(validation_payload.get("gate") or "task_execution_validation"),
    }
    validation_code, validation_result = process_quality_gate_payload(db_path, validation_input)
    if validation_code != 0 and validation_result.get("status") != "blocking":
        return validation_code, validation_result

    validation_status = validation_result["validation"]["status"]
    task_status = "completed" if validation_status == "passed" else "failed_validation"
    task_code, updated_task = update_task_status(
        str(db_path),
        task_id=task_id,
        status=task_status,
        increment_attempt=validation_status == "blocking",
    )
    if task_code != 0:
        return task_code, updated_task

    execution_method = str(execution_result.get("execution_method") or "agent_native_task_execution")
    command = str(validation_payload.get("command") or validation_result["validation"]["command"])
    session = record_execution_session(
        db_path,
        ExecutionSessionInput(
            id=_task_execution_session_id("task-exec", run_id, task_id, command, validation_status),
            run_id=run_id,
            project_id=project_id,
            task_id=task_id,
            session_type="task_execution",
            script_name=str(execution_result.get("script_name") or "agent-task-execution"),
            execution_method=execution_method,
            command=command,
            status=validation_status,
            output={
                "task_id": task_id,
                "validation": validation_result["validation"],
                "files_modified": files_modified,
                "approval_enforced": bool(files_modified),
            },
            error_summary=(
                validation_result["validation"].get("evidence")
                if validation_status == "blocking"
                else None
            ),
            files_modified=files_modified,
            attempt_log=[
                {"step": "task_loaded", "status": "passed"},
                {
                    "step": "write_approval",
                    "status": "passed" if files_modified else "not_required",
                },
                {"step": "validation_result", "status": validation_status},
                {"step": "task_status_update", "status": task_status},
            ],
            completed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )

    status_code = 2 if validation_status == "blocking" else 0
    return status_code, {
        "status": validation_status,
        "project_id": project_id,
        "run_id": run_id,
        "task": updated_task["task"],
        "validation_result": validation_result,
        "execution_session": session,
        "files_modified": files_modified,
        "approval_enforced": bool(files_modified),
        "next_action": (
            "continue task remediation with a fresh bounded fix plan"
            if validation_status == "blocking"
            else "refresh report and continue with the next runnable task"
        ),
    }


def _load_task(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    task_id: str,
) -> tuple[int, dict[str, Any]]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    if row is None:
        return 2, {
            "status": "blocked",
            "blocker_code": "TASK_NOT_FOUND",
            "task_id": task_id,
            "next_action": "select an existing task for the current project run before recording execution",
        }
    if row["project_id"] != project_id or row["run_id"] != run_id:
        return 2, {
            "status": "blocked",
            "blocker_code": "TASK_SCOPE_MISMATCH",
            "task_id": task_id,
            "task_project_id": row["project_id"],
            "task_run_id": row["run_id"],
            "project_id": project_id,
            "run_id": run_id,
            "next_action": "record execution only for a task belonging to the current project run",
        }
    return 0, {"status": "passed", "task": workflow_task_payload(row)}


def _check_file_write_approval(
    files_modified: list[str],
    approval_result: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    if not approval_result or not approval_result.get("write_allowed"):
        return 2, {
            "status": "blocked",
            "blocker_code": "WRITE_APPROVAL_REQUIRED_FOR_TASK_RESULT",
            "reason": "task execution reported file changes without an approved write_request",
            "files_modified": files_modified,
            "next_action": "provide write_request approval for every modified file before recording the task result",
        }
    approval = approval_result.get("approval") if isinstance(approval_result.get("approval"), dict) else {}
    allowed_paths = set(_string_list(approval.get("allowed_paths")))
    allowed_actions = set(_string_list(approval.get("allowed_actions")))
    if "file_write" not in allowed_actions:
        return 2, {
            "status": "blocked",
            "blocker_code": "WRITE_ACTION_NOT_APPROVED_FOR_TASK_RESULT",
            "allowed_actions": sorted(allowed_actions),
            "required_action": "file_write",
        }
    denied = [path for path in files_modified if path not in allowed_paths]
    if denied:
        return 2, {
            "status": "blocked",
            "blocker_code": "WRITE_PATH_NOT_APPROVED_FOR_TASK_RESULT",
            "denied_paths": denied,
            "allowed_paths": sorted(allowed_paths),
        }
    return 0, {"status": "passed", "write_allowed": True}


def _required_execution_value(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_execution_session_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"
