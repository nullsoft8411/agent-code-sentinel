from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import connect
from .file_inventory import sha256_file
from .task_workflow import assign_task_to_agent


def prepare_improvement_work_package(
    db_path: str,
    *,
    project_id: str,
    run_id: str,
    selected_task: dict[str, Any] | None,
    project_path: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    if selected_task is None:
        return 0, None

    task = selected_task
    if task["status"] in {"pending", "failed_validation"}:
        code, assigned = assign_task_to_agent(db_path, task_id=task["id"])
        if code != 0:
            return code, assigned
        task = assigned["task"]

    finding = _compat_finding(db_path, task["finding_id"])
    scan_finding = _scan_finding(db_path, task["finding_id"])
    file_evidence = _file_evidence(project_path, task.get("affected_file"))
    validation_commands = _candidate_validation_commands(project_path)
    approval_required = bool(task.get("affected_file"))
    return 0, {
        "status": "assigned_to_agent",
        "project_id": project_id,
        "run_id": run_id,
        "task": task,
        "finding": finding,
        "scan_finding": scan_finding,
        "required_context_files": [task["affected_file"]] if task.get("affected_file") else [],
        "file_evidence": file_evidence,
        "proposed_fix_plan": {
            "status": "needs_agent_analysis",
            "summary": "Inspect the affected file, create a bounded fix plan, request write approval, then validate.",
            "approval_required": approval_required,
            "validation_commands": validation_commands,
        },
        "approval_required": approval_required,
        "validation_commands": validation_commands,
        "next_autonomous_step": (
            f"inspect {task['affected_file']} for {task['id']} and request approval before edits"
            if task.get("affected_file")
            else f"inspect task {task['id']} and create a bounded fix plan"
        ),
    }


def _compat_finding(db_path: str, finding_id: str | None) -> dict[str, Any] | None:
    if not finding_id:
        return None
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, signature, category, severity, file_path, line_number, title, details, status
            from findings
            where id = ?
            """,
            (finding_id,),
        ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _scan_finding(db_path: str, finding_id: str | None) -> dict[str, Any] | None:
    if not finding_id:
        return None
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, scanner_name, rule_id, severity, status, title, message,
                   suggestion, evidence, signature
            from scan_findings
            where compatibility_finding_id = ?
            """,
            (finding_id,),
        ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _file_evidence(project_path: str | None, affected_file: str | None) -> dict[str, Any]:
    if not affected_file:
        return {"status": "not_applicable"}
    if not project_path:
        return {
            "status": "not_available",
            "reason": "project_path was not provided",
            "path": affected_file,
        }
    root = Path(project_path).resolve()
    path = (root / affected_file).resolve()
    if not str(path).startswith(str(root)):
        return {"status": "blocked", "blocker_code": "AFFECTED_FILE_OUTSIDE_PROJECT", "path": affected_file}
    if not path.exists() or not path.is_file():
        return {"status": "not_available", "reason": "affected file is not readable", "path": affected_file}
    return {
        "status": "passed",
        "path": affected_file,
        "bytes": path.stat().st_size,
        "content_sha256": sha256_file(path),
    }


def _candidate_validation_commands(project_path: str | None) -> list[str]:
    if not project_path:
        return []
    root = Path(project_path)
    commands: list[str] = []
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file() or (root / "tests").is_dir():
        commands.append("python3 -m pytest tests -q")
    if (root / "package.json").is_file():
        commands.append("npm test")
    if (root / "go.mod").is_file():
        commands.append("go test ./...")
    return commands
