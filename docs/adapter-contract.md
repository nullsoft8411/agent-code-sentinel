# Code Sentinel Runtime Adapter Contract

Status: ARA-0 implementation evidence
Adapter root: agent-runtime/code-sentinel

## Purpose

This contract defines how the Code Sentinel Workspace Agent transfers local
Code Sentinel logic into an agent-compatible runtime without pretending it owns
the original PostgreSQL, Redis, tmux, worker, tenant, or SaaS runtime.

The adapter is a local script project. It is allowed to be used by the Workspace
Agent when execution tools exist. If execution tools are missing, the agent must
return static_only or blocked.

## Runtime Backend

### Database

- type: SQLite
- path: agent-runtime/code-sentinel/state/code_sentinel_agent.db
- migrations: agent-runtime/code-sentinel/migrations
- generated DB files: not committed unless explicitly requested

SQLite is the v1 persistence layer. PostgreSQL is not_required for adapter v1.

Required tables for v1:

- projects
- runs
- memories
- findings
- tasks
- qa_gate_results
- validation_attempts
- approvals
- artifacts

### Queue And Worker

Redis is not_required for adapter v1.

Redis Streams and consumer groups are emulated with:

- runs.status
- tasks.status
- attempt_count
- max_attempts
- blocker
- next_autonomous_step
- artifact paths

A future daemon may poll SQLite, but v1 is CLI-driven.

### Object Storage

Object storage is not_required.

Artifacts are stored under:

- agent-runtime/code-sentinel/runs/<run_id>/

### Auth, Tenant, Billing

The adapter does not implement production auth, tenant RLS, billing, or Stripe.
Those are reference_only.

The adapter must still enforce local write approval:

- no file mutation
- no branch creation
- no commit
- no PR
- no delete
- no publish
- no deploy

unless an approval record exists for the exact run, project, action, and path.

## CLI Contract

The adapter must expose JSON-first commands.

Planned command family:

- cs-agent db init --db <path>
- cs-agent preflight --project <repo-or-url>
- cs-agent detect-checks --path <repo>
- cs-agent qa-gates --run-id <id>
- cs-agent finding add --run-id <id> ...
- cs-agent task add --run-id <id> ...
- cs-agent memory-delta --project <id>
- cs-agent report --run-id <id>
- cs-agent cycle resume --project <id>

Output rules:

- JSON by default
- no secrets in output
- every command includes status
- every blocker includes blocker_code, reason, next_action
- every executed command includes command, cwd, exit_code, stdout/stderr summary
- every static-only result says static_only
- every unavailable execution says blocked

## Status Vocabulary

Allowed statuses:

- passed
- failed
- blocking
- open
- advisory
- not_applicable
- static_only
- blocked
- pending
- in_progress
- completed

Invalid language:

- validated when validation was only proposed
- tests passed without command and exit code
- script executed without command and output evidence
- write completed without approval record and artifact

## Quality Gate Contract

The QA gate layer must include these gates:

- project_rules
- validation
- security
- dependency
- reuse
- duplicate_code
- dead_code
- unused_code
- continuation
- risk_and_rollback

Each gate result must include:

- gate
- status
- evidence
- why_it_matters
- next_action

## Approval Contract

Write approval records must include:

- approval_id
- run_id
- target_project
- branch
- allowed_paths
- allowed_actions
- approved_by
- approval_evidence
- expires_at
- consumed_at

Any command that can mutate local files or external state must check approval
first. Missing approval is a blocker, not a warning.

## Agent Studio Contract

Agent Studio files and skills are the interface layer, not the only logic layer.

The agent should prefer runtime scripts when available:

1. inspect uploaded file/script contract
2. if Python/tool execution exists, execute allowed command and report evidence
3. if execution is unavailable, report static_only or blocked
4. never claim runtime evidence from prompt memory alone

## Test-First Rule

Every adapter phase runs:

1. write focused failing test
2. run and record expected failure
3. implement smallest slice
4. rerun focused test
5. run relevant integration/Slack gate
6. record evidence and reuse/dead-code/duplicate-code outcome

No DB, CLI, write-guard, schedule, or autonomous behavior is complete from docs
alone.

## Out Of Scope For Adapter v1

- production PostgreSQL
- Redis Streams
- tmux attach/capture behavior
- Claude CLI executor
- FastAPI product API
- admin frontend
- billing/Stripe
- Kubernetes runtime
- full tenant/RBAC implementation
- raw local secrets or env values

## ARA-0 Completion Rule

ARA-0 is complete only when:

- source-map.md exists
- adapter-contract.md exists
- both are based on concrete local Code Sentinel paths
- tests/docs-contract.test.js passes
- final QA reports no fail/warn findings for changed files

