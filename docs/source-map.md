# Code Sentinel Runtime Adapter Source Map

Status: ARA-0 implementation evidence
Reference repo: /home/pika/projekte/code-sentinel
Adapter target: /home/pika/projekte/agents/agent-runtime/code-sentinel

## Purpose

This source map decides what local Code Sentinel behavior is transferred into
the Workspace Agent runtime adapter, what is emulated with SQLite/CLI state,
what remains reference_only, and what is out_of_scope for adapter v1.

The goal is not to clone the production SaaS runtime. The goal is to transfer
the operating logic that makes Code Sentinel autonomous: preflight, blocker-first
quality gates, findings-to-tasks, guarded validation, approval-aware writes,
memory/state continuation, and explicit evidence.

## Classification Legend

- direct_transfer: behavior can be implemented in the adapter almost directly.
- emulate: preserve the decision model, but replace heavy local runtime with
  SQLite, JSON, filesystem artifacts, or CLI calls.
- reference_only: keep as source-of-truth behavior guidance, but do not build in
  adapter v1.
- out_of_scope: do not transfer into adapter v1.

## Source Evidence

### Product And Workflow Analysis

Source: /home/pika/projekte/code-sentinel/docs/code-sentinel-agent-analysis.md

Classification: direct_transfer and reference_only.

Transfer:

- blocker-first scan model
- finding-to-task conversion model
- validation before completion
- explicit write boundaries
- status/evidence persistence
- agent must not bypass preflight, tests, git, tenant, budget, audit, or status
  controls

Reference only:

- full FastAPI product surface
- SaaS tenant/admin/billing/UI architecture
- complete PostgreSQL/Redis production deployment

Reason:

The analysis doc states that Code Sentinel is not just a scanner. It is a
controlled code-quality workflow with status, findings, tasks, retries, tests,
and PR results. That workflow is the adapter target. The production API and
multi-tenant service stack are too heavy for adapter v1.

### Quality Gate Runtime

Sources:

- /home/pika/projekte/code-sentinel/infrastructure/quality_gates/quality_check_repository.py
- /home/pika/projekte/code-sentinel/infrastructure/persistence/quality_gate_repository.py
- /home/pika/projekte/code-sentinel/infrastructure/execution/qg_test_runner.py

Classification: emulate.

Transfer:

- gate run records
- gate status records
- fix/session attempt records
- error/warning counts
- files affected
- branch/run metadata
- validation command result capture
- blocking vs passed/open/not_applicable decisions

Adapter equivalent:

- SQLite tables: qa_gate_results, validation_attempts, runs
- Python module: code_sentinel_agent.qa_gates
- CLI command: cs-agent qa-gates --run-id <id>
- JSON output with exact command, exit code, stderr/stdout summary, and blocker
  fields

Do not transfer directly:

- SQLAlchemy async repositories
- PostgreSQL models
- production DB transaction/session lifecycle

Reason:

The adapter needs the quality gate decision model, not the PostgreSQL-specific
implementation.

### Redis Streams And Worker Delivery

Sources:

- /home/pika/projekte/code-sentinel/infrastructure/messaging/stream_service.py
- /home/pika/projekte/code-sentinel/workers/stream_task_worker.py
- /home/pika/projekte/code-sentinel/workers/task_processing_worker.py
- /home/pika/projekte/code-sentinel/workers/scan_worker.py

Classification: emulate.

Transfer:

- stream/run state concepts
- pending/in_progress/completed/failed/dlq-like states
- retry/reclaim idea
- worker-safe idempotency
- explicit next action after each run

Adapter equivalent:

- SQLite tables: runs, tasks, validation_attempts
- fields: status, attempt_count, max_attempts, blocker, next_autonomous_step
- filesystem artifacts under agent-runtime/code-sentinel/runs
- CLI command: cs-agent cycle resume --project <target>

Do not require:

- Redis
- XREADGROUP
- XAUTOCLAIM
- consumer groups
- long-running daemon for v1

Reason:

The Workspace Agent does not need Redis to preserve the autonomous loop. SQLite
state plus deterministic CLI commands is enough for v1.

### Task Persistence And Finding-To-Task Flow

Sources:

