from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import connect

REQUIRED_REVIEW_OUTCOMES = (
    "reuse",
    "duplicate_code",
    "dead_code",
    "unused_code",
)


def qa_gates(db_path: str | Path, run_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select gate, status, evidence, why_it_matters, next_action from qa_gate_results where run_id = ? order by gate",
            (run_id,),
        ).fetchall()

    gates = [dict(row) for row in rows]
    blocking = [row["gate"] for row in gates if row["status"] == "blocking"]
    outcomes = qa_review_outcomes_from_gates(gates)
    missing = [item["gate"] for item in outcomes if item["status"] == "missing"]
    status = "blocking" if blocking or missing else "passed"
    return (2 if blocking or missing else 0), {
        "status": status,
        "run_id": run_id,
        "blocking_gates": blocking,
        "missing_review_outcomes": missing,
        "qa_review_outcomes": outcomes,
        "gates": gates,
    }


def qa_review_outcomes(db_path: str | Path, run_id: str) -> list[dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select gate, status, evidence, why_it_matters, next_action from qa_gate_results where run_id = ?",
            (run_id,),
        ).fetchall()
    return qa_review_outcomes_from_gates([dict(row) for row in rows])


def qa_review_outcomes_from_gates(gates: list[dict]) -> list[dict]:
    by_gate = {str(row.get("gate")): row for row in gates}
    outcomes: list[dict] = []
    for gate in REQUIRED_REVIEW_OUTCOMES:
        row = by_gate.get(gate)
        if row is None:
            outcomes.append({
                "gate": gate,
                "status": "missing",
                "evidence": "review outcome not recorded",
                "required": True,
            })
            continue
        outcomes.append({
            "gate": gate,
            "status": row.get("status"),
            "evidence": row.get("evidence"),
            "required": True,
        })
    return outcomes
