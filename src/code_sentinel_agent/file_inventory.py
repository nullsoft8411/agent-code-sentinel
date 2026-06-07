from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import connect
from .db_rows import sqlite_row_to_dict, sqlite_table_columns
from .project_context import SOURCE_EXTENSIONS, SKIP_DIRS


@dataclass(frozen=True)
class FileCheckInput:
    id: str
    project_id: str
    file_path: str
    status: str
    scan_job_id: str | None = None
    run_id: str | None = None
    content_sha256: str | None = None
    language: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_file_inventory(project: str | Path, *, max_files: int = 200) -> tuple[int, dict[str, Any]]:
    root = Path(project).resolve()
    if not root.exists():
        return 2, {"status": "blocked", "blocker_code": "PROJECT_PATH_MISSING", "project": str(project)}
    if not root.is_dir():
        return 2, {"status": "blocked", "blocker_code": "PROJECT_PATH_NOT_DIRECTORY", "project": str(project)}

    files: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            skipped += 1
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            skipped += 1
            continue
        if len(files) >= max_files:
            skipped += 1
            continue
        files.append(
            {
                "path": str(relative),
                "bytes": path.stat().st_size,
                "content_sha256": sha256_file(path),
                "language": _language(path),
            }
        )
    return 0, {
        "status": "passed",
        "target_project": str(root),
        "max_files": max_files,
        "files": files,
        "skipped_count": skipped,
    }


def record_file_check(db_path: str, file_check: FileCheckInput) -> dict[str, Any]:
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into file_checks(
              id, scan_job_id, run_id, project_id, file_path, content_sha256,
              language, status, checks_json, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(project_id, scan_job_id, file_path) do update set
              content_sha256 = excluded.content_sha256,
              language = excluded.language,
              status = excluded.status,
              checks_json = excluded.checks_json,
              metadata_json = excluded.metadata_json,
              updated_at = datetime('now')
            """,
            (
                file_check.id,
                file_check.scan_job_id,
                file_check.run_id,
                file_check.project_id,
                file_check.file_path,
                file_check.content_sha256,
                file_check.language,
                file_check.status,
                json.dumps(file_check.checks, sort_keys=True),
                json.dumps(file_check.metadata, sort_keys=True),
            ),
        )
        conn.commit()
        return get_file_check(
            db_path,
            project_id=file_check.project_id,
            scan_job_id=file_check.scan_job_id,
            file_path=file_check.file_path,
        )


def list_file_checks(
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
        columns = sqlite_table_columns(conn, "file_checks")
        rows = conn.execute(
            f"""
            select * from file_checks
            where {' and '.join(clauses)}
            order by file_path
            """,
            tuple(values),
        ).fetchall()
    return [sqlite_row_to_dict(row, columns) for row in rows]


def get_file_check(db_path: str, *, project_id: str, scan_job_id: str | None, file_path: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "file_checks")
        row = conn.execute(
            """
            select * from file_checks
            where project_id = ? and scan_job_id is ? and file_path = ?
            """,
            (project_id, scan_job_id, file_path),
        ).fetchone()
    if row is None:
        return {"status": "blocked", "blocker_code": "FILE_CHECK_NOT_FOUND", "file_path": file_path}
    return sqlite_row_to_dict(row, columns)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _language(path: Path) -> str | None:
    return {
        ".go": "go",
        ".js": "javascript",
        ".jsx": "javascript",
        ".md": "markdown",
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescript",
    }.get(path.suffix)
