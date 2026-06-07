from __future__ import annotations

import json
import sqlite3
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cycle import next_step_from_memory, row_to_dict
from .db import connect, initialize_database
from .postgres_state import (
    initialize_postgres_database,
    postgres_lock_acquire,
    postgres_lock_release,
    postgres_project_get,
    postgres_run_start,
)


DEFAULT_OWNER = "workspace-agent"


def call_tool(db_path: str | Path, tool_name: str, payload: dict[str, Any] | None = None) -> tuple[int, dict]:
    payload = payload or {}
    backend = detect_backend(db_path)
    if backend["type"] == "postgres":
        return call_postgres_tool(backend["dsn"], tool_name, payload)
    initialize_database(db_path)
    if tool_name == "state_project_get":
        return state_project_get(db_path, require_str(payload, "project_id"))
    if tool_name == "state_memory_get":
        return state_memory_get(db_path, require_str(payload, "project_id"))
    if tool_name == "state_run_start":
        return state_run_start(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            latest_ref=payload.get("latest_ref"),
        )
    if tool_name == "state_lock_acquire":
        return state_lock_acquire(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
            ttl_seconds=int(payload.get("ttl_seconds") or 900),
        )
    if tool_name == "state_lock_release":
        return state_lock_release(
            db_path,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
        )
    if tool_name == "state_append_event":
        return state_append_event(
            db_path,
            run_id=require_str(payload, "run_id"),
            kind=require_str(payload, "kind"),
            path=str(payload.get("path") or ""),
            sha256=payload.get("sha256"),
        )
    return 2, {
        "status": "blocked",
        "blocker_code": "UNKNOWN_MCP_STATE_TOOL",
        "tool_name": tool_name,
        "allowed_tools": [
            "state_project_get",
            "state_memory_get",
            "state_run_start",
            "state_lock_acquire",
            "state_lock_release",
            "state_append_event",
        ],
    }


def state_project_get(db_path: str | Path, project_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select id, target, default_branch, status from projects where id = ?",
            (project_id,),
        ).fetchone()

    if row is None:
        return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before exposing it through MCP", project_id=project_id)
    return 0, {"status": "passed", "project": row_to_dict(row)}


def state_memory_get(db_path: str | Path, project_id: str) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, latest_ref, memory_json, stale_memory_decision, created_at
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
            "memory": None,
            "repository_delta": "no prior memory",
            "stale_memory_decision": "not_applicable",
        }

    return 0, {
        "status": "passed",
        "project_id": project_id,
        "memory": {
            "id": row["id"],
            "latest_ref": row["latest_ref"],
            "memory_json": json.loads(row["memory_json"]),
            "stale_memory_decision": row["stale_memory_decision"],
            "created_at": row["created_at"],
        },
        "repository_delta": "repository truth must be checked before trusting memory",
    }


def state_run_start(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str | None = None,
) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "select id, target, default_branch, status from projects where id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before starting a run", project_id=project_id)

        lock = active_lock(conn, project_id)
        if lock is not None and lock["run_id"] != run_id:
            return lock_blocked(lock, project_id)

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
        loaded_memory = json.loads(memory["memory_json"]) if memory else None
        prior_step = next_step_from_memory(loaded_memory)
        next_step = f"preflight {latest_ref or 'current ref'} before continuing: {prior_step}"
        run = conn.execute(
            "select id, project_id, status, current_focus, next_autonomous_step from runs where id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            conn.execute(
                """
                insert into runs(id, project_id, status, current_focus, next_autonomous_step)
                values (?, ?, ?, ?, ?)
                """,
                (run_id, project_id, "in_progress", "autonomous_cycle", next_step),
            )
            conn.commit()
            run = conn.execute(
                "select id, project_id, status, current_focus, next_autonomous_step from runs where id = ?",
                (run_id,),
            ).fetchone()

    return 0, {
        "status": "passed",
        "state_access_mode": "mcp_state_db",
        "project": row_to_dict(project),
        "run": row_to_dict(run),
        "loaded_memory": loaded_memory,
        "stale_memory_decision": memory["stale_memory_decision"] if memory else "not_applicable",
        "repository_delta": "repository truth must be checked before trusting memory",
        "next_autonomous_step": run["next_autonomous_step"],
    }


def state_lock_acquire(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str = DEFAULT_OWNER,
    ttl_seconds: int = 900,
) -> tuple[int, dict]:
    now = utc_now()
    expires_at = now + timedelta(seconds=max(1, ttl_seconds))
    lock_id = f"{project_id}:{run_id}:{owner}"
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute("select id from projects where id = ?", (project_id,)).fetchone()
        if project is None:
            return mcp_blocked("PROJECT_NOT_FOUND", "initialize project state before acquiring a lock", project_id=project_id)

        expire_stale_locks(conn, now)
        lock = active_lock(conn, project_id)
        if lock is not None:
            if lock["run_id"] == run_id and lock["owner"] == owner:
                return 0, {"status": "passed", "lock_acquired": True, "lock": row_to_dict(lock), "idempotent": True}
            return lock_blocked(lock, project_id)

        conn.execute(
            """
            insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at)
            values (?, ?, ?, ?, 'active', ?, ?)
            """,
            (lock_id, project_id, run_id, owner, format_dt(now), format_dt(expires_at)),
        )
        conn.commit()
        lock = active_lock(conn, project_id)

    return 0, {"status": "passed", "lock_acquired": True, "lock": row_to_dict(lock), "idempotent": False}


