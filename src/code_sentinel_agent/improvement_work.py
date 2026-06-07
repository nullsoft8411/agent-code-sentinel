from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import connect
from .file_inventory import sha256_file
from .qa_gates import REQUIRED_REVIEW_OUTCOMES
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
    qa_review_outcomes = _derive_and_persist_review_outcomes(
        db_path,
        run_id=run_id,
        task=task,
        file_evidence=file_evidence,
        validation_commands=validation_commands,
    )
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
        "qa_review_outcomes": qa_review_outcomes,
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
    source_snapshot = _source_snapshot(path)
    return {
        "status": "passed",
        "path": affected_file,
        "bytes": path.stat().st_size,
        "content_sha256": sha256_file(path),
        "source_snapshot": source_snapshot,
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


def _derive_and_persist_review_outcomes(
    db_path: str,
    *,
    run_id: str,
    task: dict[str, Any],
    file_evidence: dict[str, Any],
    validation_commands: list[str],
) -> list[dict[str, Any]]:
    outcomes = _derive_review_outcomes(
        task=task,
        file_evidence=file_evidence,
        validation_commands=validation_commands,
    )
    with connect(db_path) as conn:
        for outcome in outcomes:
            conn.execute(
                """
                insert into qa_gate_results(gate, run_id, status, evidence, why_it_matters, next_action, id)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id, gate) do update set
                  status = excluded.status,
                  evidence = excluded.evidence,
                  why_it_matters = excluded.why_it_matters,
                  next_action = excluded.next_action
                """,
                (
                    outcome["gate"],
                    run_id,
                    outcome["status"],
                    outcome["evidence"],
                    outcome["why_it_matters"],
                    outcome["next_action"],
                    f"qg-review-{run_id}-{outcome['gate']}",
                ),
            )
        conn.commit()
    return outcomes


def _derive_review_outcomes(
    *,
    task: dict[str, Any],
    file_evidence: dict[str, Any],
    validation_commands: list[str],
) -> list[dict[str, Any]]:
    snapshot = file_evidence.get("source_snapshot") if isinstance(file_evidence, dict) else None
    readable = file_evidence.get("status") == "passed" and isinstance(snapshot, dict)
    blocker = file_evidence.get("blocker_code") or file_evidence.get("reason") or "affected file is not readable"
    duplicate_lines = int(snapshot.get("duplicate_non_empty_lines", 0)) if readable else 0
    dead_markers = int(snapshot.get("dead_code_markers", 0)) if readable else 0
    unused_markers = int(snapshot.get("unused_markers", 0)) if readable else 0
    common = {
        "task_id": task["id"],
        "affected_file": task.get("affected_file"),
        "required": True,
    }
    review_map = {
        "reuse": (
            "passed" if readable else "blocking",
            (
                "affected file inspected; reuse decision must prefer existing local patterns; "
                f"validation_candidates={len(validation_commands)}"
                if readable
                else f"reuse review blocked: {blocker}"
            ),
            "Reuse existing project APIs and patterns before editing the selected task file.",
            "continue with bounded fix plan after citing reused local patterns" if readable else "provide readable affected file evidence",
        ),
        "duplicate_code": (
            "passed" if readable else "blocking",
            (
                f"affected file duplicate scan completed; duplicate_non_empty_lines={duplicate_lines}"
                if readable
                else f"duplicate-code review blocked: {blocker}"
            ),
            "Duplicate code increases maintenance risk and must be checked before autonomous edits.",
            "avoid introducing duplicate code in the bounded fix" if readable else "provide readable affected file evidence",
        ),
        "dead_code": (
            "passed" if readable else "blocking",
            (
                f"affected file dead-code marker scan completed; dead_code_markers={dead_markers}"
                if readable
                else f"dead-code review blocked: {blocker}"
            ),
            "Dead code can hide stale execution paths and false completion claims.",
            "avoid adding dead code and remove only scoped dead paths with evidence" if readable else "provide readable affected file evidence",
        ),
        "unused_code": (
            "passed" if readable else "blocking",
            (
                f"affected file unused-code marker scan completed; unused_markers={unused_markers}"
                if readable
                else f"unused-code review blocked: {blocker}"
            ),
            "Unused code should not be introduced by the Workspace Agent task takeover.",
            "keep the bounded fix limited to used code paths" if readable else "provide readable affected file evidence",
        ),
    }
    return [
        {
            **common,
            "gate": gate,
            "status": review_map[gate][0],
            "evidence": review_map[gate][1],
            "why_it_matters": review_map[gate][2],
            "next_action": review_map[gate][3],
        }
        for gate in REQUIRED_REVIEW_OUTCOMES
    ]


def _source_snapshot(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")[:262144]
    lines = text.splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    duplicate_non_empty_lines = len(non_empty) - len(set(non_empty))
    lowered = text.lower()
    return {
        "line_count": len(lines),
        "non_empty_line_count": len(non_empty),
        "duplicate_non_empty_lines": duplicate_non_empty_lines,
        "dead_code_markers": lowered.count("dead code") + lowered.count("unreachable"),
        "unused_markers": lowered.count("unused"),
    }
