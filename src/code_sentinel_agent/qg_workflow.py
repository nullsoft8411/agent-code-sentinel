from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .execution_sessions import ExecutionSessionInput, record_execution_session
from .qa_gates import qa_review_outcomes
from .scan_findings import ScanFindingInput, upsert_scan_finding
from .task_creation import create_tasks_from_findings
from .task_workflow import next_runnable_task
from .validation_runner import ValidationResult, normalize_validation_payload


def process_quality_gate_payload(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    code, normalized = normalize_validation_payload(payload)
    if code != 0:
        return code, normalized
    assert isinstance(normalized, ValidationResult)

    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "select project_id from runs where id = ?",
            (normalized.run_id,),
        ).fetchone()
        if run is None:
            return 2, {
                "status": "blocked",
                "blocker_code": "RUN_NOT_FOUND",
                "reason": f"run not found: {normalized.run_id}",
            }
        if run["project_id"] != normalized.project_id:
            return 2, {
                "status": "blocked",
                "blocker_code": "RUN_PROJECT_MISMATCH",
                "run_project_id": run["project_id"],
                "payload_project_id": normalized.project_id,
            }
        validation_attempt_id = _validation_attempt_id(normalized)
        conn.execute(
            """
            insert or replace into validation_attempts(
              id, run_id, command, cwd, exit_code, stdout_summary,
              stderr_summary, status
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validation_attempt_id,
                normalized.run_id,
                normalized.command,
                normalized.cwd,
                normalized.exit_code,
                normalized.stdout_summary,
                normalized.stderr_summary,
                normalized.status,
            ),
        )
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
                normalized.gate,
                normalized.run_id,
                normalized.status,
                normalized.evidence,
                "failed validation must become tracked findings/tasks before autonomous completion",
                "create or continue the selected task for the failing gate",
                f"qg-{_digest(normalized.run_id, normalized.gate)}",
            ),
        )
        conn.commit()

    session = record_execution_session(
        db_path,
        ExecutionSessionInput(
            id=f"exec-{validation_attempt_id}",
            run_id=normalized.run_id,
            project_id=normalized.project_id,
            session_type="validation",
            script_name="qg-workflow",
            execution_method="agent_runtime_cli",
            command=normalized.command,
            status=normalized.status,
            output={
                "gate": normalized.gate,
                "exit_code": normalized.exit_code,
                "stdout_summary": normalized.stdout_summary,
                "stderr_summary": normalized.stderr_summary,
            },
            error_summary=normalized.stderr_summary if normalized.status == "blocking" else None,
            files_modified=[],
            attempt_log=[
                {
                    "step": "validation_result_normalized",
                    "status": normalized.status,
                    "evidence": normalized.evidence,
                }
            ],
            completed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )

    created_findings = []
    for index, finding in enumerate(normalized.findings, start=1):
        scan_finding = ScanFindingInput(
            id=f"qg-finding-{_digest(normalized.run_id, normalized.gate, str(index), finding['evidence'])}",
            project_id=normalized.project_id,
            run_id=normalized.run_id,
            scanner_name="validation_runner",
            rule_id=finding["rule_id"],
            signature=_finding_signature(normalized, finding),
            severity=finding["severity"],
            title=finding["title"],
            message=finding["message"],
            evidence=finding["evidence"],
            file_path=finding["file_path"],
            line_number=finding["line_number"],
            metadata={"gate": normalized.gate, "command": normalized.command},
        )
        created_findings.append(upsert_scan_finding(str(db_path), scan_finding))

    task_code, task_payload = create_tasks_from_findings(
        str(db_path),
        project_id=normalized.project_id,
        run_id=normalized.run_id,
    )
    if task_code != 0:
        return task_code, task_payload

    selected_task = selected_task_for_agent_takeover(
        str(db_path),
        project_id=normalized.project_id,
        run_id=normalized.run_id,
    )
    with connect(db_path) as conn:
        if selected_task:
            conn.execute(
                """
                update runs
                set current_focus = ?, next_autonomous_step = ?, updated_at = datetime('now')
                where id = ?
                """,
                (
                    "qa_gate_takeover",
                    f"take over {selected_task['task_type']} {selected_task['id']}: {selected_task['title']}",
                    normalized.run_id,
                ),
            )
            conn.commit()

    review_outcomes = qa_review_outcomes(db_path, normalized.run_id)
    status_code = 2 if normalized.status == "blocking" else 0
    return status_code, {
        "status": normalized.status,
        "project_id": normalized.project_id,
        "run_id": normalized.run_id,
        "gate": normalized.gate,
        "validation": {
            "command": normalized.command,
            "exit_code": normalized.exit_code,
            "status": normalized.status,
            "evidence": normalized.evidence,
        },
        "execution_session": session,
        "findings": created_findings,
        "task_creation": task_payload,
        "qa_review_outcomes": {
            "required": ["reuse", "duplicate_code", "dead_code", "unused_code"],
            "missing_review_outcomes": [
                item["gate"]
                for item in review_outcomes
                if item["status"] == "missing"
            ],
            "outcomes": review_outcomes,
        },
        "selected_task_for_agent_takeover": selected_task,
        "secret_redaction_applied": normalized.secret_redaction_applied,
    }


def selected_task_for_agent_takeover(
    db_path: str | Path,
    *,
    project_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    return next_runnable_task(str(db_path), project_id=project_id, run_id=run_id)


def _finding_signature(result: ValidationResult, finding: dict[str, Any]) -> str:
    return "finding:qg:" + _digest(
        result.project_id,
        result.run_id,
        result.gate,
        result.command,
        finding["file_path"],
        str(finding["line_number"]),
        finding["message"],
    )


def _validation_attempt_id(result: ValidationResult) -> str:
    return "validation-" + _digest(result.run_id, result.gate, result.command, str(result.exit_code))


def _digest(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
