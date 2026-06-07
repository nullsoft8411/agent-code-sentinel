from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import connect


def qa_gates(db_path: str | Path, run_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select gate, status, evidence, why_it_matters, next_action from qa_gate_results where run_id = ? order by gate",
            (run_id,),
        ).fetchall()

    gates = [dict(row) for row in rows]
    blocking = [row["gate"] for row in gates if row["status"] == "blocking"]
    status = "blocking" if blocking else "passed"
    return (2 if blocking else 0), {
        "status": status,
        "run_id": run_id,
        "blocking_gates": blocking,
        "gates": gates,
    }

