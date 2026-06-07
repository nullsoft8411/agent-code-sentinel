create table if not exists agent_execution_sessions (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  task_id text references tasks(id) on delete set null,
  session_type text not null,
  script_name text not null,
  execution_method text not null,
  command text not null,
  status text not null,
  output_json text not null default '{}',
  error_summary text,
  files_modified_json text not null default '[]',
  attempt_log_json text not null default '[]',
  started_at text not null default (datetime('now')),
  completed_at text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create index if not exists idx_agent_execution_sessions_run
  on agent_execution_sessions(run_id);

create index if not exists idx_agent_execution_sessions_task
  on agent_execution_sessions(task_id);

create index if not exists idx_agent_execution_sessions_project_status
  on agent_execution_sessions(project_id, status);
