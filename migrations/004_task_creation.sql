alter table tasks add column task_type text not null default 'standalone';
alter table tasks add column task_signature text;
alter table tasks add column subtask_order integer;
alter table tasks add column progress_json text not null default '{}';

create unique index if not exists idx_tasks_project_signature
  on tasks(project_id, task_signature)
  where task_signature is not null;

create index if not exists idx_tasks_parent_order
  on tasks(parent_task_id, subtask_order);
