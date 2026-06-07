from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import connect
from .qg_workflow import selected_task_for_agent_takeover


def report(db_path: str | Path, run_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select runs.id as run_id, runs.status as run_status, runs.current_focus,
                   runs.next_autonomous_step, projects.id as project_id, projects.target
            from runs
            join projects on projects.id = runs.project_id
            where runs.id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return 2, {
                "status": "blocked",
                "blocker_code": "RUN_NOT_FOUND",
                "reason": f"run not found: {run_id}",
                "next_action": "initialize the run before requesting a report",
            }
        counts = {
            "findings": count(conn, "findings", run_id),
            "tasks": count(conn, "tasks", run_id),
            "qa_gates": count(conn, "qa_gate_results", run_id),
            "validation_attempts": count(conn, "validation_attempts", run_id),
        }

    return 0, {
        "status": "passed",
        "run": {
            "id": row["run_id"],
            "status": row["run_status"],
            "current_focus": row["current_focus"],
            "next_autonomous_step": row["next_autonomous_step"],
        },
        "project": {
            "id": row["project_id"],
            "target": row["target"],
        },
        "counts": counts,
        "selected_task_for_agent_takeover": selected_task_for_agent_takeover(
            db_path,
            project_id=row["project_id"],
            run_id=row["run_id"],
        ),
    }


def count(conn: sqlite3.Connection, table: str, run_id: str) -> int:
    return int(conn.execute(f"select count(*) from {table} where run_id = ?", (run_id,)).fetchone()[0])
