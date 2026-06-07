from __future__ import annotations

import argparse
import json
import sys

from .agent_analysis import analyze_context, load_analysis_payload
from .approvals import approval_check
from .check_detection import detect_checks
from .cycle import resume_cycle
from .db import initialize_database
from .execution_sessions import parse_execution_session_payload, record_execution_session
from .memory import memory_delta
from .mcp_state import call_tool
from .preflight import preflight
from .qg_workflow import process_quality_gate_payload
from .qa_gates import qa_gates
from .reports import report
from .task_creation import create_tasks_from_findings, update_task_status


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

    mcp_state = subparsers.add_parser("mcp-state")
    mcp_state.add_argument("--db", required=True)
    mcp_state.add_argument("--tool", required=True)
    mcp_state.add_argument("--payload-json", default="{}")

    analyze = subparsers.add_parser("analyze-context")
    analyze_input = analyze.add_mutually_exclusive_group(required=True)
    analyze_input.add_argument("--input-json")
    analyze_input.add_argument("--input-file")

    create_tasks = subparsers.add_parser("create-tasks")
    create_tasks.add_argument("--db", required=True)
    create_tasks.add_argument("--project-id", required=True)
    create_tasks.add_argument("--run-id", required=True)
    create_tasks.add_argument("--max-subtasks-per-parent", type=int, default=20)

    task_status = subparsers.add_parser("task-status")
    task_status.add_argument("--db", required=True)
    task_status.add_argument("--task-id", required=True)
    task_status.add_argument("--status", required=True)

    qg_workflow = subparsers.add_parser("qg-workflow")
    qg_workflow.add_argument("--db", required=True)
    qg_workflow.add_argument("--payload-json", required=True)

    execution_session = subparsers.add_parser("execution-session")
    execution_session.add_argument("--db", required=True)
    execution_session.add_argument("--payload-json", required=True)

    args = parser.parse_args(argv)

    if args.command == "db" and args.db_command == "init":
        initialize_database(args.db)
        return emit(0, {"status": "passed", "db": args.db})
    if args.command == "detect-checks":
        return emit(0, detect_checks(args.path))
    if args.command == "preflight":
        code, payload = preflight(args.project)
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
    if args.command == "create-tasks":
        code, payload = create_tasks_from_findings(
            args.db,
            project_id=args.project_id,
            run_id=args.run_id,
            max_subtasks_per_parent=args.max_subtasks_per_parent,
        )
        return emit(code, payload)
    if args.command == "task-status":
        code, payload = update_task_status(args.db, task_id=args.task_id, status=args.status)
        return emit(code, payload)
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

    return emit(2, {"status": "blocked", "blocker_code": "UNKNOWN_COMMAND"})


def emit(exit_code: int, payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
