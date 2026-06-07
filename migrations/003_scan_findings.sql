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
  metadata_json text not null default '{}',
  started_at text not null default (datetime('now')),
  completed_at text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
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
  metadata_json text not null default '{}',
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
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
  started_at text not null default (datetime('now')),
  completed_at text,
  exit_code integer,
  stdout_summary text,
  stderr_summary text,
  metadata_json text not null default '{}',
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
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
  checks_json text not null default '[]',
  metadata_json text not null default '{}',
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(project_id, scan_job_id, file_path)
);

create index if not exists idx_file_checks_project_file
  on file_checks(project_id, file_path);
