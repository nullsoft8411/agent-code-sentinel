create table if not exists projects (
  id text primary key,
  target text not null unique,
  default_branch text,
  status text not null default 'active',
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
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
  started_at text not null default (datetime('now')),
  completed_at text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists memories (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  latest_ref text,
  memory_json text not null,
  stale_memory_decision text,
  created_at text not null default (datetime('now'))
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
  created_at text not null default (datetime('now')),
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
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists qa_gate_results (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  gate text not null,
  status text not null,
  evidence text not null,
  why_it_matters text,
  next_action text,
  created_at text not null default (datetime('now')),
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
  created_at text not null default (datetime('now'))
);

create table if not exists approvals (
  id text primary key,
  run_id text not null,
  target_project text not null,
  branch text,
  allowed_paths_json text not null,
  allowed_actions_json text not null,
  approved_by text not null,
  approval_evidence text not null,
  expires_at text,
  consumed_at text,
  created_at text not null default (datetime('now'))
);

create table if not exists artifacts (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  kind text not null,
  path text not null,
  sha256 text,
  created_at text not null default (datetime('now'))
);

