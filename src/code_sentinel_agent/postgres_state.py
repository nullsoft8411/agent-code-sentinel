from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from .agent_analysis import analyze_context
from .file_inventory import build_file_inventory
from .project_context import project_context
from .validation_runner import ValidationResult, normalize_validation_payload


POSTGRES_MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "postgres" / "001_mcp_state.sql"


@dataclass(frozen=True)
class PostgresCommand:
    command: list[str]
    env: dict[str, str]


def psql_available() -> bool:
    return shutil.which("psql") is not None


def build_psql_command(dsn: str) -> PostgresCommand:
    env = os.environ.copy()
    env.update(parse_pg_env(dsn))
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    return PostgresCommand(
        command=[
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
        ],
        env=env,
    )


def parse_pg_env(dsn: str) -> dict[str, str]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("Postgres DSN must start with postgresql:// or postgres://")
    database = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
    env = {
        "PGHOST": parsed.hostname or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(database or "postgres"),
    }
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    return env


def run_psql_json(dsn: str, sql: str) -> tuple[int, dict]:
    if not psql_available():
        return 2, {
            "status": "blocked",
            "blocker_code": "PSQL_CLIENT_MISSING",
            "state_backend": "postgres",
            "reason": "psql client is required for the dependency-free Postgres backend",
        }
    command = build_psql_command(dsn)
    try:
        result = subprocess.run(
            command.command,
            input=sql,
            text=True,
            capture_output=True,
            timeout=15,
            env=command.env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 2, {
            "status": "blocked",
            "blocker_code": "POSTGRES_QUERY_TIMEOUT",
            "state_backend": "postgres",
            "reason": "psql query exceeded timeout",
        }

    if result.returncode != 0:
        return 2, {
            "status": "blocked",
            "blocker_code": "POSTGRES_QUERY_FAILED",
            "state_backend": "postgres",
            "reason": summarize_stderr(result.stderr),
        }

    output = result.stdout.strip()
    if not output:
        return 0, {"status": "passed", "state_backend": "postgres"}
    try:
        return 0, json.loads(output.splitlines()[-1])
    except json.JSONDecodeError as exc:
        return 2, {
            "status": "blocked",
            "blocker_code": "POSTGRES_JSON_PARSE_FAILED",
            "state_backend": "postgres",
            "reason": str(exc),
        }


def initialize_postgres_database(dsn: str) -> tuple[int, dict]:
    migration_sql = POSTGRES_MIGRATION.read_text(encoding="utf-8")
    sql = migration_sql + "\nselect json_build_object('status','passed','state_backend','postgres','migration','001_mcp_state');\n"
    return run_psql_json(dsn, sql)


def postgres_project_get(dsn: str, project_id: str) -> tuple[int, dict]:
    sql = """
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'project', json_build_object(
      'id', id,
      'target', target,
      'default_branch', default_branch,
      'status', status
    )
  )
  from projects
  where id = :project_id),
  json_build_object(
    'status', 'blocked',
    'blocker_code', 'PROJECT_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'initialize project state before exposing it through MCP'
  )
);
"""
    return run_psql_json(dsn, bind_literal(sql, "project_id", project_id))


def postgres_memory_get(dsn: str, project_id: str) -> tuple[int, dict]:
    sql = """
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'memory', memory_json,
    'latest_ref', latest_ref,
    'stale_memory_decision', coalesce(stale_memory_decision, 'not_applicable'),
    'next_autonomous_step', coalesce(nullif(memory_json->>'next_autonomous_step', ''), 'run repository preflight')
  )
  from memories
  where project_id = :project_id
  order by created_at desc
  limit 1),
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'memory', null,
    'latest_ref', null,
    'stale_memory_decision', 'missing_memory',
    'next_autonomous_step', 'run repository preflight'
  )
  where exists (select 1 from projects where id = :project_id)),
  json_build_object(
    'status', 'blocked',
    'blocker_code', 'PROJECT_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'initialize project state before reading memory'
  )
);
"""
    return run_psql_json(dsn, bind_literal(sql, "project_id", project_id))


def postgres_report_get(dsn: str, run_id: str) -> tuple[int, dict]:
    sql = """
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'partial_report', true,
    'run', json_build_object(
      'id', runs.id,
      'status', runs.status,
      'current_focus', runs.current_focus,
      'next_autonomous_step', runs.next_autonomous_step
    ),
    'project', json_build_object(
      'id', projects.id,
      'target', projects.target
    ),
    'counts', json_build_object(
      'qa_gates', (select count(*) from qa_gate_results where run_id = :run_id),
      'artifacts', (select count(*) from artifacts where run_id = :run_id),
      'findings', (select count(*) from findings where run_id = :run_id),
      'tasks', (select count(*) from tasks where run_id = :run_id),
      'validation_attempts', (select count(*) from validation_attempts where run_id = :run_id),
      'execution_sessions', (select count(*) from agent_execution_sessions where run_id = :run_id)
    ),
    'unsupported_counts', json_build_array(),
    'qa_review_outcomes', json_build_array(),
    'missing_review_outcomes', json_build_array(),
    'execution_sessions', coalesce(
      (select json_agg(json_build_object(
        'id', id,
        'task_id', task_id,
        'session_type', session_type,
        'script_name', script_name,
        'execution_method', execution_method,
        'status', status,
        'output_json', output_json,
        'attempt_log_json', attempt_log_json
      ) order by created_at)
      from agent_execution_sessions
      where run_id = :run_id),
      json_build_array()
    ),
    'selected_task_for_agent_takeover', null
  )
  from runs
  join projects on projects.id = runs.project_id
  where runs.id = :run_id),
  json_build_object(
    'status', 'blocked',
    'blocker_code', 'RUN_NOT_FOUND',
    'state_backend', 'postgres',
    'run_id', :run_id,
    'reason', 'initialize the run before requesting a report'
  )
);
"""
    return run_psql_json(dsn, bind_literal(sql, "run_id", run_id))


def postgres_analyze_to_state(dsn: str, payload: dict[str, Any]) -> tuple[int, dict]:
    code, analysis = analyze_context(payload)
    if code != 0:
        return code, analysis | {"state_backend": "postgres"}

    project_id = str(payload.get("project_id") or analysis.get("project_id") or "").strip()
    run_id = str(payload.get("run_id") or analysis.get("run_id") or "").strip()
    if not project_id or not run_id:
        return 2, {
            "status": "blocked",
            "blocker_code": "INVALID_ANALYSIS_INPUT",
            "state_backend": "postgres",
            "reason": "project_id and run_id are required",
        }

    scan_job_id = str(payload.get("scan_job_id") or stable_id("analysis-scan", project_id, run_id))
    findings = [
        {
            "id": stable_id("analysis-finding", run_id, finding["signature"]),
            "compatibility_finding_id": "compat-" + stable_id("analysis-finding", run_id, finding["signature"]),
            "signature": str(finding["signature"]),
            "category": str(finding.get("category") or finding.get("rule_id") or "agent_analysis"),
            "scanner_name": str(finding.get("source") or "agent_analysis"),
            "rule_id": str(finding.get("rule_id") or "agent_analysis"),
            "severity": str(finding["severity"]),
            "title": str(finding["title"]),
            "file_path": str(finding.get("file_path") or "project"),
            "line_number": finding.get("line_number") if isinstance(finding.get("line_number"), int) else None,
            "status": str(finding.get("status") or "open"),
            "message": str(finding.get("description") or ""),
            "evidence": str(finding.get("evidence") or ""),
            "metadata": {
                "source": "analyze_to_state",
                "output_contract_version": analysis["output_contract_version"],
            },
            "task_id": stable_id("task", "standalone:" + str(finding["signature"])),
            "task_signature": "standalone:" + str(finding["signature"]),
            "task_priority": severity_priority(str(finding["severity"])),
        }
        for finding in analysis["findings"]
    ]
    analysis_json = json.dumps(analysis, sort_keys=True)
    findings_json = json.dumps(findings, sort_keys=True)
    latest_ref = str(payload.get("latest_ref") or "")
    sql = """
begin;
select case when pg_try_advisory_xact_lock(hashtext(:project_id))
then json_build_object('advisory_lock', 'taken')
else json_build_object(
  'status', 'blocked',
  'blocker_code', 'STATE_ADVISORY_LOCK_HELD',
  'state_backend', 'postgres',
  'project_id', :project_id,
  'reason', 'another transaction owns the project advisory lock'
) end;
insert into scan_jobs(
  id, run_id, project_id, scanner_name, scan_type, status, target_ref,
  files_total, files_scanned, findings_count, metadata_json, completed_at, updated_at
)
select
  :scan_job_id,
  :run_id,
  :project_id,
  'agent_analysis',
  'agent_native_analysis',
  'completed',
  nullif(:latest_ref, ''),
  coalesce((select count(distinct value->>'file_path') from jsonb_array_elements(:findings_json::jsonb) value), 0),
  coalesce((select count(distinct value->>'file_path') from jsonb_array_elements(:findings_json::jsonb) value), 0),
  coalesce(jsonb_array_length(:findings_json::jsonb), 0),
  jsonb_build_object('source', 'analyze_to_state'),
  now(),
  now()
where exists (select 1 from projects where id = :project_id)
  and exists (select 1 from runs where id = :run_id and project_id = :project_id)
on conflict (id) do update set
  status = excluded.status,
  target_ref = excluded.target_ref,
  files_total = excluded.files_total,
  files_scanned = excluded.files_scanned,
  findings_count = excluded.findings_count,
  completed_at = now(),
  updated_at = now();
with input_findings as (
  select value
  from jsonb_array_elements(:findings_json::jsonb) value
),
compat_inserted as (
  insert into findings(
    id, run_id, project_id, signature, category, severity,
    file_path, line_number, title, details, status
  )
  select
    value->>'compatibility_finding_id',
    :run_id,
    :project_id,
    value->>'signature',
    value->>'category',
    value->>'severity',
    nullif(value->>'file_path', ''),
    nullif(value->>'line_number', '')::integer,
    value->>'title',
    value->>'message',
    value->>'status'
  from input_findings
  where exists (select 1 from scan_jobs where id = :scan_job_id)
  on conflict (project_id, signature) do nothing
  returning id, signature
),
scan_inserted as (
  insert into scan_findings(
    id, scan_job_id, run_id, project_id, compatibility_finding_id,
    scanner_name, rule_id, file_path, line_number, severity, status,
    title, message, evidence, signature, metadata_json
  )
  select
    value->>'id',
    :scan_job_id,
    :run_id,
    :project_id,
    value->>'compatibility_finding_id',
    value->>'scanner_name',
    value->>'rule_id',
    nullif(value->>'file_path', ''),
    nullif(value->>'line_number', '')::integer,
    value->>'severity',
    value->>'status',
    value->>'title',
    value->>'message',
    value->>'evidence',
    value->>'signature',
    value->'metadata'
  from input_findings
  where exists (select 1 from scan_jobs where id = :scan_job_id)
  on conflict (project_id, signature) do nothing
  returning id, signature
),
task_inserted as (
  insert into tasks(
    id, finding_id, run_id, project_id, status, priority, title,
    affected_file, task_type, task_signature, progress_json
  )
  select
    value->>'task_id',
    findings.id,
    :run_id,
    :project_id,
    'pending',
    (value->>'task_priority')::integer,
    'Fix ' || (value->>'title'),
    nullif(value->>'file_path', ''),
    'standalone',
    value->>'task_signature',
    '{}'::jsonb
  from input_findings
  join findings on findings.project_id = :project_id and findings.signature = value->>'signature'
  where findings.run_id = :run_id
  on conflict (project_id, task_signature) do nothing
  returning id
),
selected_task as (
  select *
  from tasks
  where project_id = :project_id
    and run_id = :run_id
    and task_type != 'parent'
    and status in ('pending', 'assigned_to_agent', 'in_progress', 'failed_validation')
    and attempt_count < max_attempts
  order by
    case status
      when 'in_progress' then 1
      when 'assigned_to_agent' then 2
      when 'failed_validation' then 3
      else 4
    end,
    priority desc,
    subtask_order,
    created_at,
    id
  limit 1
)
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'mode', 'agent_supplied_analysis_persistence',
    'agent_execution_boundary', 'The Workspace Agent performs reasoning and supplies finding_candidates; this adapter normalizes, redacts, persists, creates tasks, and reports state.',
    'project_id', :project_id,
    'run_id', :run_id,
    'analysis', :analysis_json::jsonb,
    'scan_job', json_build_object(
      'id', scan_jobs.id,
      'status', scan_jobs.status,
      'findings_count', scan_jobs.findings_count
    ),
    'counts', json_build_object(
      'analysis_findings', coalesce(jsonb_array_length(:findings_json::jsonb), 0),
      'persisted_findings', (select count(*) from scan_findings where run_id = :run_id and project_id = :project_id),
      'tasks', (select count(*) from tasks where run_id = :run_id and project_id = :project_id),
      'created_tasks', (select count(*) from task_inserted)
    ),
    'selected_task_for_agent_takeover',
      (select json_build_object(
        'id', id,
        'finding_id', finding_id,
        'run_id', run_id,
        'project_id', project_id,
        'status', status,
        'priority', priority,
        'title', title,
        'affected_file', affected_file,
        'task_type', task_type,
        'task_signature', task_signature,
        'progress', progress_json
      ) from selected_task),
    'next_autonomous_step', coalesce(
      (select 'take over ' || task_type || ' ' || id || ': ' || title from selected_task),
      'review analysis findings and create a bounded fix queue'
    )
  )
  from scan_jobs
  where scan_jobs.id = :scan_job_id),
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'RUN_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'run_id', :run_id,
    'reason', 'initialize project and run state before persisting analysis'
  )
  where exists (select 1 from projects where id = :project_id)),
  json_build_object(
    'status', 'blocked',
    'blocker_code', 'PROJECT_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'initialize project state before persisting analysis'
  )
);
commit;
"""
    return run_psql_json(
        dsn,
        bind_literals(
            sql,
            {
                "project_id": project_id,
                "run_id": run_id,
                "scan_job_id": scan_job_id,
                "latest_ref": latest_ref,
                "findings_json": findings_json,
                "analysis_json": analysis_json,
            },
        ),
    )


def postgres_run_cycle(dsn: str, payload: dict[str, Any]) -> tuple[int, dict]:
    unsupported_inputs = [
        key
        for key in ("task_execution_result", "write_request")
        if payload.get(key)
    ]
    if unsupported_inputs:
        return 2, {
            "status": "blocked",
            "blocker_code": "POSTGRES_RUN_CYCLE_ADVANCED_INPUT_NOT_IMPLEMENTED",
            "state_backend": "postgres",
            "unsupported_inputs": unsupported_inputs,
            "reason": "Postgres run-cycle currently covers Agent-owned state takeover only; project evidence, validation, task execution and write approvals are separate migration slices",
            "next_action": "run state_analyze_to_state first, then call state_run_cycle without advanced execution payloads",
        }

    project_id = str(payload.get("project_id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    latest_ref = str(payload.get("latest_ref") or "current ref")
    owner = str(payload.get("owner") or "workspace-agent-cycle")
    if not project_id or not run_id:
        return 2, {
            "status": "blocked",
            "blocker_code": "INVALID_RUN_CYCLE_INPUT",
            "state_backend": "postgres",
            "reason": "project_id and run_id are required",
        }

    project_evidence = postgres_project_evidence(payload, project_id=project_id, run_id=run_id, latest_ref=latest_ref)
    validation_code, validation_state = postgres_validation_state(payload, project_id=project_id, run_id=run_id)
    if validation_code != 0:
        return validation_code, validation_state
    scan_job = project_evidence.get("scan_job") if isinstance(project_evidence.get("scan_job"), dict) else {}
    validation_result = validation_state.get("validation_result")
    cycle_status = (
        "blocking"
        if project_evidence.get("status") == "blocking"
        or (isinstance(validation_result, dict) and validation_result.get("status") == "blocking")
        else "passed"
    )
    project_evidence_next_step = (
        f"resolve project evidence blocker: {project_evidence.get('blocker_code')}"
        if project_evidence.get("status") == "blocking"
        else "create or continue the selected task for the failing gate"
        if isinstance(validation_result, dict) and validation_result.get("status") == "blocking"
        else "analyze project context and create findings/tasks before editing"
    )
    plugin_executions = project_evidence.get("plugin_executions") if isinstance(project_evidence.get("plugin_executions"), list) else []
    file_checks = project_evidence.get("file_checks") if isinstance(project_evidence.get("file_checks"), list) else []
    project_evidence_json = json.dumps(project_evidence, sort_keys=True)
    plugin_executions_json = json.dumps(plugin_executions, sort_keys=True)
    file_checks_json = json.dumps(file_checks, sort_keys=True)
    validation_result_json = json.dumps(validation_result, sort_keys=True)
    validation_findings_json = json.dumps(validation_state.get("findings") or [], sort_keys=True)

    sql = """
begin;
select case when pg_try_advisory_xact_lock(hashtext(:project_id))
then json_build_object('advisory_lock', 'taken')
else json_build_object(
  'status', 'blocked',
  'blocker_code', 'STATE_ADVISORY_LOCK_HELD',
  'state_backend', 'postgres',
  'project_id', :project_id,
  'reason', 'another transaction owns the project advisory lock'
) end;
update state_locks
set status = 'expired', released_at = now(), updated_at = now()
where status = 'active' and released_at is null and expires_at is not null and expires_at <= now();
insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at)
select :lock_id, :project_id, :run_id, :owner, 'active', now(), now() + interval '15 minutes'
where exists (select 1 from projects where id = :project_id)
  and not exists (
    select 1 from state_locks
    where project_id = :project_id
      and status = 'active'
      and released_at is null
      and (run_id <> :run_id or owner <> :owner)
  )
on conflict (id) do update set
  status = 'active',
  acquired_at = now(),
  expires_at = now() + interval '15 minutes',
  released_at = null,
  updated_at = now();
insert into runs(id, project_id, status, current_focus, next_autonomous_step)
select
  :run_id,
  :project_id,
  'in_progress',
  'autonomous_cycle',
  'preflight ' || :latest_ref || ' before continuing: ' || coalesce(
    (select nullif(memory_json->>'next_autonomous_step', '')
     from memories
     where project_id = :project_id
     order by created_at desc
     limit 1),
    'run repository preflight'
  )
where exists (select 1 from projects where id = :project_id)
  and exists (
    select 1 from state_locks
    where project_id = :project_id
      and run_id = :run_id
      and owner = :owner
      and status = 'active'
      and released_at is null
  )
on conflict (id) do nothing;
with scan_job_upsert as (
  insert into scan_jobs(
    id, run_id, project_id, scanner_name, scan_type, status, target_ref,
    files_total, files_scanned, files_skipped, findings_count, error_message,
    metadata_json, completed_at, updated_at
  )
  select
    :scan_job_id,
    :run_id,
    :project_id,
    'agent_autonomous_cycle',
    'project_context',
    :scan_job_status,
    :latest_ref,
    (:files_total)::integer,
    (:files_scanned)::integer,
    (:files_skipped)::integer,
    0,
    nullif(:scan_error_message, ''),
    jsonb_build_object('project_path', nullif(:project_path, ''), 'source', 'run-cycle'),
    now(),
    now()
  where nullif(:scan_job_id, '') is not null
    and exists (select 1 from runs where id = :run_id and project_id = :project_id)
  on conflict (id) do update set
    status = excluded.status,
    target_ref = excluded.target_ref,
    files_total = excluded.files_total,
    files_scanned = excluded.files_scanned,
    files_skipped = excluded.files_skipped,
    error_message = excluded.error_message,
    metadata_json = excluded.metadata_json,
    completed_at = excluded.completed_at,
    updated_at = now()
  returning id
),
plugin_input as (
  select value
  from jsonb_array_elements(:plugin_executions_json::jsonb) value
),
plugin_insert as (
  insert into plugin_executions(
    id, scan_job_id, run_id, project_id, plugin_name, status, completed_at,
    exit_code, stdout_summary, stderr_summary, metadata_json
  )
  select
    value->>'id',
    nullif(value->>'scan_job_id', ''),
    :run_id,
    :project_id,
    value->>'plugin_name',
    value->>'status',
    now(),
    nullif(value->>'exit_code', '')::integer,
    value->>'stdout_summary',
    value->>'stderr_summary',
    value->'metadata'
  from plugin_input
  where exists (select 1 from runs where id = :run_id and project_id = :project_id)
  on conflict (id) do update set
    status = excluded.status,
    completed_at = excluded.completed_at,
    exit_code = excluded.exit_code,
    stdout_summary = excluded.stdout_summary,
    stderr_summary = excluded.stderr_summary,
    metadata_json = excluded.metadata_json,
    updated_at = now()
  returning id
),
file_check_input as (
  select value
  from jsonb_array_elements(:file_checks_json::jsonb) value
),
file_check_insert as (
  insert into file_checks(
    id, scan_job_id, run_id, project_id, file_path, content_sha256,
    language, status, checks_json, metadata_json
  )
  select
    value->>'id',
    value->>'scan_job_id',
    :run_id,
    :project_id,
    value->>'file_path',
    value->>'content_sha256',
    value->>'language',
    value->>'status',
    value->'checks',
    value->'metadata'
  from file_check_input
  where exists (select 1 from runs where id = :run_id and project_id = :project_id)
  on conflict (project_id, scan_job_id, file_path) do update set
    content_sha256 = excluded.content_sha256,
    language = excluded.language,
    status = excluded.status,
    checks_json = excluded.checks_json,
    metadata_json = excluded.metadata_json,
    updated_at = now()
  returning id
),
validation_attempt_insert as (
  insert into validation_attempts(
    id, run_id, command, cwd, exit_code, stdout_summary, stderr_summary, status
  )
  select
    :validation_attempt_id,
    :run_id,
    :validation_command,
    nullif(:validation_cwd, ''),
    (:validation_exit_code)::integer,
    :validation_stdout_summary,
    :validation_stderr_summary,
    :validation_status
  where :validation_present = 'true'
    and exists (select 1 from runs where id = :run_id and project_id = :project_id)
  on conflict (id) do update set
    command = excluded.command,
    cwd = excluded.cwd,
    exit_code = excluded.exit_code,
    stdout_summary = excluded.stdout_summary,
    stderr_summary = excluded.stderr_summary,
    status = excluded.status
  returning id
),
qa_gate_insert as (
  insert into qa_gate_results(gate, run_id, status, evidence, why_it_matters, next_action, id)
  select
    :validation_gate,
    :run_id,
    :validation_status,
    :validation_evidence,
    'failed validation must become tracked findings/tasks before autonomous completion',
    'create or continue the selected task for the failing gate',
    :qa_gate_id
  where :validation_present = 'true'
    and exists (select 1 from runs where id = :run_id and project_id = :project_id)
  on conflict(run_id, gate) do update set
    status = excluded.status,
    evidence = excluded.evidence,
    why_it_matters = excluded.why_it_matters,
    next_action = excluded.next_action
  returning id
),
validation_session_insert as (
  insert into agent_execution_sessions(
    id, run_id, project_id, task_id, session_type, script_name,
    execution_method, command, status, output_json, error_summary,
    files_modified_json, attempt_log_json, completed_at
  )
  select
    'exec-' || :validation_attempt_id,
    :run_id,
    :project_id,
    null,
    'validation',
    'qg-workflow',
    'agent_runtime_cli',
    :validation_command,
    :validation_status,
    jsonb_build_object(
      'gate', :validation_gate,
      'exit_code', (:validation_exit_code)::integer,
      'stdout_summary', :validation_stdout_summary,
      'stderr_summary', :validation_stderr_summary
    ),
    nullif(:validation_error_summary, ''),
    '[]'::jsonb,
    jsonb_build_array(jsonb_build_object('step', 'validation_result_normalized', 'status', :validation_status, 'evidence', :validation_evidence)),
    now()
  where :validation_present = 'true'
    and exists (select 1 from validation_attempt_insert)
  on conflict (id) do update set
    status = excluded.status,
    output_json = excluded.output_json,
    error_summary = excluded.error_summary,
    attempt_log_json = excluded.attempt_log_json,
    completed_at = excluded.completed_at,
    updated_at = now()
  returning id
),
validation_finding_input as (
  select value
  from jsonb_array_elements(:validation_findings_json::jsonb) value
),
validation_finding_insert as (
  insert into findings(
    id, run_id, project_id, signature, category, severity,
    file_path, line_number, title, details, status
  )
  select
    value->>'compatibility_finding_id',
    :run_id,
    :project_id,
    value->>'signature',
    value->>'rule_id',
    value->>'severity',
    nullif(value->>'file_path', ''),
    nullif(value->>'line_number', '')::integer,
    value->>'title',
    value->>'message',
    'open'
  from validation_finding_input
  where exists (select 1 from validation_attempt_insert)
  on conflict (project_id, signature) do nothing
  returning id, signature
),
validation_scan_finding_insert as (
  insert into scan_findings(
    id, scan_job_id, run_id, project_id, compatibility_finding_id,
    scanner_name, rule_id, file_path, line_number, severity, status,
    title, message, evidence, signature, metadata_json
  )
  select
    value->>'id',
    null,
    :run_id,
    :project_id,
    value->>'compatibility_finding_id',
    'validation_runner',
    value->>'rule_id',
    nullif(value->>'file_path', ''),
    nullif(value->>'line_number', '')::integer,
    value->>'severity',
    'open',
    value->>'title',
    value->>'message',
    value->>'evidence',
    value->>'signature',
    jsonb_build_object('gate', :validation_gate, 'command', :validation_command)
  from validation_finding_input
  where exists (select 1 from validation_attempt_insert)
  on conflict (project_id, signature) do nothing
  returning id, signature
),
validation_task_insert as (
  insert into tasks(
    id, finding_id, run_id, project_id, status, priority, title,
    affected_file, task_type, task_signature, progress_json
  )
  select
    value->>'task_id',
    findings.id,
    :run_id,
    :project_id,
    'pending',
    (value->>'task_priority')::integer,
    'Fix ' || (value->>'title'),
    nullif(value->>'file_path', ''),
    'standalone',
    value->>'task_signature',
    '{}'::jsonb
  from validation_finding_input
  join findings on findings.project_id = :project_id and findings.signature = value->>'signature'
  where exists (select 1 from validation_attempt_insert)
  on conflict (project_id, task_signature) do nothing
  returning id
),
selected_task as (
  select *
  from tasks
  where project_id = :project_id
    and run_id = :run_id
    and task_type != 'parent'
    and status in ('pending', 'assigned_to_agent', 'in_progress', 'failed_validation')
    and attempt_count < max_attempts
  order by
    case status
      when 'in_progress' then 1
      when 'assigned_to_agent' then 2
      when 'failed_validation' then 3
      else 4
    end,
    priority desc,
    subtask_order,
    created_at,
    id
  limit 1
),
run_update as (
  update runs
  set
    current_focus = case
      when :project_evidence_status = 'blocking' then 'project_evidence'
      when :validation_status = 'blocking' then 'qa_gate_takeover'
      when exists (select 1 from selected_task) then 'task_takeover'
      else 'analysis'
    end,
    next_autonomous_step = case
      when :cycle_status = 'blocking' then :project_evidence_next_step
      else coalesce(
      (select 'take over ' || task_type || ' ' || id || ': ' || title from selected_task),
      'analyze project context and create findings/tasks before editing'
      )
    end,
    updated_at = now()
  where id = :run_id
    and exists (
      select 1 from state_locks
      where project_id = :project_id
        and run_id = :run_id
        and owner = :owner
        and status = 'active'
        and released_at is null
    )
  returning id
),
session_insert as (
  insert into agent_execution_sessions(
    id, run_id, project_id, task_id, session_type, script_name,
    execution_method, command, status, output_json, files_modified_json,
    attempt_log_json, completed_at
  )
  select
    'cycle-' || :run_id,
    :run_id,
    :project_id,
    (select id from selected_task),
    'autonomous_cycle',
    'run-cycle',
    'agent_runtime_cli',
    'cs-agent run-cycle',
    :cycle_status,
    jsonb_build_object(
      'latest_ref', :latest_ref,
      'selected_task_id', (select id from selected_task),
      'project_evidence_status', :project_evidence_status,
      'validation_status', nullif(:validation_status, ''),
      'scan_job_id', nullif(:scan_job_id, ''),
      'file_checks_count', (:file_checks_count)::integer
    ),
    '[]'::jsonb,
    jsonb_build_array(
      jsonb_build_object('step', 'lock_acquired', 'status', 'passed'),
      jsonb_build_object('step', 'run_state_loaded', 'status', 'passed'),
      jsonb_build_object('step', 'project_evidence', 'status', :project_evidence_status),
      jsonb_build_object('step', 'validation_result', 'status', coalesce(nullif(:validation_status, ''), 'not_available')),
      jsonb_build_object('step', 'selected_task', 'status', case when exists (select 1 from selected_task) then 'passed' else 'not_available' end)
    ),
    now()
  where exists (select 1 from run_update)
  on conflict (id) do update set
    task_id = excluded.task_id,
    status = excluded.status,
    output_json = excluded.output_json,
    attempt_log_json = excluded.attempt_log_json,
    completed_at = excluded.completed_at,
    updated_at = now()
  returning id, task_id, status, output_json, attempt_log_json
),
audit_insert as (
  insert into audit_events(id, run_id, project_id, event_type, summary, payload_json)
  select
    'audit-cycle-' || :run_id,
    :run_id,
    :project_id,
    'autonomous_cycle',
    'Cycle exited with status ' || :cycle_status,
    jsonb_build_object(
      'latest_ref', :latest_ref,
      'selected_task_id', (select id from selected_task),
      'current_focus', case when :cycle_status = 'blocking' and :project_evidence_status = 'blocking' then 'project_evidence' when :validation_status = 'blocking' then 'qa_gate_takeover' when exists (select 1 from selected_task) then 'task_takeover' else 'analysis' end,
      'project_evidence_status', :project_evidence_status,
      'validation_status', nullif(:validation_status, ''),
      'scan_job_id', nullif(:scan_job_id, '')
    )
  where exists (select 1 from run_update)
  on conflict (id) do update set
    summary = excluded.summary,
    payload_json = excluded.payload_json
  returning id, event_type, summary, payload_json
),
lock_release as (
  update state_locks
  set status = 'released', released_at = now(), updated_at = now()
  where project_id = :project_id
    and run_id = :run_id
    and owner = :owner
    and status = 'active'
    and released_at is null
  returning id
)
select coalesce(
  (select json_build_object(
    'status', :cycle_status,
    'state_backend', 'postgres',
    'project_id', :project_id,
    'run_id', :run_id,
    'latest_ref', :latest_ref,
    'loaded_memory', (select memory_json from memories where project_id = :project_id order by created_at desc limit 1),
    'stale_memory_decision', coalesce((select stale_memory_decision from memories where project_id = :project_id order by created_at desc limit 1), 'not_applicable'),
    'repository_delta', 'repository truth must be checked before trusting memory',
    'selected_task_for_agent_takeover',
      (select json_build_object(
        'id', id,
        'finding_id', finding_id,
        'run_id', run_id,
        'project_id', project_id,
        'status', status,
        'priority', priority,
        'title', title,
        'affected_file', affected_file,
        'task_type', task_type,
        'task_signature', task_signature,
        'progress', progress_json
      ) from selected_task),
    'validation_result', :validation_result_json::jsonb,
    'task_execution_result', null,
    'project_evidence', :project_evidence_json::jsonb,
    'improvement_work_package', null,
    'cycle_session', (select json_build_object('id', id, 'task_id', task_id, 'status', status, 'output_json', output_json, 'attempt_log_json', attempt_log_json) from session_insert),
    'audit_event', (select json_build_object('id', id, 'event_type', event_type, 'summary', summary, 'payload_json', payload_json) from audit_insert),
    'report', json_build_object(
      'status', 'passed',
      'run', json_build_object('id', runs.id, 'status', runs.status, 'current_focus', runs.current_focus, 'next_autonomous_step', runs.next_autonomous_step),
      'project', json_build_object('id', projects.id, 'target', projects.target),
      'counts', json_build_object(
        'findings', (select count(*) from findings where run_id = :run_id),
        'tasks', (select count(*) from tasks where run_id = :run_id),
        'qa_gates', (select count(*) from qa_gate_results where run_id = :run_id),
        'validation_attempts', (select count(*) from validation_attempts where run_id = :run_id),
        'execution_sessions', (select count(*) from agent_execution_sessions where run_id = :run_id)
      ),
      'plugin_executions', (select count(*) from plugin_executions where run_id = :run_id),
      'file_checks', (select count(*) from file_checks where run_id = :run_id),
      'selected_task_for_agent_takeover', (select json_build_object('id', id, 'title', title, 'affected_file', affected_file, 'task_type', task_type, 'task_signature', task_signature) from selected_task)
    ),
    'next_autonomous_step', runs.next_autonomous_step,
    'lock_released', exists (select 1 from lock_release)
  )
  from runs
  join projects on projects.id = runs.project_id
  where runs.id = :run_id
    and exists (select 1 from run_update)),
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'STATE_LOCK_HELD',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'another run owns the active project state lock',
    'active_lock', json_build_object('id', id, 'run_id', run_id, 'owner', owner, 'status', status)
  )
  from state_locks
  where project_id = :project_id and status = 'active' and released_at is null
  order by acquired_at desc
  limit 1),
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'PROJECT_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'initialize project state before running a cycle'
  ))
);
commit;
"""
    code, result = run_psql_json(
        dsn,
        bind_literals(
            sql,
            {
                "project_id": project_id,
                "run_id": run_id,
                "latest_ref": latest_ref,
                "owner": owner,
                "lock_id": f"{project_id}:{run_id}:{owner}",
                "cycle_status": cycle_status,
                "project_evidence_status": str(project_evidence.get("status") or "not_requested"),
                "project_evidence_next_step": project_evidence_next_step,
                "project_evidence_json": project_evidence_json,
                "project_path": str(payload.get("project_path") or ""),
                "scan_job_id": str(scan_job.get("id") or ""),
                "scan_job_status": str(scan_job.get("status") or ""),
                "scan_error_message": str(scan_job.get("error_message") or ""),
                "files_total": str(scan_job.get("files_total") or 0),
                "files_scanned": str(scan_job.get("files_scanned") or 0),
                "files_skipped": str(scan_job.get("files_skipped") or 0),
                "file_checks_count": str(project_evidence.get("file_checks_count") or 0),
                "plugin_executions_json": plugin_executions_json,
                "file_checks_json": file_checks_json,
                "validation_present": str(bool(validation_result)).lower(),
                "validation_result_json": validation_result_json,
                "validation_findings_json": validation_findings_json,
                "validation_attempt_id": str(validation_state.get("validation_attempt_id") or ""),
                "validation_gate": str(validation_result.get("gate") if isinstance(validation_result, dict) else ""),
                "validation_command": str(validation_result.get("command") if isinstance(validation_result, dict) else ""),
                "validation_cwd": str(validation_result.get("cwd") if isinstance(validation_result, dict) and validation_result.get("cwd") else ""),
                "validation_exit_code": str(validation_result.get("exit_code") if isinstance(validation_result, dict) else 0),
                "validation_stdout_summary": str(validation_result.get("stdout_summary") if isinstance(validation_result, dict) else ""),
                "validation_stderr_summary": str(validation_result.get("stderr_summary") if isinstance(validation_result, dict) else ""),
                "validation_status": str(validation_result.get("status") if isinstance(validation_result, dict) else ""),
                "validation_evidence": str(validation_result.get("evidence") if isinstance(validation_result, dict) else ""),
                "validation_error_summary": (
                    str(validation_result.get("stderr_summary") or "")
                    if isinstance(validation_result, dict) and validation_result.get("status") == "blocking"
                    else ""
                ),
                "qa_gate_id": str(validation_state.get("qa_gate_id") or ""),
            },
        ),
    )
    if code == 0 and result.get("status") == "blocking":
        return 2, result
    return code, result


def postgres_project_evidence(
    payload: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    latest_ref: str,
) -> dict[str, Any]:
    project_path = str(payload.get("project_path") or "").strip()
    if not project_path:
        return {"status": "not_requested", "project_path": None, "file_checks_count": 0}

    scan_job_id = stable_id("scan", run_id, project_path)
    context_code, context_payload = project_context(project_path)
    inventory_code, inventory_payload = build_file_inventory(project_path)
    status = "blocking" if context_code != 0 or inventory_code != 0 else "passed"
    blocker = (
        context_payload.get("blocker_code")
        or inventory_payload.get("blocker_code")
        or ("PROJECT_EVIDENCE_BLOCKED" if status == "blocking" else None)
    )

    plugin_executions = [
        {
            "id": stable_id("plugin", run_id, "project_context"),
            "project_id": project_id,
            "run_id": run_id,
            "scan_job_id": scan_job_id,
            "plugin_name": "project_context",
            "status": "passed" if context_code == 0 else "blocking",
            "exit_code": context_code,
            "stdout_summary": context_payload.get("status"),
            "stderr_summary": context_payload.get("blocker_code"),
            "metadata": {"source": "autonomous_cycle"},
        },
        {
            "id": stable_id("plugin", run_id, "file_inventory"),
            "project_id": project_id,
            "run_id": run_id,
            "scan_job_id": scan_job_id,
            "plugin_name": "file_inventory",
            "status": "passed" if inventory_code == 0 else "blocking",
            "exit_code": inventory_code,
            "stdout_summary": inventory_payload.get("status"),
            "stderr_summary": inventory_payload.get("blocker_code"),
            "metadata": {"source": "autonomous_cycle"},
        },
    ]

    inventory_files = inventory_payload.get("files") if isinstance(inventory_payload.get("files"), list) else []
    file_checks = [
        {
            "id": stable_id("file-check", run_id, scan_job_id, file_record["path"]),
            "project_id": project_id,
            "run_id": run_id,
            "scan_job_id": scan_job_id,
            "file_path": file_record["path"],
            "content_sha256": file_record.get("content_sha256"),
            "language": file_record.get("language"),
            "status": "passed",
            "checks": [{"name": "inventory", "status": "passed"}],
            "metadata": {"bytes": file_record.get("bytes"), "source": "autonomous_cycle"},
        }
        for file_record in inventory_files
        if isinstance(file_record, dict) and file_record.get("path")
    ]
    files_skipped = int(inventory_payload.get("skipped_count") or 0) if inventory_code == 0 else 0
    scan_job = {
        "id": scan_job_id,
        "project_id": project_id,
        "run_id": run_id,
        "scanner_name": "agent_autonomous_cycle",
        "scan_type": "project_context",
        "status": "failed" if status == "blocking" else "completed",
        "target_ref": latest_ref,
        "files_total": len(file_checks) + files_skipped,
        "files_scanned": len(file_checks),
        "files_skipped": files_skipped,
        "error_message": blocker,
        "metadata": {"project_path": project_path, "source": "run-cycle"},
    }

    if status == "blocking":
        return {
            "status": "blocking",
            "blocker_code": blocker,
            "project_path": project_path,
            "project_context": context_payload,
            "file_inventory": inventory_payload,
            "scan_job": scan_job,
            "plugin_executions": plugin_executions,
            "file_checks": [],
            "file_checks_count": 0,
        }

    return {
        "status": "passed",
        "project_path": project_path,
        "project_context": {
            "context_summary": context_payload["context_summary"],
            "project_rules": context_payload["project_rules"],
            "check_detection": context_payload["check_detection"],
            "git_truth": context_payload["git_truth"],
            "next_autonomous_step": context_payload["next_autonomous_step"],
        },
        "file_inventory": {
            "status": inventory_payload["status"],
            "files_count": len(file_checks),
            "skipped_count": files_skipped,
        },
        "scan_job": scan_job,
        "plugin_executions": plugin_executions,
        "file_checks": file_checks,
        "file_checks_count": len(file_checks),
    }


def postgres_validation_state(
    payload: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
) -> tuple[int, dict[str, Any]]:
    validation_payload = payload.get("validation_result") if isinstance(payload.get("validation_result"), dict) else None
    if not validation_payload:
        return 0, {"validation_result": None, "findings": []}

    code, normalized = normalize_validation_payload({**validation_payload, "project_id": project_id, "run_id": run_id})
    if code != 0:
        assert isinstance(normalized, dict)
        return code, {**normalized, "state_backend": "postgres"}
    assert isinstance(normalized, ValidationResult)

    validation_attempt_id = "validation-" + qg_digest(
        normalized.run_id,
        normalized.gate,
        normalized.command,
        str(normalized.exit_code),
    )
    findings = [
        {
            "id": "qg-finding-" + qg_digest(normalized.run_id, normalized.gate, str(index), finding["evidence"]),
            "compatibility_finding_id": "compat-qg-finding-" + qg_digest(
                normalized.run_id,
                normalized.gate,
                str(index),
                finding["evidence"],
            ),
            "signature": qg_finding_signature(normalized, finding),
            "rule_id": finding["rule_id"],
            "severity": finding["severity"],
            "title": finding["title"],
            "message": finding["message"],
            "evidence": finding["evidence"],
            "file_path": finding["file_path"],
            "line_number": finding["line_number"],
            "task_id": stable_id("task", "standalone:" + qg_finding_signature(normalized, finding)),
            "task_signature": "standalone:" + qg_finding_signature(normalized, finding),
            "task_priority": severity_priority(finding["severity"]),
        }
        for index, finding in enumerate(normalized.findings, start=1)
    ]
    return 0, {
        "validation_attempt_id": validation_attempt_id,
        "qa_gate_id": "qg-" + qg_digest(normalized.run_id, normalized.gate),
        "validation_result": {
            "gate": normalized.gate,
            "command": normalized.command,
            "exit_code": normalized.exit_code,
            "cwd": normalized.cwd,
            "stdout_summary": normalized.stdout_summary,
            "stderr_summary": normalized.stderr_summary,
            "status": normalized.status,
            "evidence": normalized.evidence,
            "secret_redaction_applied": normalized.secret_redaction_applied,
        },
        "findings": findings,
    }


def qg_finding_signature(result: ValidationResult, finding: dict[str, Any]) -> str:
    return "finding:qg:" + qg_digest(
        result.project_id,
        result.run_id,
        result.gate,
        result.command,
        finding["file_path"],
        str(finding["line_number"]),
        finding["message"],
    )


def qg_digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def postgres_lock_acquire(
    dsn: str,
    *,
    project_id: str,
    run_id: str,
    owner: str,
    ttl_seconds: int,
) -> tuple[int, dict]:
    lock_id = f"{project_id}:{run_id}:{owner}"
    sql = """
begin;
select case when pg_try_advisory_xact_lock(hashtext(:project_id))
then json_build_object('advisory_lock', 'taken')
else json_build_object(
  'status', 'blocked',
  'blocker_code', 'STATE_ADVISORY_LOCK_HELD',
  'state_backend', 'postgres',
  'project_id', :project_id,
  'reason', 'another transaction owns the project advisory lock'
) end;
update state_locks
set status = 'expired', released_at = now(), updated_at = now()
where status = 'active' and released_at is null and expires_at is not null and expires_at <= now();
insert into state_locks(id, project_id, run_id, owner, status, acquired_at, expires_at)
select :lock_id, :project_id, :run_id, :owner, 'active', now(), now() + (:ttl_seconds || ' seconds')::interval
where exists (select 1 from projects where id = :project_id)
  and not exists (
    select 1 from state_locks
    where project_id = :project_id and status = 'active' and released_at is null
  )
on conflict (id) do nothing;
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'lock_acquired', true,
    'lock', json_build_object(
      'id', id,
      'project_id', project_id,
      'run_id', run_id,
      'owner', owner,
      'status', status
    )
  )
  from state_locks
  where project_id = :project_id
    and run_id = :run_id
    and owner = :owner
    and status = 'active'
    and released_at is null
  order by acquired_at desc
  limit 1),
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'STATE_LOCK_HELD',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'another run owns the active project state lock',
    'active_lock', json_build_object('id', id, 'run_id', run_id, 'owner', owner, 'status', status)
  )
  from state_locks
  where project_id = :project_id and status = 'active' and released_at is null
  order by acquired_at desc
  limit 1),
  json_build_object(
    'status', 'blocked',
    'blocker_code', 'PROJECT_NOT_FOUND',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'initialize project state before acquiring a lock'
  )
);
commit;
"""
    return run_psql_json(
        dsn,
        bind_literals(
            sql,
            {
                "project_id": project_id,
                "run_id": run_id,
                "owner": owner,
                "ttl_seconds": str(max(1, ttl_seconds)),
                "lock_id": lock_id,
            },
        ),
    )


def postgres_lock_release(dsn: str, *, project_id: str, run_id: str, owner: str) -> tuple[int, dict]:
    sql = """
begin;
select case when pg_try_advisory_xact_lock(hashtext(:project_id))
then json_build_object('advisory_lock', 'taken')
else json_build_object(
  'status', 'blocked',
  'blocker_code', 'STATE_ADVISORY_LOCK_HELD',
  'state_backend', 'postgres',
  'project_id', :project_id,
  'reason', 'another transaction owns the project advisory lock'
) end;
update state_locks
set status = 'released', released_at = now(), updated_at = now()
where project_id = :project_id
  and run_id = :run_id
  and owner = :owner
  and status = 'active'
  and released_at is null;
select coalesce(
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'STATE_LOCK_HELD',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'another run owns the active project state lock',
    'active_lock', json_build_object('id', id, 'run_id', run_id, 'owner', owner, 'status', status)
  )
  from state_locks
  where project_id = :project_id
    and status = 'active'
    and released_at is null
  order by acquired_at desc
  limit 1),
  json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'lock_released', true,
    'project_id', :project_id,
    'run_id', :run_id
  )
);
commit;
"""
    return run_psql_json(
        dsn,
        bind_literals(sql, {"project_id": project_id, "run_id": run_id, "owner": owner}),
    )


def postgres_run_start(
    dsn: str,
    *,
    project_id: str,
    run_id: str,
    latest_ref: str | None,
) -> tuple[int, dict]:
    latest_ref_value = latest_ref or "current ref"
    sql = """
begin;
select case when pg_try_advisory_xact_lock(hashtext(:project_id))
then json_build_object('advisory_lock', 'taken')
else json_build_object(
  'status', 'blocked',
  'blocker_code', 'STATE_ADVISORY_LOCK_HELD',
  'state_backend', 'postgres',
  'project_id', :project_id,
  'reason', 'another transaction owns the project advisory lock'
) end;
insert into runs(id, project_id, status, current_focus, next_autonomous_step)
select
  :run_id,
  :project_id,
  'in_progress',
  'autonomous_cycle',
  'preflight ' || :latest_ref || ' before continuing: ' || coalesce(
    (select nullif(memory_json->>'next_autonomous_step', '')
     from memories
     where project_id = :project_id
     order by created_at desc
     limit 1),
    'run repository preflight'
  )
where exists (select 1 from projects where id = :project_id)
  and not exists (
    select 1 from state_locks
    where project_id = :project_id
      and status = 'active'
      and released_at is null
      and run_id <> :run_id
  )
on conflict (id) do nothing;
select coalesce(
  (select json_build_object(
    'status', 'passed',
    'state_backend', 'postgres',
    'project', json_build_object('id', projects.id, 'target', projects.target, 'default_branch', projects.default_branch, 'status', projects.status),
    'run', json_build_object('id', runs.id, 'project_id', runs.project_id, 'status', runs.status, 'current_focus', runs.current_focus, 'next_autonomous_step', runs.next_autonomous_step),
    'loaded_memory', (select memory_json from memories where project_id = :project_id order by created_at desc limit 1),
    'stale_memory_decision', coalesce((select stale_memory_decision from memories where project_id = :project_id order by created_at desc limit 1), 'not_applicable'),
    'repository_delta', 'repository truth must be checked before trusting memory',
    'next_autonomous_step', runs.next_autonomous_step
  )
  from runs
  join projects on projects.id = runs.project_id
  where runs.id = :run_id),
  (select json_build_object(
    'status', 'blocked',
    'blocker_code', 'STATE_LOCK_HELD',
    'state_backend', 'postgres',
    'project_id', :project_id,
    'reason', 'another run owns the active project state lock',
    'active_lock', json_build_object('id', id, 'run_id', run_id, 'owner', owner, 'status', status)
  )
  from state_locks
  where project_id = :project_id and status = 'active' and released_at is null and run_id <> :run_id
  order by acquired_at desc
  limit 1),
  json_build_object('status', 'blocked', 'blocker_code', 'PROJECT_NOT_FOUND', 'state_backend', 'postgres', 'project_id', :project_id, 'reason', 'initialize project state before starting a run')
);
commit;
"""
    return run_psql_json(
        dsn,
        bind_literals(sql, {"project_id": project_id, "run_id": run_id, "latest_ref": latest_ref_value}),
    )


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def severity_priority(severity: str) -> int:
    return {
        "critical": 40,
        "blocking": 40,
        "high": 30,
        "medium": 20,
        "low": 10,
        "info": 0,
    }.get(str(severity).lower(), 20)


def bind_literals(sql: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        sql = bind_literal(sql, key, value)
    return sql


def bind_literal(sql: str, key: str, value: str) -> str:
    return sql.replace(f":{key}", quote_sql(value))


def quote_sql(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def summarize_stderr(stderr: str) -> str:
    text = " ".join((stderr or "").split())
    if not text:
        return "psql returned a non-zero exit code"
    return text[:500]
