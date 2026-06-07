from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import connect


def memory_delta(db_path: str | Path, project_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select latest_ref, memory_json, stale_memory_decision
            from memories
            where project_id = ?
            order by created_at desc
            limit 1
            """,
            (project_id,),
        ).fetchone()

    if row is None:
        return 0, {
            "status": "passed",
            "project_id": project_id,
            "loaded_memory": None,
            "repository_delta": "no prior memory",
            "stale_memory_decision": "not_applicable",
            "latest_ref": None,
        }

    return 0, {
        "status": "passed",
        "project_id": project_id,
        "loaded_memory": json.loads(row["memory_json"]),
        "repository_delta": "repository truth must be checked before trusting memory",
        "stale_memory_decision": row["stale_memory_decision"],
        "latest_ref": row["latest_ref"],
    }

