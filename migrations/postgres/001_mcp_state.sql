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
