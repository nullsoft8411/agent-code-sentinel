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
