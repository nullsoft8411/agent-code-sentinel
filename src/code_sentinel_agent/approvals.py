from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import connect


def approval_check(
    db_path: str | Path,
    *,
    run_id: str,
    target_project: str,
    branch: str,
    path: str,
    action: str,
) -> tuple[int, dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select *
            from approvals
            where run_id = ?
              and target_project = ?
              and (branch = ? or branch is null)
              and consumed_at is null
            order by created_at desc
            """,
            (run_id, target_project, branch),
        ).fetchall()

    if not rows:
        return blocked("WRITE_APPROVAL_MISSING", "no approval record matches run, project, and branch")

    for row in rows:
        approval = approval_payload(row)
        if approval_expired(row["expires_at"]):
            continue
        if path not in approval["allowed_paths"]:
            return blocked("WRITE_PATH_NOT_APPROVED", f"path is outside approval scope: {path}", approval)
        if action not in approval["allowed_actions"]:
            return blocked("WRITE_ACTION_NOT_APPROVED", f"action is outside approval scope: {action}", approval)
        return 0, {
            "status": "passed",
            "write_allowed": True,
            "run_id": run_id,
            "target_project": target_project,
            "path": path,
            "action": action,
            "approval": approval,
        }

    return blocked("WRITE_APPROVAL_EXPIRED", "matching approval records are expired")


def approval_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "target_project": row["target_project"],
        "branch": row["branch"],
        "allowed_paths": json.loads(row["allowed_paths_json"]),
        "allowed_actions": json.loads(row["allowed_actions_json"]),
        "approved_by": row["approved_by"],
        "approval_evidence": row["approval_evidence"],
        "expires_at": row["expires_at"],
    }


def approval_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


def blocked(blocker_code: str, reason: str, approval: dict | None = None) -> tuple[int, dict]:
    payload = {
        "status": "blocked",
        "write_allowed": False,
        "blocker_code": blocker_code,
        "reason": reason,
        "next_action": "provide explicit per-run approval for the exact project, branch, path, and action",
    }
    if approval:
        payload["approval"] = approval
    return 2, payload

