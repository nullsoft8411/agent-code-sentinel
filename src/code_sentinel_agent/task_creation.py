from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .db import connect
from .task_workflow import progress_json, refresh_parent_progress, update_task_status, workflow_task_payload

SEVERITY_PRIORITY = {
    "critical": 40,
    "blocking": 40,
    "high": 30,
    "medium": 20,
    "low": 10,
    "info": 0,
}


@dataclass(frozen=True)
class TaskCreationResult:
    status: str
    project_id: str
    run_id: str
    created_tasks: list[dict[str, Any]]
    skipped_duplicates: int
    grouped_files: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "created_tasks": self.created_tasks,
            "counts": {
                "created_tasks": len(self.created_tasks),
                "skipped_duplicates": self.skipped_duplicates,
                "grouped_files": self.grouped_files,
            },
        }


def create_tasks_from_findings(
    db_path: str,
    *,
    project_id: str,
    run_id: str,
    max_subtasks_per_parent: int = 20,
) -> tuple[int, dict[str, Any]]:
    if max_subtasks_per_parent < 1:
        return 2, {
            "status": "blocked",
            "blocker_code": "INVALID_MAX_SUBTASKS",
            "reason": "max_subtasks_per_parent must be >= 1",
        }

    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        findings = _open_findings(conn, project_id=project_id, run_id=run_id)
        grouped = _group_by_file(findings)
        too_large = {
            file_path: len(items)
            for file_path, items in grouped.items()
            if len(items) > max_subtasks_per_parent
        }
        if too_large:
            return 2, {
                "status": "blocked",
                "blocker_code": "MAX_SUBTASKS_EXCEEDED",
                "max_subtasks_per_parent": max_subtasks_per_parent,
                "groups": too_large,
            }

        created: list[dict[str, Any]] = []
        skipped_duplicates = 0
        for file_path, file_findings in grouped.items():
            if len(file_findings) == 1:
                task, duplicate = _create_standalone_task(conn, file_findings[0])
                skipped_duplicates += int(duplicate)
                if task:
                    created.append(task)
                continue
            parent, duplicate = _create_parent_task(conn, project_id, run_id, file_path, file_findings)
            skipped_duplicates += int(duplicate)
            if parent:
                created.append(parent)
            for order, finding in enumerate(file_findings, start=1):
                subtask, duplicate = _create_subtask(conn, finding, parent_task_id=_parent_task_id(file_path, project_id, run_id), order=order)
                skipped_duplicates += int(duplicate)
                if subtask:
                    created.append(subtask)
            refresh_parent_progress(conn, _parent_task_id(file_path, project_id, run_id))
        conn.commit()

    result = TaskCreationResult(
        status="passed",
        project_id=project_id,
        run_id=run_id,
        created_tasks=created,
        skipped_duplicates=skipped_duplicates,
        grouped_files=len(grouped),
    )
    return 0, result.as_payload()


def _open_findings(conn: sqlite3.Connection, *, project_id: str, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select * from findings
        where project_id = ? and run_id = ? and status = 'open'
        order by coalesce(file_path, 'project'), line_number, severity desc, title
        """,
        (project_id, run_id),
    ).fetchall()


def _group_by_file(findings: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for finding in findings:
        grouped[_safe_file_path(finding["file_path"])].append(finding)
    return dict(grouped)


def _create_standalone_task(conn: sqlite3.Connection, finding: sqlite3.Row) -> tuple[dict[str, Any] | None, bool]:
    task_signature = f"standalone:{finding['signature']}"
    if _task_exists(conn, finding["project_id"], task_signature):
        return None, True
    task_id = _task_id(task_signature)
    conn.execute(
        """
        insert into tasks(
          id, finding_id, run_id, project_id, status, priority, title,
          affected_file, task_type, task_signature, progress_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            finding["id"],
            finding["run_id"],
            finding["project_id"],
            "pending",
            _priority(finding["severity"]),
            f"Fix {finding['title']}",
            finding["file_path"],
            "standalone",
            task_signature,
            "{}",
        ),
    )
    conn.execute("update findings set status = 'task_created' where id = ?", (finding["id"],))
    return _task_by_id(conn, task_id), False


def _create_parent_task(
    conn: sqlite3.Connection,
    project_id: str,
    run_id: str,
    file_path: str,
    findings: list[sqlite3.Row],
) -> tuple[dict[str, Any] | None, bool]:
    task_signature = f"parent:{project_id}:{run_id}:{file_path}"
    if _task_exists(conn, project_id, task_signature):
        return None, True
    task_id = _parent_task_id(file_path, project_id, run_id)
    conn.execute(
        """
        insert into tasks(
          id, run_id, project_id, status, priority, title, affected_file,
          task_type, task_signature, progress_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            run_id,
            project_id,
            "pending",
            max(_priority(finding["severity"]) for finding in findings),
            f"Fix {len(findings)} findings in {file_path}",
            file_path,
            "parent",
            task_signature,
            progress_json(total=len(findings), completed=0, blocked=0),
        ),
    )
    return _task_by_id(conn, task_id), False


def _create_subtask(
    conn: sqlite3.Connection,
    finding: sqlite3.Row,
    *,
    parent_task_id: str,
    order: int,
) -> tuple[dict[str, Any] | None, bool]:
    task_signature = f"subtask:{finding['signature']}"
    if _task_exists(conn, finding["project_id"], task_signature):
        return None, True
    task_id = _task_id(task_signature)
    conn.execute(
        """
        insert into tasks(
          id, finding_id, run_id, project_id, parent_task_id, status, priority,
          title, affected_file, task_type, task_signature, subtask_order,
          progress_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            finding["id"],
            finding["run_id"],
            finding["project_id"],
            parent_task_id,
            "pending",
            _priority(finding["severity"]),
            f"Fix {finding['title']}",
            finding["file_path"],
            "subtask",
            task_signature,
            order,
            "{}",
        ),
    )
    conn.execute("update findings set status = 'task_created' where id = ?", (finding["id"],))
    return _task_by_id(conn, task_id), False


def _task_exists(conn: sqlite3.Connection, project_id: str, task_signature: str) -> bool:
    return bool(
        conn.execute(
            "select 1 from tasks where project_id = ? and task_signature = ?",
            (project_id, task_signature),
        ).fetchone()
    )


def _task_by_id(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    return workflow_task_payload(row)


def _parent_task_id(file_path: str, project_id: str, run_id: str) -> str:
    return _task_id(f"parent:{project_id}:{run_id}:{file_path}")


def _task_id(signature: str) -> str:
    return f"task-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]}"


def _priority(severity: str) -> int:
    return SEVERITY_PRIORITY.get(str(severity).lower(), 20)


def _safe_file_path(value: str | None) -> str:
    raw = (value or "project").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in PurePosixPath(raw).parts:
        return "project"
    return raw
