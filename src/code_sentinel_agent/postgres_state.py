from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote


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
      'findings', 0,
      'tasks', 0,
      'validation_attempts', 0,
      'execution_sessions', 0
    ),
    'unsupported_counts', json_build_array(
      'findings',
      'tasks',
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
