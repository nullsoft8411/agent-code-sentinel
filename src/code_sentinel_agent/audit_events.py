from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .db import connect
from .db_rows import sqlite_row_to_dict, sqlite_table_columns


@dataclass(frozen=True)
class AuditEventInput:
    id: str | None
    project_id: str
    event_type: str
    summary: str
    run_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def append_audit_event(db_path: str, event: AuditEventInput) -> dict[str, Any]:
    event_id = event.id or audit_event_id(
        project_id=event.project_id,
        run_id=event.run_id,
        event_type=event.event_type,
        summary=event.summary,
    )
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "audit_events")
        conn.execute(
            """
            insert or replace into audit_events(id, run_id, project_id, event_type, summary, payload_json)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event.run_id,
                event.project_id,
                event.event_type,
                event.summary,
                json.dumps(event.payload, sort_keys=True),
            ),
        )
        conn.commit()
        row = conn.execute("select * from audit_events where id = ?", (event_id,)).fetchone()
    return audit_event_payload(sqlite_row_to_dict(row, columns))


def list_audit_events(
    db_path: str,
    *,
    project_id: str,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["project_id = ?"]
    values: list[Any] = [project_id]
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    with connect(db_path) as conn:
        columns = sqlite_table_columns(conn, "audit_events")
        rows = conn.execute(
            f"""
            select * from audit_events
            where {' and '.join(clauses)}
            order by created_at, id
            """,
            tuple(values),
        ).fetchall()
    return [audit_event_payload(sqlite_row_to_dict(row, columns)) for row in rows]


def audit_event_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "project_id": row["project_id"],
        "event_type": row["event_type"],
        "summary": row["summary"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": row["created_at"],
    }


def audit_event_id(*, project_id: str, run_id: str | None, event_type: str, summary: str) -> str:
    import hashlib

    digest = hashlib.sha256("|".join([project_id, str(run_id), event_type, summary]).encode("utf-8")).hexdigest()[:16]
    return f"audit-{digest}"
