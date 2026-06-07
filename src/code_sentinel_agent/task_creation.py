from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .db import connect

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
            _refresh_parent_progress(conn, _parent_task_id(file_path, project_id, run_id))
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


def update_task_status(
    db_path: str,
    *,
    task_id: str,
    status: str,
) -> tuple[int, dict[str, Any]]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if task is None:
            return 2, {"status": "blocked", "blocker_code": "TASK_NOT_FOUND"}
        conn.execute(
            "update tasks set status = ?, updated_at = datetime('now') where id = ?",
            (status, task_id),
        )
        if task["parent_task_id"]:
            _refresh_parent_progress(conn, task["parent_task_id"])
        conn.commit()
        updated = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        return 0, {"status": "passed", "task": _task_payload(updated)}


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
            _progress_json(total=len(findings), completed=0, blocked=0),
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


def _refresh_parent_progress(conn: sqlite3.Connection, parent_task_id: str) -> None:
    rows = conn.execute(
        "select status from tasks where parent_task_id = ? order by subtask_order",
        (parent_task_id,),
    ).fetchall()
    if not rows:
        return
    statuses = [row["status"] for row in rows]
    completed = statuses.count("completed")
    blocked = statuses.count("blocked")
    if completed == len(statuses):
        parent_status = "completed"
    elif blocked:
        parent_status = "blocked"
    elif any(status == "in_progress" for status in statuses):
        parent_status = "in_progress"
    else:
        parent_status = "pending"
    conn.execute(
        """
        update tasks
        set status = ?, progress_json = ?, updated_at = datetime('now')
        where id = ?
        """,
        (
            parent_status,
            _progress_json(total=len(statuses), completed=completed, blocked=blocked),
            parent_task_id,
        ),
    )


def _task_exists(conn: sqlite3.Connection, project_id: str, task_signature: str) -> bool:
    return bool(
        conn.execute(
            "select 1 from tasks where project_id = ? and task_signature = ?",
            (project_id, task_signature),
        ).fetchone()
    )


def _task_by_id(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    return _task_payload(row)


def _task_payload(row: sqlite3.Row) -> dict[str, Any]:
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
        "task_type": row["task_type"],
        "task_signature": row["task_signature"],
        "subtask_order": row["subtask_order"],
        "progress": json.loads(row["progress_json"] or "{}"),
    }


def _parent_task_id(file_path: str, project_id: str, run_id: str) -> str:
    return _task_id(f"parent:{project_id}:{run_id}:{file_path}")


def _task_id(signature: str) -> str:
    return f"task-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]}"


def _priority(severity: str) -> int:
    return SEVERITY_PRIORITY.get(str(severity).lower(), 20)


def _progress_json(*, total: int, completed: int, blocked: int) -> str:
    return json.dumps(
        {
            "total_subtasks": total,
            "completed_subtasks": completed,
            "blocked_subtasks": blocked,
        },
        sort_keys=True,
    )


def _safe_file_path(value: str | None) -> str:
    raw = (value or "project").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in PurePosixPath(raw).parts:
        return "project"
    return raw
