create table if not exists state_locks (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  run_id text not null,
  owner text not null,
  status text not null default 'active',
  acquired_at text not null default (datetime('now')),
  expires_at text,
  released_at text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create unique index if not exists idx_state_locks_one_active_project
on state_locks(project_id)
where status = 'active' and released_at is null;
