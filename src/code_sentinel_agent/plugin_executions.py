from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .db import connect
from .db_rows import sqlite_row_to_dict, sqlite_table_columns, validate_status

PLUGIN_EXECUTION_STATUSES = {"pending", "running", "passed", "blocking", "failed", "skipped"}


@dataclass(frozen=True)
class PluginExecutionInput:
    id: str
    project_id: str
    plugin_name: str
    status: str
    scan_job_id: str | None = None
    run_id: str | None = None
    exit_code: int | None = None
    stdout_summary: str | None = None
    stderr_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def record_plugin_execution(db_path: str, execution: PluginExecutionInput) -> dict[str, Any]:
    validate_status(execution.status, PLUGIN_EXECUTION_STATUSES, label="plugin execution")
    completed_at = "datetime('now')" if execution.status in {"passed", "blocking", "failed", "skipped"} else "null"
    with connect(db_path) as conn:
        conn.execute(
            f"""
            insert into plugin_executions(
              id, scan_job_id, run_id, project_id, plugin_name, status,
              completed_at, exit_code, stdout_summary, stderr_summary, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, {completed_at}, ?, ?, ?, ?)
            on conflict(id) do update set
              status = excluded.status,
              completed_at = excluded.completed_at,
              exit_code = excluded.exit_code,
              stdout_summary = excluded.stdout_summary,
              stderr_summary = excluded.stderr_summary,
              metadata_json = excluded.metadata_json,
              updated_at = datetime('now')
            """,
            (
                execution.id,
                execution.scan_job_id,
                execution.run_id,
                execution.project_id,
                execution.plugin_name,
                execution.status,
                execution.exit_code,
                execution.stdout_summary,
                execution.stderr_summary,
                json.dumps(execution.metadata, sort_keys=True),
            ),
        )
        conn.commit()
        return get_plugin_execution(db_path, execution.id)


def list_plugin_executions(
    db_path: str,
    *,
    project_id: str,
    run_id: str | None = None,
    scan_job_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["project_id = ?"]
    values: list[Any] = [project_id]
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    if scan_job_id:
        clauses.append("scan_job_id = ?")
        values.append(scan_job_id)
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "plugin_executions")
        rows = conn.execute(
            f"""
            select * from plugin_executions
            where {' and '.join(clauses)}
            order by started_at, id
            """,
            tuple(values),
        ).fetchall()
    return [sqlite_row_to_dict(row, columns) for row in rows]


def get_plugin_execution(db_path: str, execution_id: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "plugin_executions")
        row = conn.execute("select * from plugin_executions where id = ?", (execution_id,)).fetchone()
    if row is None:
        return {"status": "blocked", "blocker_code": "PLUGIN_EXECUTION_NOT_FOUND", "execution_id": execution_id}
    return sqlite_row_to_dict(row, columns)