def state_lock_release(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
    owner: str = DEFAULT_OWNER,
) -> tuple[int, dict]:
    now = format_dt(utc_now())
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        lock = active_lock(conn, project_id)
        if lock is None:
            return 0, {"status": "passed", "lock_released": False, "reason": "no active lock"}
        if lock["run_id"] != run_id or lock["owner"] != owner:
            return lock_blocked(lock, project_id)
        conn.execute(
            """
            update state_locks
            set status = 'released', released_at = ?, updated_at = ?
            where id = ?
            """,
            (now, now, lock["id"]),
        )
        conn.commit()

    return 0, {"status": "passed", "lock_released": True, "project_id": project_id, "run_id": run_id}


def state_append_event(
    db_path: str | Path,
    *,
    run_id: str,
    kind: str,
    path: str,
    sha256: str | None = None,
) -> tuple[int, dict]:
    artifact_id = f"{run_id}:{kind}:{path or 'event'}"
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("select id, project_id from runs where id = ?", (run_id,)).fetchone()
        if run is None:
            return mcp_blocked("RUN_NOT_FOUND", "start run state before appending events", run_id=run_id)
        conn.execute(
            """
            insert or replace into artifacts(id, run_id, kind, path, sha256)
            values (?, ?, ?, ?, ?)
            """,
            (artifact_id, run_id, kind, path, sha256),
        )
        conn.commit()

    return 0, {"status": "passed", "event_appended": True, "artifact_id": artifact_id, "run_id": run_id}


def active_lock(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        select id, project_id, run_id, owner, status, acquired_at, expires_at, released_at
        from state_locks
        where project_id = ? and status = 'active' and released_at is null
        order by acquired_at desc
        limit 1
        """,
        (project_id,),
    ).fetchone()


def expire_stale_locks(conn: sqlite3.Connection, now: datetime) -> None:
    now_text = format_dt(now)
    conn.execute(
        """
        update state_locks
        set status = 'expired', released_at = ?, updated_at = ?
        where status = 'active'
          and released_at is null
          and expires_at is not null
          and expires_at <= ?
        """,
        (now_text, now_text, now_text),
    )


def lock_blocked(lock: sqlite3.Row, project_id: str) -> tuple[int, dict]:
    return 2, {
        "status": "blocked",
        "blocker_code": "STATE_LOCK_HELD",
        "project_id": project_id,
        "reason": "another run owns the active project state lock",
        "active_lock": row_to_dict(lock),
        "next_action": "retry after the active lock is released or expires",
    }


def mcp_blocked(blocker_code: str, reason: str, **extra: Any) -> tuple[int, dict]:
    payload = {"status": "blocked", "blocker_code": blocker_code, "reason": reason}
    payload.update(extra)
    return 2, payload


def require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def detect_backend(db_path: str | Path) -> dict:
    value = str(db_path)
    if value.startswith(("postgresql://", "postgres://")):
        return {
            "type": "postgres",
            "dsn": value,
            "driver_available": postgres_driver_available(),
        }
    return {"type": "sqlite", "path": value, "driver_available": True}


def postgres_driver_available() -> bool:
    return importlib.util.find_spec("psycopg") is not None or importlib.util.find_spec("psycopg2") is not None


def postgres_not_ready(tool_name: str, backend: dict) -> tuple[int, dict]:
    if backend["driver_available"]:
        blocker_code = "POSTGRES_BACKEND_NOT_IMPLEMENTED"
        reason = "Postgres driver is available, but MCP-state Postgres SQL adapter is not implemented yet"
    else:
        blocker_code = "POSTGRES_DRIVER_MISSING"
        reason = "Postgres DSN was requested, but no psycopg/psycopg2 driver is installed"
    return 2, {
        "status": "blocked",
        "blocker_code": blocker_code,
        "state_backend": "postgres",
        "tool_name": tool_name,
        "reason": reason,
        "next_action": "install/configure a Postgres driver and implement the Postgres SQL adapter before using this backend",
    }


def call_postgres_tool(dsn: str, tool_name: str, payload: dict[str, Any]) -> tuple[int, dict]:
    if tool_name == "state_project_get":
        return postgres_project_get(dsn, require_str(payload, "project_id"))
    if tool_name == "state_lock_acquire":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_lock_acquire(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
            ttl_seconds=int(payload.get("ttl_seconds") or 900),
        )
    if tool_name == "state_lock_release":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_lock_release(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            owner=str(payload.get("owner") or DEFAULT_OWNER),
        )
    if tool_name == "state_run_start":
        code, init_payload = initialize_postgres_database(dsn)
        if code != 0:
            return code, init_payload
        return postgres_run_start(
            dsn,
            project_id=require_str(payload, "project_id"),
            run_id=require_str(payload, "run_id"),
            latest_ref=payload.get("latest_ref"),
        )
    return postgres_not_ready(tool_name, {"type": "postgres", "dsn": dsn, "driver_available": postgres_driver_available()})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
