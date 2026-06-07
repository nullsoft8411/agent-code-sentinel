from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .db import connect


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


@dataclass(frozen=True)
class ScanFindingInput:
    id: str
    project_id: str
    signature: str
    scanner_name: str
    rule_id: str
    severity: str
    title: str
    run_id: str | None = None
    scan_job_id: str | None = None
    file_path: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    status: str = "open"
    message: str | None = None
    suggestion: str | None = None
    evidence: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def create_scan_job(db_path: str, job: ScanJobInput) -> dict[str, Any]:
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
                _json(job.metadata),
            ),
        )
        conn.commit()
        return _row_to_dict(
            conn.execute("select * from scan_jobs where id = ?", (job.id,)).fetchone(),
            [column[1] for column in conn.execute("pragma table_info(scan_jobs)").fetchall()],
        )


def upsert_scan_finding(db_path: str, finding: ScanFindingInput) -> dict[str, Any]:
    with connect(db_path) as conn:
        existing = conn.execute(
            "select * from scan_findings where project_id = ? and signature = ?",
            (finding.project_id, finding.signature),
        ).fetchone()
        scan_columns = [column[1] for column in conn.execute("pragma table_info(scan_findings)").fetchall()]
        if existing:
            return {"created": False, "finding": _row_to_dict(existing, scan_columns)}

        compatibility_finding_id = f"compat-{finding.id}"
        conn.execute(
            """
            insert into findings(
              id, run_id, project_id, signature, category, severity,
              file_path, line_number, title, details, status
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compatibility_finding_id,
                _required_run_id(finding.run_id),
                finding.project_id,
                finding.signature,
                finding.rule_id,
                finding.severity,
                finding.file_path,
                finding.line_number,
                finding.title,
                finding.message,
                finding.status,
            ),
        )
        conn.execute(
            """
            insert into scan_findings(
              id, scan_job_id, run_id, project_id, compatibility_finding_id,
              scanner_name, rule_id, file_path, line_number, column_number,
              severity, status, title, message, suggestion, evidence,
              signature, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding.id,
                finding.scan_job_id,
                finding.run_id,
                finding.project_id,
                compatibility_finding_id,
                finding.scanner_name,
                finding.rule_id,
                finding.file_path,
                finding.line_number,
                finding.column_number,
                finding.severity,
                finding.status,
                finding.title,
                finding.message,
                finding.suggestion,
                finding.evidence,
                finding.signature,
                _json(finding.metadata),
            ),
        )
        _refresh_scan_job_count(conn, finding.scan_job_id)
        conn.commit()
        row = conn.execute("select * from scan_findings where id = ?", (finding.id,)).fetchone()
        return {"created": True, "finding": _row_to_dict(row, scan_columns)}


def list_scan_findings(db_path: str, *, project_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        columns = [column[1] for column in conn.execute("pragma table_info(scan_findings)").fetchall()]
        if run_id:
            rows = conn.execute(
                """
                select * from scan_findings
                where project_id = ? and run_id = ?
                order by file_path, line_number, title
                """,
                (project_id, run_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select * from scan_findings
                where project_id = ?
                order by file_path, line_number, title
                """,
                (project_id,),
            ).fetchall()
        return [_row_to_dict(row, columns) for row in rows]


def update_scan_finding_status(
    db_path: str,
    *,
    project_id: str,
    signature: str,
    status: str,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            select id, compatibility_finding_id from scan_findings
            where project_id = ? and signature = ?
            """,
            (project_id, signature),
        ).fetchone()
        if row is None:
            return {"status": "blocked", "blocker_code": "SCAN_FINDING_NOT_FOUND"}
        conn.execute(
            """
            update scan_findings
            set status = ?, updated_at = datetime('now')
            where project_id = ? and signature = ?
            """,
            (status, project_id, signature),
        )
        conn.execute(
            """
            update findings
            set status = ?
            where id = ?
            """,
            (status, row[1]),
        )
        conn.commit()
        return {"status": "passed", "scan_finding_id": row[0], "finding_status": status}


def _refresh_scan_job_count(conn: Any, scan_job_id: str | None) -> None:
    if not scan_job_id:
        return
    conn.execute(
        """
        update scan_jobs
        set findings_count = (
          select count(*) from scan_findings where scan_job_id = ?
        ),
        updated_at = datetime('now')
        where id = ?
        """,
        (scan_job_id, scan_job_id),
    )


def _required_run_id(run_id: str | None) -> str:
    if not run_id:
        raise ValueError("run_id is required for compatibility findings")
    return run_id


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    return dict(zip(columns, row, strict=True))
