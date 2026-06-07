from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import connect

TASK_STATUSES = {
    "pending",
    "assigned_to_agent",
    "in_progress",
    "blocked",
    "blocked_approval_required",
    "blocked_validation_unavailable",
    "failed_validation",
    "completed",
    "cancelled",
}

RUNNABLE_STATUSES = {"pending", "assigned_to_agent", "in_progress", "failed_validation"}
BLOCKED_STATUSES = {"blocked", "blocked_approval_required", "blocked_validation_unavailable"}


def update_task_status(
    db_path: str,
    *,
    task_id: str,
    status: str,
    increment_attempt: bool = False,
) -> tuple[int, dict[str, Any]]:
    if status not in TASK_STATUSES:
        return 2, {"status": "blocked", "blocker_code": "INVALID_TASK_STATUS", "task_status": status}
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if task is None:
            return 2, {"status": "blocked", "blocker_code": "TASK_NOT_FOUND"}
        conn.execute(
            """
            update tasks
            set status = ?,
                attempt_count = attempt_count + ?,
                updated_at = datetime('now')
            where id = ?
            """,
            (status, 1 if increment_attempt else 0, task_id),
        )
        if task["parent_task_id"]:
            refresh_parent_progress(conn, task["parent_task_id"])
        conn.commit()
        updated = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        return 0, {"status": "passed", "task": workflow_task_payload(updated)}


def assign_task_to_agent(db_path: str, *, task_id: str) -> tuple[int, dict[str, Any]]:
    return update_task_status(db_path, task_id=task_id, status="assigned_to_agent")


def next_runnable_task(db_path: str, *, project_id: str, run_id: str | None = None) -> dict[str, Any] | None:
    clauses = ["project_id = ?", f"status in ({','.join('?' for _ in RUNNABLE_STATUSES)})"]
    values: list[Any] = [project_id, *sorted(RUNNABLE_STATUSES)]
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            select * from tasks
            where {' and '.join(clauses)}
              and task_type != 'parent'
              and attempt_count < max_attempts
            order by
              case status
                when 'in_progress' then 1
                when 'assigned_to_agent' then 2
                when 'failed_validation' then 3
                else 4
              end,
              priority desc,
              subtask_order,
              created_at,
              id
            limit 1
            """,
            tuple(values),
        ).fetchone()
    return workflow_task_payload(row) if row else None


def refresh_parent_progress(conn: sqlite3.Connection, parent_task_id: str) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select status from tasks where parent_task_id = ? order by subtask_order",
        (parent_task_id,),
    ).fetchall()
    if not rows:
        return
    statuses = [row["status"] for row in rows]
    completed = statuses.count("completed")
    blocked = sum(1 for status in statuses if status in BLOCKED_STATUSES)
    failed = statuses.count("failed_validation")
    if completed == len(statuses):
        parent_status = "completed"
    elif blocked:
        parent_status = "blocked"
    elif failed:
        parent_status = "failed_validation"
    elif any(status == "in_progress" for status in statuses):
        parent_status = "in_progress"
    elif any(status == "assigned_to_agent" for status in statuses):
        parent_status = "assigned_to_agent"
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
            progress_json(total=len(statuses), completed=completed, blocked=blocked, failed=failed),
            parent_task_id,
        ),
    )


def progress_json(*, total: int, completed: int, blocked: int, failed: int = 0) -> str:
    return json.dumps(
        {
            "total_subtasks": total,
            "completed_subtasks": completed,
            "blocked_subtasks": blocked,
            "failed_subtasks": failed,
        },
        sort_keys=True,
    )


def workflow_task_payload(row: sqlite3.Row) -> dict[str, Any]:
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
