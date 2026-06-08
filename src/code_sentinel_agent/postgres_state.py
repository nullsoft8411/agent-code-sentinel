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
      'validation_attempts', 0,
      'execution_sessions', 0
    ),
    'unsupported_counts', json_build_array(
      'validation_attempts',
      'execution_sessions'
    ),
    'qa_review_outcomes', json_build_array(),
    'missing_review_outcomes', json_build_array(),
    'execution_sessions', json_build_array(),
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
