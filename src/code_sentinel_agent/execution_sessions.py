from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import connect
from .output_contract import sanitize_text


@dataclass(frozen=True)
class ExecutionSessionInput:
    id: str
    run_id: str
    project_id: str
    session_type: str
    script_name: str
    execution_method: str
    command: str
    status: str
    task_id: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error_summary: str | None = None
    files_modified: list[str] = field(default_factory=list)
    attempt_log: list[dict[str, Any]] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


def record_execution_session(db_path: str | Path, session: ExecutionSessionInput) -> dict[str, Any]:
    command, command_redacted = sanitize_text(session.command)
    output_json, output_redacted = _sanitized_json(session.output)
    error_summary, error_redacted = sanitize_text(session.error_summary)
    files_modified_json, files_redacted = _sanitized_json(session.files_modified)
    attempt_log_json, attempt_redacted = _sanitized_json(session.attempt_log)
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            insert into agent_execution_sessions(
              id, run_id, project_id, task_id, session_type, script_name,
              execution_method, command, status, output_json, error_summary,
              files_modified_json, attempt_log_json, started_at, completed_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, coalesce(?, datetime('now')), ?)
            on conflict(id) do update set
              task_id = excluded.task_id,
              status = excluded.status,
              output_json = excluded.output_json,
              error_summary = excluded.error_summary,
              files_modified_json = excluded.files_modified_json,
              attempt_log_json = excluded.attempt_log_json,
              completed_at = excluded.completed_at,
              updated_at = datetime('now')
            """,
            (
                session.id,
                session.run_id,
                session.project_id,
                session.task_id,
                session.session_type,
                session.script_name,
                session.execution_method,
                command,
                session.status,
                output_json,
                error_summary or None,
                files_modified_json,
                attempt_log_json,
                session.started_at,
                session.completed_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "select * from agent_execution_sessions where id = ?",
            (session.id,),
        ).fetchone()
    payload = _session_payload(row)
    payload["secret_redaction_applied"] = (
        command_redacted or output_redacted or error_redacted or files_redacted or attempt_redacted
    )
    return payload


def list_execution_sessions(
    db_path: str | Path,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select * from agent_execution_sessions
            where run_id = ?
            order by started_at, id
            """,
            (run_id,),
        ).fetchall()
        return [_session_payload(row) for row in rows]


def parse_execution_session_payload(payload: dict[str, Any]) -> ExecutionSessionInput:
    return ExecutionSessionInput(
        id=_required_session_value(payload, "id"),
        run_id=_required_session_value(payload, "run_id"),
        project_id=_required_session_value(payload, "project_id"),
        task_id=_optional(payload, "task_id"),
        session_type=_required_session_value(payload, "session_type"),
        script_name=_required_session_value(payload, "script_name"),
        execution_method=_required_session_value(payload, "execution_method"),
        command=_required_session_value(payload, "command"),
        status=_required_session_value(payload, "status"),
        output=payload.get("output") if isinstance(payload.get("output"), dict) else {},
        error_summary=_optional(payload, "error_summary"),
        files_modified=_list_of_strings(payload.get("files_modified")),
        attempt_log=payload.get("attempt_log") if isinstance(payload.get("attempt_log"), list) else [],
        started_at=_optional(payload, "started_at"),
        completed_at=_optional(payload, "completed_at"),
    )


def _session_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "session_type": row["session_type"],
        "script_name": row["script_name"],
        "execution_method": row["execution_method"],
        "command": row["command"],
        "status": row["status"],
        "output": json.loads(row["output_json"] or "{}"),
        "error_summary": row["error_summary"],
        "files_modified": json.loads(row["files_modified_json"] or "[]"),
        "attempt_log": json.loads(row["attempt_log_json"] or "[]"),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def _sanitized_json(value: Any) -> tuple[str, bool]:
    sanitized, redacted = _sanitize_value(value)
    return json.dumps(sanitized, sort_keys=True), redacted


def _sanitize_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        redacted = False
        sanitized_items = []
        for item in value:
            sanitized_item, item_redacted = _sanitize_value(item)
            redacted = redacted or item_redacted
            sanitized_items.append(sanitized_item)
        return sanitized_items, redacted
    if isinstance(value, dict):
        redacted = False
        sanitized_dict: dict[str, Any] = {}
        for key, item in value.items():
            safe_key, key_redacted = sanitize_text(key)
            sanitized_item, item_redacted = _sanitize_value(item)
            redacted = redacted or key_redacted or item_redacted
            sanitized_dict[safe_key] = sanitized_item
        return sanitized_dict, redacted
    return value, False


def _required_session_value(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return str(value).strip() if value else None


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