- /home/pika/projekte/code-sentinel/infrastructure/persistence/task_repository.py
- /home/pika/projekte/code-sentinel/application/services/task_creation_service.py
- /home/pika/projekte/code-sentinel/docs/code-sentinel-agent-analysis.md

Classification: emulate.

Transfer:

- finding grouping by file
- dedupe by signature
- parent/subtask idea
- task severity/status/priority
- affected file and line evidence
- retry attempt counters

Adapter equivalent:

- SQLite tables: findings, tasks
- Python module: code_sentinel_agent.tasks
- CLI commands: cs-agent finding add, cs-agent task add, cs-agent report
- deterministic signatures from source path, category, rule id, line, and text

Do not transfer directly:

- tenant-aware SQLAlchemy task models
- full production task API
- DB RLS behavior

Reason:

The agent needs durable task/finding state, not the full SaaS task service.

### Session And tmux Execution

Sources:

- /home/pika/projekte/code-sentinel/infrastructure/quality_gates/session_manager.py
- /home/pika/projekte/code-sentinel/infrastructure/session/claude_session_store.py
- /home/pika/projekte/code-sentinel/infrastructure/execution/claude_executor.py

Classification: reference_only for tmux/session UX, emulate for execution state.

Transfer:

- observable execution concept
- session/run record
- started/completed/error states
- command/output evidence
- cleanup/rollback expectation

Adapter equivalent:

- SQLite run records
- artifacts: prompt, command, stdout, stderr, report JSON
- no tmux requirement
- no Claude CLI requirement

Out of scope for v1:

- tmux attach/capture
- Redis-backed ClaudeSessionStore
- Claude executor replacement as a production bot runner

Reason:

The Workspace Agent may have Python execution or may only have file/static
analysis. It must report static_only or blocked when no execution evidence
exists.

### Security, Tenant, RBAC, Budget, Audit

Sources:

- /home/pika/projekte/code-sentinel/docs/code-sentinel-agent-analysis.md
- /home/pika/projekte/code-sentinel/infrastructure/persistence/tenant_aware_repository.py
- /home/pika/projekte/code-sentinel/presentation/api/middleware/auth.py
- /home/pika/projekte/code-sentinel/infrastructure/billing/budget_guard.py
- /home/pika/projekte/code-sentinel/infrastructure/security

Classification: reference_only with adapter guard equivalents.

Transfer:

- never cross project/user boundaries
- never expose secrets
- every write must require explicit per-run approval
- record evidence and rollback plan
- fail closed when auth/scope is missing

Adapter equivalent:

- SQLite approvals table
- approval fields: run_id, target_project, branch, allowed_paths,
  allowed_actions, expires_at, approved_by, evidence
- write guard checks before mutation-capable commands
- output blocker when approval is missing

Do not transfer directly:

- tenant-aware PostgreSQL RLS
- JWT/API-key middleware
- Stripe/billing implementation
- production audit tables

Reason:

The adapter has a single local operator context. It still needs guardrails, but
not the full SaaS tenancy stack.

## v1 Transfer Matrix

| Area | Classification | Adapter Representation |
| --- | --- | --- |
| blocker-first scan order | direct_transfer | qa_gates.py and preflight.py |
| validation result language | direct_transfer | JSON gate output and validation_attempts |
| findings/tasks | emulate | SQLite findings/tasks tables |
| Redis Streams | emulate | SQLite run/task states |
| DLQ/retry | emulate | failed/blocked/dlq-like run statuses |
| PostgreSQL persistence | emulate | SQLite |
| SQLAlchemy repositories | reference_only | schema and repo helper inspiration |
| tmux sessions | reference_only | run artifacts and execution evidence |
| Claude executor | reference_only | Workspace Agent or Python tool execution |
| tenant/RBAC/budget | reference_only | approval/write guard and blocker output |
| billing/Stripe/UI/API | out_of_scope | not implemented in adapter v1 |
| Kubernetes/Docker deployment | out_of_scope | not required for adapter v1 |

## ARA-0 Conclusion

The adapter should start with SQLite and Python CLIs. It should not start by
copying PostgreSQL repositories, Redis services, tmux session management, or API
controllers. The first implementation phase is SQLite State Core, but only after
this source-map and adapter-contract pass the docs contract test.

