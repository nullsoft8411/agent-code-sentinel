from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import connect


def resume_cycle(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str,
) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "select id, target, default_branch from projects where id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            return 2, {
                "status": "blocked",
                "blocker_code": "PROJECT_NOT_FOUND",
                "project_id": project_id,
                "next_action": "initialize project state before resuming an autonomous cycle",
            }

        memory = conn.execute(
            """
            select latest_ref, memory_json, stale_memory_decision
            from memories
            where project_id = ?
            order by created_at desc
            limit 1
            """,
            (project_id,),
        ).fetchone()
        previous_run = conn.execute(
            """
            select id, status, current_focus, next_autonomous_step
            from runs
            where project_id = ? and id != ?
            order by created_at desc
            limit 1
            """,
            (project_id, run_id),
        ).fetchone()

        loaded_memory = json.loads(memory["memory_json"]) if memory else None
        prior_step = next_step_from_memory(loaded_memory)
        next_autonomous_step = f"preflight {latest_ref} before continuing: {prior_step}"

        existing = conn.execute(
            "select id, status, current_focus, next_autonomous_step from runs where id = ?",
            (run_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                insert into runs(id, project_id, status, current_focus, next_autonomous_step)
                values (?, ?, ?, ?, ?)
                """,
                (run_id, project_id, "in_progress", "autonomous_cycle", next_autonomous_step),
            )
            conn.commit()
            run = conn.execute(
                "select id, status, current_focus, next_autonomous_step from runs where id = ?",
                (run_id,),
            ).fetchone()
        else:
            run = existing

    return 0, {
        "status": "passed",
        "project_id": project_id,
        "project": {
            "id": project["id"],
            "target": project["target"],
            "default_branch": project["default_branch"],
        },
        "previous_run": row_to_dict(previous_run),
        "loaded_memory": loaded_memory,
        "repository_delta": "repository truth must be checked before trusting memory",
        "stale_memory_decision": memory["stale_memory_decision"] if memory else "not_applicable",
        "latest_ref": latest_ref,
        "run": row_to_dict(run),
        "next_autonomous_step": run["next_autonomous_step"],
    }


def next_step_from_memory(loaded_memory: dict | None) -> str:
    if not loaded_memory:
        return "run repository preflight"
    value = loaded_memory.get("next_autonomous_step")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "run repository preflight"


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}
