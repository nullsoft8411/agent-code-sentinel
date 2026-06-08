from __future__ import annotations

import argparse
import json
import sys

from .agent_analysis import analyze_context, load_analysis_payload
from .analysis_contract import AnalysisContractInput, build_analysis_contract
from .analysis_workflow import analyze_to_state
from .approvals import approval_check
from .audit_events import AuditEventInput, append_audit_event, list_audit_events
from .check_detection import detect_checks
from .cycle import resume_cycle, run_autonomous_cycle
from .db import initialize_database
from .execution_sessions import parse_execution_session_payload, record_execution_session
from .file_inventory import FileCheckInput, build_file_inventory, list_file_checks, record_file_check
from .memory import memory_delta
from .mcp_state import call_tool
from .preflight import preflight
from .project_context import project_context
from .qg_workflow import process_quality_gate_payload
from .qa_gates import qa_gates
from .reports import report
from .plugin_executions import PluginExecutionInput, list_plugin_executions, record_plugin_execution
from .scan_jobs import list_scan_jobs, update_scan_job_progress
from .task_creation import create_tasks_from_findings
from .task_workflow import assign_task_to_agent, next_runnable_task, update_task_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cs-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_init = db_subparsers.add_parser("init")
    db_init.add_argument("--db", required=True)

    detect = subparsers.add_parser("detect-checks")
    detect.add_argument("--path", required=True)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--project", required=True)

    context_parser = subparsers.add_parser("project-context")
    context_parser.add_argument("--project", required=True)
    context_parser.add_argument("--max-files", type=int, default=80)

    qa = subparsers.add_parser("qa-gates")
    qa.add_argument("--db", required=True)
    qa.add_argument("--run-id", required=True)

    memory = subparsers.add_parser("memory-delta")
    memory.add_argument("--db", required=True)
    memory.add_argument("--project-id", required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--db", required=True)
    report_parser.add_argument("--run-id", required=True)

    approval_parser = subparsers.add_parser("approval-check")
    approval_parser.add_argument("--db", required=True)
    approval_parser.add_argument("--run-id", required=True)
    approval_parser.add_argument("--target-project", required=True)
    approval_parser.add_argument("--branch", required=True)
    approval_parser.add_argument("--path", required=True)
    approval_parser.add_argument("--action", required=True)

    resume = subparsers.add_parser("resume-cycle")
    resume.add_argument("--db", required=True)
    resume.add_argument("--project-id", required=True)
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--latest-ref", required=True)

    run_cycle = subparsers.add_parser("run-cycle")
    run_cycle.add_argument("--db", required=True)
    run_cycle.add_argument("--payload-json", required=True)

    mcp_state = subparsers.add_parser("mcp-state")
    mcp_state.add_argument("--db", required=True)
    mcp_state.add_argument("--tool", required=True)
    mcp_state.add_argument("--payload-json", default="{}")

    analyze = subparsers.add_parser("analyze-context")
    analyze_input = analyze.add_mutually_exclusive_group(required=True)
    analyze_input.add_argument("--input-json")
    analyze_input.add_argument("--input-file")

    analyze_state = subparsers.add_parser("analyze-to-state")
    analyze_state.add_argument("--db", required=True)
    analyze_state_input = analyze_state.add_mutually_exclusive_group(required=True)
    analyze_state_input.add_argument("--input-json")
    analyze_state_input.add_argument("--input-file")

    analysis_contract = subparsers.add_parser("analysis-contract")
    analysis_contract.add_argument("--project-id", required=True)
    analysis_contract.add_argument("--run-id", required=True)
    analysis_contract.add_argument("--target-project", required=True)
    analysis_contract.add_argument("--file", action="append", default=[])
    analysis_contract.add_argument("--validation-command", action="append", default=[])

    create_tasks = subparsers.add_parser("create-tasks")
    create_tasks.add_argument("--db", required=True)
    create_tasks.add_argument("--project-id", required=True)
    create_tasks.add_argument("--run-id", required=True)
    create_tasks.add_argument("--max-subtasks-per-parent", type=int, default=20)

    task_status = subparsers.add_parser("task-status")
    task_status.add_argument("--db", required=True)
    task_status.add_argument("--task-id", required=True)
    task_status.add_argument("--status", required=True)
    task_status.add_argument("--increment-attempt", action="store_true")

    task_assign = subparsers.add_parser("task-assign")
    task_assign.add_argument("--db", required=True)
    task_assign.add_argument("--task-id", required=True)

    next_task = subparsers.add_parser("next-task")
    next_task.add_argument("--db", required=True)
    next_task.add_argument("--project-id", required=True)
    next_task.add_argument("--run-id")

    qg_workflow = subparsers.add_parser("qg-workflow")
    qg_workflow.add_argument("--db", required=True)
    qg_workflow.add_argument("--payload-json", required=True)

    execution_session = subparsers.add_parser("execution-session")
    execution_session.add_argument("--db", required=True)
    execution_session.add_argument("--payload-json", required=True)

    scan_jobs = subparsers.add_parser("scan-jobs")
    scan_jobs.add_argument("--db", required=True)
    scan_jobs.add_argument("--project-id", required=True)
    scan_jobs.add_argument("--run-id")

    scan_job_progress = subparsers.add_parser("scan-job-progress")
    scan_job_progress.add_argument("--db", required=True)
    scan_job_progress.add_argument("--scan-job-id", required=True)
    scan_job_progress.add_argument("--status")
    scan_job_progress.add_argument("--files-total", type=int)
    scan_job_progress.add_argument("--files-scanned", type=int)
    scan_job_progress.add_argument("--files-skipped", type=int)
    scan_job_progress.add_argument("--error-message")

    plugin_execution = subparsers.add_parser("plugin-execution")
    plugin_execution.add_argument("--db", required=True)
    plugin_execution.add_argument("--payload-json", required=True)

    plugin_executions = subparsers.add_parser("plugin-executions")
    plugin_executions.add_argument("--db", required=True)
    plugin_executions.add_argument("--project-id", required=True)
    plugin_executions.add_argument("--run-id")
    plugin_executions.add_argument("--scan-job-id")

    file_inventory = subparsers.add_parser("file-inventory")
    file_inventory.add_argument("--project", required=True)
    file_inventory.add_argument("--max-files", type=int, default=200)

    file_check = subparsers.add_parser("file-check")
    file_check.add_argument("--db", required=True)
    file_check.add_argument("--payload-json", required=True)

    file_checks = subparsers.add_parser("file-checks")
    file_checks.add_argument("--db", required=True)
    file_checks.add_argument("--project-id", required=True)
    file_checks.add_argument("--run-id")
    file_checks.add_argument("--scan-job-id")

    audit_event = subparsers.add_parser("audit-event")
    audit_event.add_argument("--db", required=True)
    audit_event.add_argument("--payload-json", required=True)

    audit_events = subparsers.add_parser("audit-events")
    audit_events.add_argument("--db", required=True)
    audit_events.add_argument("--project-id", required=True)
    audit_events.add_argument("--run-id")

    args = parser.parse_args(argv)

    if args.command == "db" and args.db_command == "init":
        initialize_database(args.db)
        return emit(0, {"status": "passed", "db": args.db})
    if args.command == "detect-checks":
        return emit(0, detect_checks(args.path))
    if args.command == "preflight":
        code, payload = preflight(args.project)
        return emit(code, payload)
    if args.command == "project-context":
        code, payload = project_context(args.project, max_files=args.max_files)
        return emit(code, payload)
    if args.command == "qa-gates":
        code, payload = qa_gates(args.db, args.run_id)
        return emit(code, payload)
    if args.command == "memory-delta":
        code, payload = memory_delta(args.db, args.project_id)
        return emit(code, payload)
    if args.command == "report":
        code, payload = report(args.db, args.run_id)
        return emit(code, payload)
    if args.command == "approval-check":
        code, payload = approval_check(
            args.db,
            run_id=args.run_id,
            target_project=args.target_project,
            branch=args.branch,
            path=args.path,
            action=args.action,
        )
        return emit(code, payload)
    if args.command == "resume-cycle":
        code, payload = resume_cycle(
            args.db,
            project_id=args.project_id,
            run_id=args.run_id,
            latest_ref=args.latest_ref,
        )
        return emit(code, payload)
    if args.command == "run-cycle":
        try:
            payload_json = json.loads(args.payload_json)
        except json.JSONDecodeError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_JSON_PAYLOAD", "reason": str(exc)})
        try:
            code, payload = run_autonomous_cycle(args.db, payload_json)
        except ValueError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_CYCLE_PAYLOAD", "reason": str(exc)})
        return emit(code, payload)
    if args.command == "mcp-state":
        try:
            payload_json = json.loads(args.payload_json)
        except json.JSONDecodeError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_JSON_PAYLOAD", "reason": str(exc)})
        try:
            code, payload = call_tool(args.db, args.tool, payload_json)
        except ValueError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_TOOL_PAYLOAD", "reason": str(exc)})
        return emit(code, payload)
    if args.command == "analyze-context":
        try:
            payload_json = load_analysis_payload(input_json=args.input_json, input_file=args.input_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_ANALYSIS_INPUT", "reason": str(exc)})
        code, payload = analyze_context(payload_json)
        return emit(code, payload)
    if args.command == "analyze-to-state":
        try:
            payload_json = load_analysis_payload(input_json=args.input_json, input_file=args.input_file)
            code, payload = analyze_to_state(args.db, payload_json)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_ANALYSIS_STATE_INPUT", "reason": str(exc)})
        return emit(code, payload)
    if args.command == "analysis-contract":
        return emit(0, build_analysis_contract(
            AnalysisContractInput(
                project_id=args.project_id,
                run_id=args.run_id,
                target_project=args.target_project,
                files_to_analyze=args.file,
                validation_commands=args.validation_command,
            )
        ))
    if args.command == "create-tasks":
        code, payload = create_tasks_from_findings(
            args.db,
            project_id=args.project_id,
            run_id=args.run_id,
            max_subtasks_per_parent=args.max_subtasks_per_parent,
        )
        return emit(code, payload)
    if args.command == "task-status":
        code, payload = update_task_status(
            args.db,
            task_id=args.task_id,
            status=args.status,
            increment_attempt=args.increment_attempt,
        )
        return emit(code, payload)
    if args.command == "task-assign":
        code, payload = assign_task_to_agent(args.db, task_id=args.task_id)
        return emit(code, payload)
    if args.command == "next-task":
        return emit(0, {
            "status": "passed",
            "selected_task_for_agent_takeover": next_runnable_task(
                args.db,
                project_id=args.project_id,
                run_id=args.run_id,
            ),
        })
    if args.command == "qg-workflow":
        try:
            payload_json = json.loads(args.payload_json)
        except json.JSONDecodeError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_JSON_PAYLOAD", "reason": str(exc)})
        code, payload = process_quality_gate_payload(args.db, payload_json)
        return emit(code, payload)
    if args.command == "execution-session":
        try:
            payload_json = json.loads(args.payload_json)
            session = parse_execution_session_payload(payload_json)
        except (json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_EXECUTION_SESSION_PAYLOAD", "reason": str(exc)})
        return emit(0, {"status": "passed", "session": record_execution_session(args.db, session)})
    if args.command == "scan-jobs":
        return emit(0, {
            "status": "passed",
            "scan_jobs": list_scan_jobs(args.db, project_id=args.project_id, run_id=args.run_id),
        })
    if args.command == "scan-job-progress":
        try:
            scan_job = update_scan_job_progress(
                args.db,
                scan_job_id=args.scan_job_id,
                status=args.status,
                files_total=args.files_total,
                files_scanned=args.files_scanned,
                files_skipped=args.files_skipped,
                error_message=args.error_message,
            )
        except ValueError as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_SCAN_JOB_STATUS", "reason": str(exc)})
        return emit(0, {"status": "passed", "scan_job": scan_job})
    if args.command == "plugin-execution":
        try:
            payload_json = json.loads(args.payload_json)
            execution = PluginExecutionInput(
                id=_required_payload_value(payload_json, "id"),
                project_id=_required_payload_value(payload_json, "project_id"),
                plugin_name=_required_payload_value(payload_json, "plugin_name"),
                status=_required_payload_value(payload_json, "status"),
                scan_job_id=payload_json.get("scan_job_id"),
                run_id=payload_json.get("run_id"),
                exit_code=payload_json.get("exit_code"),
                stdout_summary=payload_json.get("stdout_summary"),
                stderr_summary=payload_json.get("stderr_summary"),
                metadata=payload_json.get("metadata") if isinstance(payload_json.get("metadata"), dict) else {},
            )
            return emit(0, {"status": "passed", "plugin_execution": record_plugin_execution(args.db, execution)})
        except (json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_PLUGIN_EXECUTION_PAYLOAD", "reason": str(exc)})
    if args.command == "plugin-executions":
        return emit(0, {
            "status": "passed",
            "plugin_executions": list_plugin_executions(
                args.db,
                project_id=args.project_id,
                run_id=args.run_id,
                scan_job_id=args.scan_job_id,
            ),
        })
    if args.command == "file-inventory":
        code, payload = build_file_inventory(args.project, max_files=args.max_files)
        return emit(code, payload)
    if args.command == "file-check":
        try:
            payload_json = json.loads(args.payload_json)
            file_check_payload = FileCheckInput(
                id=_required_payload_value(payload_json, "id"),
                project_id=_required_payload_value(payload_json, "project_id"),
                file_path=_required_payload_value(payload_json, "file_path"),
                status=_required_payload_value(payload_json, "status"),
                scan_job_id=payload_json.get("scan_job_id"),
                run_id=payload_json.get("run_id"),
                content_sha256=payload_json.get("content_sha256"),
                language=payload_json.get("language"),
                checks=payload_json.get("checks") if isinstance(payload_json.get("checks"), list) else [],
                metadata=payload_json.get("metadata") if isinstance(payload_json.get("metadata"), dict) else {},
            )
            return emit(0, {"status": "passed", "file_check": record_file_check(args.db, file_check_payload)})
        except (json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_FILE_CHECK_PAYLOAD", "reason": str(exc)})
    if args.command == "file-checks":
        return emit(0, {
            "status": "passed",
            "file_checks": list_file_checks(
                args.db,
                project_id=args.project_id,
                run_id=args.run_id,
                scan_job_id=args.scan_job_id,
            ),
        })
    if args.command == "audit-event":
        try:
            payload_json = json.loads(args.payload_json)
            event = AuditEventInput(
                id=payload_json.get("id") if isinstance(payload_json.get("id"), str) else None,
                project_id=_required_payload_value(payload_json, "project_id"),
                run_id=payload_json.get("run_id") if isinstance(payload_json.get("run_id"), str) else None,
                event_type=_required_payload_value(payload_json, "event_type"),
                summary=_required_payload_value(payload_json, "summary"),
                payload=payload_json.get("payload") if isinstance(payload_json.get("payload"), dict) else {},
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return emit(2, {"status": "blocked", "blocker_code": "INVALID_AUDIT_EVENT_PAYLOAD", "reason": str(exc)})
        return emit(0, {"status": "passed", "audit_event": append_audit_event(args.db, event)})
    if args.command == "audit-events":
        return emit(0, {
            "status": "passed",
            "audit_events": list_audit_events(args.db, project_id=args.project_id, run_id=args.run_id),
        })

    return emit(2, {"status": "blocked", "blocker_code": "UNKNOWN_COMMAND"})


def emit(exit_code: int, payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return exit_code


def _required_payload_value(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


if __name__ == "__main__":
    sys.exit(main())
