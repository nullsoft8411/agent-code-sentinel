#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Any

from code_sentinel_agent.mcp_state import call_tool
from code_sentinel_agent.postgres_state import initialize_postgres_database, run_psql_json


PROJECT_ID = "proj-postgres-live-e2e"
RUN_ID = "run-postgres-live-e2e"
TASK_ID = "task-postgres-live-e2e"
TARGET_PROJECT = "nullsoft8411/agent-code-sentinel"
MODIFIED_FILE = "src/app.py"


def main() -> int:
    dsn = os.environ.get("CODE_SENTINEL_POSTGRES_DSN", "").strip()
    if not dsn:
        return e2e_emit(
            2,
            {
                "status": "blocked",
                "blocker_code": "POSTGRES_DSN_MISSING",
                "reason": "CODE_SENTINEL_POSTGRES_DSN is required for live Postgres E2E",
                "script_execution_mode": "python_executed_from_cloned_repo",
                "live_postgres_e2e": False,
            },
        )

    init_code, init_payload = initialize_postgres_database(dsn)
    if init_code != 0:
        return e2e_emit(2, e2e_blocked("POSTGRES_INIT_FAILED", init_payload))

    seed_code, seed_payload = seed_state(dsn)
    if seed_code != 0:
        return e2e_emit(2, e2e_blocked("POSTGRES_SEED_FAILED", seed_payload))

    approval_code, approval_payload = call_tool(
        dsn,
        "state_approval_record",
        {
            "id": "approval-postgres-live-e2e",
            "run_id": RUN_ID,
            "target_project": TARGET_PROJECT,
            "branch": "main",
            "allowed_paths": [MODIFIED_FILE],
            "allowed_actions": ["file_write"],
            "approved_by": "operator",
            "approval_evidence": "explicit live Postgres E2E approval without secrets",
        },
    )
    if approval_code != 0:
        return e2e_emit(2, e2e_blocked("APPROVAL_RECORD_FAILED", approval_payload))

    task_code, task_payload = call_tool(
        dsn,
        "state_task_execution_result",
        {
            "project_id": PROJECT_ID,
            "run_id": RUN_ID,
            "write_request": {
                "target_project": TARGET_PROJECT,
                "branch": "main",
                "path": MODIFIED_FILE,
                "action": "file_write",
            },
            "task_execution_result": {
                "task_id": TASK_ID,
                "execution_method": "agent_native_python",
                "script_name": "postgres-task-execution-e2e",
                "files_modified": [MODIFIED_FILE],
                "validation_result": {
                    "gate": "postgres_live_task_execution",
                    "command": "PYTHONPATH=src python3 -m pytest tests/test_mcp_state.py -q",
                    "exit_code": 0,
                    "stdout": "live postgres task execution e2e passed",
                    "stderr": "",
                },
            },
        },
    )
    if task_code != 0:
        return e2e_emit(2, e2e_blocked("TASK_EXECUTION_RESULT_FAILED", task_payload))

    report_code, report_payload = call_tool(dsn, "state_report_get", {"run_id": RUN_ID})
    if report_code != 0:
        return e2e_emit(2, e2e_blocked("REPORT_READBACK_FAILED", report_payload))

    return e2e_emit(
        0,
        {
            "status": "postgres_task_execution_e2e_passed",
            "script_execution_mode": "python_executed_from_cloned_repo",
            "live_postgres_e2e": True,
            "project_id": PROJECT_ID,
            "run_id": RUN_ID,
            "task_id": TASK_ID,
            "approval_recorded": approval_payload.get("status") == "passed",
            "task_execution_status": task_payload.get("status"),
            "task_status_after_execution": nested(task_payload, "task", "status"),
            "approval_enforced": task_payload.get("approval_enforced"),
            "validation_attempts_count": nested(report_payload, "counts", "validation_attempts"),
            "execution_sessions_count": nested(report_payload, "counts", "execution_sessions"),
            "write_actions": ["state_approval_record", "state_task_execution_result"],
            "unapproved_actions": [],
        },
    )


def seed_state(dsn: str) -> tuple[int, dict[str, Any]]:
    sql = f"""
insert into projects(id, target, default_branch, status)
values ('{PROJECT_ID}', '{TARGET_PROJECT}', 'main', 'active')
on conflict(id) do update set
  target = excluded.target,
  default_branch = excluded.default_branch,
  status = excluded.status,
  updated_at = now();

insert into runs(id, project_id, status, current_focus, next_autonomous_step)
values ('{RUN_ID}', '{PROJECT_ID}', 'in_progress', 'task_takeover', 'record direct task execution result')
on conflict(id) do update set
  project_id = excluded.project_id,
  status = excluded.status,
  current_focus = excluded.current_focus,
  next_autonomous_step = excluded.next_autonomous_step,
  updated_at = now();

insert into findings(id, run_id, project_id, signature, category, severity, file_path, title, details, status)
values (
  'finding-postgres-live-e2e',
  '{RUN_ID}',
  '{PROJECT_ID}',
  'finding:postgres-live-e2e',
  'e2e',
  'medium',
  '{MODIFIED_FILE}',
  'Live Postgres direct task execution E2E',
  'Seed finding for direct task execution result E2E',
  'open'
)
on conflict(project_id, signature) do update set
  run_id = excluded.run_id,
  file_path = excluded.file_path,
  title = excluded.title,
  details = excluded.details,
  status = excluded.status;

insert into tasks(
  id, finding_id, run_id, project_id, status, priority, title,
  affected_file, task_type, task_signature, progress_json
)
values (
  '{TASK_ID}',
  'finding-postgres-live-e2e',
  '{RUN_ID}',
  '{PROJECT_ID}',
  'assigned_to_agent',
  50,
  'Complete live Postgres direct task execution E2E',
  '{MODIFIED_FILE}',
  'standalone',
  'task:postgres-live-e2e',
  '{{}}'::jsonb
)
on conflict(id) do update set
  finding_id = excluded.finding_id,
  run_id = excluded.run_id,
  project_id = excluded.project_id,
  status = excluded.status,
  affected_file = excluded.affected_file,
  updated_at = now();

select json_build_object(
  'status', 'passed',
  'project_id', '{PROJECT_ID}',
  'run_id', '{RUN_ID}',
  'task_id', '{TASK_ID}'
);
"""
    return run_psql_json(dsn, sql)


def nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def e2e_blocked(blocker_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "blocker_code": blocker_code,
        "script_execution_mode": "python_executed_from_cloned_repo",
        "live_postgres_e2e": False,
        "detail": payload,
    }


def e2e_emit(code: int, payload: dict[str, Any]) -> int:
    print(json.dumps(payload, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
