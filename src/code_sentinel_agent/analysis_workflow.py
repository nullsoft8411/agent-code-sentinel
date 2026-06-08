from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from .agent_analysis import analyze_context
from .db import initialize_database
from .qg_workflow import selected_task_for_agent_takeover
from .reports import report
from .scan_findings import ScanFindingInput, upsert_scan_finding
from .scan_jobs import ScanJobInput, create_scan_job, update_scan_job_progress
from .task_creation import create_tasks_from_findings


def analyze_to_state(db_path: str | Path, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist Agent-supplied findings plus deterministic checks and derive takeover tasks."""
    initialize_database(db_path)
    code, analysis = analyze_context(payload)
    if code != 0:
        return code, analysis

    project_id = _required_analysis_value(payload, "project_id", analysis)
    run_id = _required_analysis_value(payload, "run_id", analysis)
    scan_job = _ensure_analysis_scan_job(
        str(db_path),
        project_id=project_id,
        run_id=run_id,
        payload=payload,
    )
    persisted_findings = []
    for finding in analysis["findings"]:
        persisted_findings.append(
            upsert_scan_finding(
                str(db_path),
                ScanFindingInput(
                    id=_analysis_stable_id("analysis-finding", run_id, finding["signature"]),
                    project_id=project_id,
                    run_id=run_id,
                    scan_job_id=scan_job["id"],
                    scanner_name=str(finding.get("source") or "agent_analysis"),
                    rule_id=str(finding.get("rule_id") or "agent_analysis"),
                    signature=str(finding["signature"]),
                    severity=str(finding["severity"]),
                    title=str(finding["title"]),
                    file_path=str(finding.get("file_path") or "project"),
                    line_number=finding.get("line_number") if isinstance(finding.get("line_number"), int) else None,
                    status=str(finding.get("status") or "open"),
                    message=str(finding.get("description") or ""),
                    evidence=str(finding.get("evidence") or ""),
                    metadata={
                        "source": "analyze_to_state",
                        "output_contract_version": analysis["output_contract_version"],
                    },
                ),
            )
        )

    scan_job = update_scan_job_progress(
        str(db_path),
        scan_job_id=scan_job["id"],
        status="completed",
        files_total=len({finding["file_path"] for finding in analysis["findings"]}),
        files_scanned=len({finding["file_path"] for finding in analysis["findings"]}),
    )
    task_code, task_payload = create_tasks_from_findings(
        str(db_path),
        project_id=project_id,
        run_id=run_id,
        max_subtasks_per_parent=int(payload.get("max_subtasks_per_parent") or 20),
    )
    if task_code != 0:
        return task_code, task_payload

    selected_task = selected_task_for_agent_takeover(str(db_path), project_id=project_id, run_id=run_id)
    report_code, report_payload = report(db_path, run_id)
    if report_code != 0:
        return report_code, report_payload

    return 0, {
        "status": "passed",
        "mode": "agent_supplied_analysis_persistence",
        "agent_execution_boundary": (
            "The Workspace Agent performs reasoning and supplies finding_candidates; "
            "this script only normalizes, redacts, persists, creates tasks, and reports state."
        ),
        "project_id": project_id,
        "run_id": run_id,
        "analysis": analysis,
        "scan_job": scan_job,
        "persisted_findings": persisted_findings,
        "task_creation": task_payload,
        "selected_task_for_agent_takeover": selected_task,
        "report": report_payload,
        "next_autonomous_step": (
            f"take over {selected_task['task_type']} {selected_task['id']}: {selected_task['title']}"
            if selected_task
            else "review analysis findings and create a bounded fix queue"
        ),
    }


def _ensure_analysis_scan_job(
    db_path: str,
    *,
    project_id: str,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    scan_job_id = str(payload.get("scan_job_id") or _analysis_stable_id("analysis-scan", project_id, run_id))
    try:
        return create_scan_job(
            db_path,
            ScanJobInput(
                id=scan_job_id,
                project_id=project_id,
                run_id=run_id,
                scanner_name="agent_analysis",
                scan_type="agent_native_analysis",
                status="running",
                target_ref=payload.get("latest_ref"),
                metadata={"source": "analyze_to_state"},
            ),
        )
    except sqlite3.IntegrityError:
        return update_scan_job_progress(db_path, scan_job_id=scan_job_id, status="running")


def _required_analysis_value(payload: dict[str, Any], key: str, analysis: dict[str, Any]) -> str:
    value = str(payload.get(key) or analysis.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _analysis_stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"
