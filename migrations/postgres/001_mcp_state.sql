create table if not exists projects (
  id text primary key,
  target text not null unique,
  default_branch text,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists runs (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  status text not null,
  current_focus text,
  blocker text,
  next_autonomous_step text,
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists memories (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  latest_ref text,
  memory_json jsonb not null,
  stale_memory_decision text,
  created_at timestamptz not null default now()
);

create table if not exists findings (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  signature text not null,
  category text not null,
  severity text not null,
  file_path text,
  line_number integer,
  title text not null,
  details text,
  status text not null default 'open',
  created_at timestamptz not null default now(),
  unique(project_id, signature)
);

create table if not exists tasks (
  id text primary key,
  finding_id text references findings(id) on delete set null,
  run_id text not null references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  parent_task_id text references tasks(id) on delete set null,
  status text not null,
  priority integer not null default 0,
  title text not null,
  affected_file text,
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  task_type text not null default 'standalone',
  task_signature text,
  subtask_order integer,
  progress_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_tasks_project_signature
on tasks(project_id, task_signature)
where task_signature is not null;

create index if not exists idx_tasks_parent_order
on tasks(parent_task_id, subtask_order);

create table if not exists scan_jobs (
  id text primary key,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  scanner_name text not null,
  scan_type text not null,
  status text not null,
  target_ref text,
  files_total integer not null default 0,
  files_scanned integer not null default 0,
  files_skipped integer not null default 0,
  findings_count integer not null default 0,
  error_message text,
  metadata_json jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_scan_jobs_project_status
on scan_jobs(project_id, status);

create index if not exists idx_scan_jobs_run
on scan_jobs(run_id);

create table if not exists scan_findings (
  id text primary key,
  scan_job_id text references scan_jobs(id) on delete set null,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  compatibility_finding_id text references findings(id) on delete set null,
  scanner_name text not null,
  rule_id text not null,
  file_path text,
  line_number integer,
  column_number integer,
  severity text not null,
  status text not null default 'open',
  title text not null,
  message text,
  suggestion text,
  evidence text,
  signature text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(project_id, signature)
);

create index if not exists idx_scan_findings_project_status
on scan_findings(project_id, status);

create index if not exists idx_scan_findings_run
on scan_findings(run_id);

create index if not exists idx_scan_findings_scan_job
on scan_findings(scan_job_id);

create index if not exists idx_scan_findings_file_path
on scan_findings(file_path);

create table if not exists plugin_executions (
  id text primary key,
  scan_job_id text references scan_jobs(id) on delete cascade,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  plugin_name text not null,
  status text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  exit_code integer,
  stdout_summary text,
  stderr_summary text,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_plugin_executions_scan_job
on plugin_executions(scan_job_id);

create table if not exists file_checks (
  id text primary key,
  scan_job_id text references scan_jobs(id) on delete cascade,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  file_path text not null,
  content_sha256 text,
  language text,
  status text not null,
  checks_json jsonb not null default '[]'::jsonb,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(project_id, scan_job_id, file_path)
);

create index if not exists idx_file_checks_project_file
on file_checks(project_id, file_path);

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
  output_json jsonb not null default '{}'::jsonb,
  error_summary text,
  files_modified_json jsonb not null default '[]'::jsonb,
  attempt_log_json jsonb not null default '[]'::jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_agent_execution_sessions_run
on agent_execution_sessions(run_id);

create index if not exists idx_agent_execution_sessions_task
on agent_execution_sessions(task_id);

create index if not exists idx_agent_execution_sessions_project_status
on agent_execution_sessions(project_id, status);

create table if not exists audit_events (
  id text primary key,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  event_type text not null,
  summary text not null,
  payload_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_audit_events_project_run
on audit_events(project_id, run_id);

create table if not exists qa_gate_results (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  gate text not null,
  status text not null,
  evidence text not null,
  why_it_matters text,
  next_action text,
  created_at timestamptz not null default now(),
  unique(run_id, gate)
);

create table if not exists validation_attempts (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  command text not null,
  cwd text,
  exit_code integer,
  stdout_summary text,
  stderr_summary text,
  status text not null,
  created_at timestamptz not null default now()
);

create table if not exists approvals (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  target_project text not null,
  branch text,
  allowed_paths_json jsonb not null,
  allowed_actions_json jsonb not null,
  approved_by text not null,
  approval_evidence text not null,
  expires_at timestamptz,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_approvals_run_target_branch
on approvals(run_id, target_project, branch)
where consumed_at is null;

create table if not exists artifacts (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  kind text not null,
  path text not null,
  sha256 text,
  created_at timestamptz not null default now()
);

create table if not exists state_locks (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  run_id text not null,
  owner text not null,
  status text not null default 'active',
  acquired_at timestamptz not null default now(),
  expires_at timestamptz,
  released_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_state_locks_one_active_project
on state_locks(project_id)
where status = 'active' and released_at is null;

-- Advisory lock key for project-level MCP state operations:
-- select pg_try_advisory_xact_lock(hashtext(:project_id));
-- Use this inside the same transaction before creating/updating run state.
