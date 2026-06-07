create table if not exists audit_events (
  id text primary key,
  run_id text references runs(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  event_type text not null,
  summary text not null,
  payload_json text not null default '{}',
  created_at text not null default (datetime('now'))
);

create index if not exists idx_audit_events_project_run
  on audit_events(project_id, run_id);

create table if not exists pr_states (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  run_id text references runs(id) on delete set null,
  branch text not null,
  base_branch text,
  status text not null,
  pr_url text,
  metadata_json text not null default '{}',
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(project_id, branch)
);

create index if not exists idx_pr_states_project_status
  on pr_states(project_id, status);
