from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import connect
from .db_rows import json_object, sqlite_row_to_dict, sqlite_table_columns, validate_status

SCAN_JOB_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


@dataclass(frozen=True)
class ScanJobInput:
    id: str
    project_id: str
    run_id: str | None
    scanner_name: str
    scan_type: str
    status: str = "running"
    target_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def create_scan_job(db_path: str, job: ScanJobInput) -> dict[str, Any]:
    validate_status(job.status, SCAN_JOB_STATUSES, label="scan job")
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into scan_jobs(
              id, run_id, project_id, scanner_name, scan_type, status,
              target_ref, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.run_id,
                job.project_id,
                job.scanner_name,
                job.scan_type,
                job.status,
                job.target_ref,
                json_object(job.metadata),
            ),
        )
        conn.commit()
        return get_scan_job(str(db_path), job.id)


def update_scan_job_progress(
    db_path: str,
    *,
    scan_job_id: str,
    status: str | None = None,
    files_total: int | None = None,
    files_scanned: int | None = None,
    files_skipped: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    if status is not None:
        validate_status(status, SCAN_JOB_STATUSES, label="scan job")
    updates: list[str] = []
    values: list[Any] = []
    if status is not None:
        updates.append("status = ?")
        values.append(status)
        if status in {"completed", "failed", "cancelled"}:
            updates.append("completed_at = datetime('now')")
    for column, value in [
        ("files_total", files_total),
        ("files_scanned", files_scanned),
        ("files_skipped", files_skipped),
        ("error_message", error_message),
    ]:
        if value is not None:
            updates.append(f"{column} = ?")
            values.append(value)
    if not updates:
        return get_scan_job(db_path, scan_job_id)
    updates.append("updated_at = datetime('now')")
    values.append(scan_job_id)
    with connect(db_path) as conn:
        conn.execute(
            f"update scan_jobs set {', '.join(updates)} where id = ?",
            tuple(values),
        )
        conn.commit()
    return get_scan_job(db_path, scan_job_id)


def get_scan_job(db_path: str, scan_job_id: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "scan_jobs")
        row = conn.execute("select * from scan_jobs where id = ?", (scan_job_id,)).fetchone()
    if row is None:
        return {"status": "blocked", "blocker_code": "SCAN_JOB_NOT_FOUND", "scan_job_id": scan_job_id}
    return sqlite_row_to_dict(row, columns)


def list_scan_jobs(db_path: str, *, project_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "scan_jobs")
        if run_id:
            rows = conn.execute(
                """
                select * from scan_jobs
                where project_id = ? and run_id = ?
                order by created_at, id
                """,
                (project_id, run_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select * from scan_jobs
                where project_id = ?
                order by created_at, id
                """,
                (project_id,),
            ).fetchall()
    return [sqlite_row_to_dict(row, columns) for row in rows]
