# Agent-Native Code Sentinel Migration Plan

Status: implementation_plan
Created: 2026-06-07
Source runtime: /home/pika/projekte/code-sentinel
Target runtime: /home/pika/projekte/agent-code-sentinel
Primary actor: ChatGPT Workspace Agent
State bridge: PIKA MCP state tools and shared DB

## 1. Core Correction

The local Code Sentinel source uses ClaudeExecutor, Claude sessions, tmux
sessions, budget tracking and conversation logs as the old external-AI execution
path. That path must not be copied as the target executor.

In the Workspace Agent architecture, the Agent itself is the executor:

Contract statement: The Agent itself is the executor.

1. The Agent reads repository context, rules, files, prior state and command
   output.
2. The Agent performs the analysis that the old ClaudeExecutor delegated to an
   external Claude CLI run.
3. The Agent creates findings from static checks, command output, file analysis,
   project rules and its own reasoning.
4. The Agent converts findings into jobs, parent tasks and subtasks.
5. The Agent decides the next autonomous step, validates it, records evidence and
   only mutates files when an explicit approval contract allows it.

Therefore, the migration target is not "copy ClaudeExecutor". The migration
target is "replace ClaudeExecutor with an agent-native analysis and execution
contract that preserves Code Sentinel's workflow, safety gates and state model".

Contract statement: The migration target is not "copy ClaudeExecutor".

## 2. Source Responsibilities To Preserve

### 2.1 Finding Analysis

Source evidence:

- application/scanner_service.py
- application/services/code_scanner_orchestrator.py
- domain/scanning/plugins/builtin/qg_check_plugin.py
- application/services/task_creation_service.py
- infrastructure/persistence/models/scan_finding.py

Preserve:

- blocker-first checks before normal analysis
- finding categories such as security, duplicate code, dead code, code quality
  and dependency
- file path, line, severity, title, description, rule/source, suggestion and
  dedupe signature
- command-output-derived findings from lint/type/test tools
- agent-reasoned findings from project context and file analysis

Do not preserve directly:

- Redis-driven delivery
- SQLAlchemy repository implementation
- any external AI process as the analyzer

Target modules:

- scanner_registry.py
- project_context.py
- scan_jobs.py
- scan_findings.py
- agent_analysis.py
- output_contract.py

### 2.2 Finding To Job/Task/Subtask

Source evidence:

- application/services/task_creation_service.py
- infrastructure/persistence/models/task.py
- workers/task_processing_worker.py

Preserve:

- one file with one finding can become one standalone task
- one file with multiple findings becomes one parent task and ordered subtasks
- dedupe by deterministic signature
- max-subtask protection
- severity and priority propagation
- parent status derived from subtask states
- retry/attempt counters and blocked/failed/completed states

Target modules:

- task_creation.py
- task_workflow.py
- policy.py
- reports.py

### 2.3 ScanJob And Plugin Execution

Source evidence:

- application/services/scan_execution_service.py
- infrastructure/persistence/models/scan_job.py
- infrastructure/persistence/models/plugin_execution_model.py
- workers/scan_worker.py

Preserve:

- single entrypoint for scan job creation
- trigger to scan-type mapping
- pending, running, completed, failed, cancelled states
- files_total, files_scanned, files_skipped and issues_found
- plugin/check execution records
- scan result leads to findings and then task creation

Target modules:

- scan_jobs.py
- scanner_registry.py
- plugin_executions.py
- file_inventory.py

### 2.4 Quality Gates And Validation

Source evidence:

- domain/scanning/plugins/builtin/qg_check_plugin.py
- infrastructure/execution/qg_test_runner.py
- infrastructure/persistence/models/quality_gate.py
- infrastructure/persistence/models/qg_workflow.py

Preserve:

- preflight before work
- blocker gate before normal scan/fix
- lint/type/test commands normalized as evidence
- command, cwd, exit_code, stdout/stderr summaries
- retry/fix-session lifecycle
- no completion claim without validation evidence

Target modules:

- preflight.py
- qa_gates.py
- validation_runner.py
- qg_workflow.py
- execution_sessions.py

### 2.5 External-AI Execution Replacement

Source evidence:

- infrastructure/execution/claude_executor.py
- infrastructure/persistence/models/claude_session_model.py
- workers/task_processing_worker.py

Preserve as workflow concepts:

- input task context
- safety validation for branch/path/command
- execution session record
- files_modified evidence
- output/error summaries
- retry/circuit-breaker style blocker handling
- conversation/attempt log as sanitized artifact concept

Replace:

- Claude CLI command
- tmux session manager
- Claude model selection
- Claude token budget
- Claude session store

Target replacement:

- agent_analysis.py: creates findings and fix plans from repo context, command
  results and prior state.
- agent_execution.py: records the Agent's own action plan, commands requested,
  write approvals, command results and final task result.

### 2.6 Complete Functional Migration Coverage

Complete migration means every functional responsibility of the local
Code Sentinel workweise is either ported into agent-code-sentinel, explicitly
adapted to the Workspace Agent runtime, or explicitly classified as not
required for the Agent target after source-backed review. It is not acceptable
to declare broad local Code Sentinel areas out of scope from assumption alone.
Every model, service, worker, plugin category, repository contract, API-facing
workflow, safety guard and persistence behavior must have a row in the coverage
map or a linked follow-up gate before managed Agent testing resumes.

| Source functional area | Local source evidence | Target responsibility | Current target coverage | Required local gate before Agent packaging |
|---|---|---|---|---|
| Project intake and rules | AGENTS.md, README, project metadata, scanner/project services | Agent reads AGENTS.md chain, docs, configs, git truth, selected files and target ref before analysis | project_context.py, preflight.py and analysis_contract.py exist; local E2E now derives the Agent finding target from project_context file_inventory | Keep this covered in every broader local/managed E2E before packaging |
| Finding analysis | scanner_service.py, code_scanner_orchestrator.py, qg_check_plugin.py, scan_finding.py | Agent itself analyzes files and tool output, then emits structured finding candidates | agent_analysis.py, output_contract.py and analysis_workflow.py exist | E2E must prove Agent-supplied findings are persisted through analyze-to-state and then drive tasks |
| Scan job lifecycle | scan_execution_service.py, scan_job model, scan_worker.py | Runtime records scan job trigger, status, totals, target ref and plugin/file evidence | scan_jobs.py, file_inventory.py and plugin_executions.py exist for SQLite | Postgres parity and local smoke must prove scan_job, plugin_execution and file_check readback |
| Finding persistence and dedupe | scan_finding model, scanner repositories | Runtime stores source-compatible findings with signature dedupe and compatibility finding mapping | scan_findings.py and compatibility findings table exist | E2E must prove duplicate signatures do not create duplicate findings or tasks |
| Finding to task/subtask | task_creation_service.py, task model, subtask model | Runtime groups findings by affected file; one finding creates a standalone task, multiple findings create parent plus ordered subtasks | task_creation.py and task_workflow.py exist | E2E must prove parent progress/status across completed, blocked and failed subtasks |
| QA gate and validation | qg_workflow_orchestrator.py, qg_test_runner.py, quality_gate models | Runtime turns failed validation into findings/tasks and never marks failed validation complete | validation_runner.py, qa_gates.py and qg_workflow.py exist | E2E must prove failing validation creates finding, task and selected_task_for_agent_takeover |
| Task takeover | task_processing_worker.py | Agent pulls exactly one runnable task/subtask and owns the analysis/fix decision | cycle.py, improvement_work.py and task_execution.py exist | E2E must prove one selected task, affected-file evidence, QA review outcomes, report and next_autonomous_step |
| Old external execution boundary | claude_executor.py, Claude session model, session cleanup worker, tmux manager | Do not port executor; replace with Agent-owned decisions plus agent_execution_sessions | execution_sessions.py exists and target src is guarded against Claude/tmux references | Tests must keep target runtime free of ClaudeExecutor, Claude CLI and tmux; sessions must store Agent-native evidence only |
| Approval and write guard | claude_executor path/branch validation, git_service.py, PR flow | Runtime blocks writes unless explicit per-run approval covers action/path/branch | approvals.py and task_execution.py approval checks exist | E2E must prove unapproved file changes block and approved bounded changes record approval evidence |
| Audit/report/next step | audit_logger/audit models, worker status/report flows | Every cycle writes audit events, report counts, QA outcomes, risks, rollback and next_autonomous_step | audit_events.py, reports.py and cycle.py exist | E2E must prove report readback includes findings/tasks/qa/session/audit and no false-complete state |
| MCP shared state | local DB/repositories, queues/workers | MCP exposes controlled state tools without raw SQL or generic shell fallback | SQLite MCP tools and trend-mcp schemas exist | Local trend-mcp smoke must prove all required state tools, including state_run_cycle and analyze-to-state, against the runtime repo |
| Postgres shared state | local PostgreSQL/SQLAlchemy semantics | Durable shared-state backend or an explicit per-tool blocker with migration task | Postgres bootstrap currently covers only a subset of MCP tools | Do not claim complete migration or AN-9 readiness until every runtime state tool is implemented for Postgres or listed with exact blocker, owner and follow-up |
| Tenant/RBAC/API-key/user/service-account safety | tenant.py, user.py, api_key.py, service_account.py, invitation.py, auth/API services | Do not port SaaS human-identity, tenant-admin, role CRUD, SSO/OAuth, API-key or service-account management. Preserve only the functional Agent safety boundary through project_id, Workspace/MCP connector identity, explicit approvals, state lock owner and audit actors | source-reviewed and removed for SaaS identity; authorization exceptions and approval/write guard are adapted | Keep human-identity removal explicit in coverage and continue mapping project isolation, approvals, locks and audit evidence before AN-9 |
| Billing/budget/usage guard | budget.py, usage.py, budget_guard_service.py, billing/Stripe integration paths | Do not port billing, budget or usage accounting into the Agent runtime | source-reviewed and removed by explicit user decision | Keep budget/usage removed from target claims; do not replace it with generic quota, usage, cost-estimation or token-budget behavior |
| FastAPI/product API workflows | routers/API services, task_commands.py, task_queries.py, project/scan/task endpoints | Preserve user-visible workflow semantics as CLI/MCP/Agent contracts where they affect autonomous operation | partial: CLI and MCP commands exist for core runtime; full endpoint-to-contract mapping is incomplete | Build endpoint/workflow inventory and mark every workflow as migrated, adapted, or blocked before AN-9 |
| Redis/worker orchestration | Redis streams, scan_worker.py, task_processing_worker.py, worker_registry.py, stream_task_worker.py, projection/session cleanup workers | Preserve scheduling, queue, locking, retry, cleanup, projection and continuation semantics through MCP state, locks, schedules and reports | partial: lock/run/task cycle exists; full worker inventory mapping is incomplete | Map every worker to Agent/MCP equivalent, local script, schedule, cleanup task, or explicit blocker |
| GitHub/PR lifecycle | git_service.py, github_app_service.py, tracked_pr.py, pr_* workers | Preserve clone, branch, diff, commit, PR, tracking and rollback behavior through controlled MCP/GitHub tools | partial: clone/write/PR E2E exists; full tracked PR lifecycle mapping is incomplete | Add tracked PR lifecycle coverage or explicit blocker before complete migration claim |

The open migration work is therefore not "add Claude-like executor". The open
work is to make the Agent-owned analysis pipeline produce the same state
transitions that the local service used to produce through scanners, workers,
tasks, QA workflows and executor sessions.
- execution_sessions.py: persists agent-native execution sessions without
  referencing an external AI provider.
- policy.py: enforces write scope, command scope and approval boundaries. It
  must not reintroduce budget, usage, billing or token-cost accounting.

The Agent is the intelligence layer. Python scripts provide deterministic
state, extraction, normalization, validation and reporting.

### 2.7 Full Local Source Coverage Gate

Before any further Slack, Agent Studio, schedule, connector or managed-Agent
E2E work, run a full local Code Sentinel source coverage pass. This pass must
start from the source project, not from the already-ported adapter modules.

Required source inputs:

- infrastructure/persistence/models/
- database/alembic/versions/
- infrastructure/persistence/*repository*.py
- application/services/, application/commands/ and application/queries/
- domain/scanning/plugins/ and domain/scanning/entities/
- infrastructure/execution/
- workers/
- API/router paths if present
- docs that define user-visible Code Sentinel behavior

Coverage output must classify every discovered source responsibility as exactly
one of:

- migrated: implemented in agent-code-sentinel with tests and E2E evidence
- adapted: intentionally represented differently for the Workspace Agent, with
  the target module, test and E2E evidence named
- blocked: not implemented yet, with blocker code, risk and next task
- removed: not needed in the Agent target only after source-backed reasoning and
  explicit user acceptance

No phase after AN-8 and no AN-9 managed packaging may be called ready while any
source responsibility is unmapped. A passing local test suite is necessary but
not sufficient; the coverage matrix must prove that the whole local
Code Sentinel functional surface has been reviewed.

Contract statement: Full local Code Sentinel source coverage is mandatory before managed Agent testing resumes.

Initial full source coverage evidence:

- Generated file: docs/local-code-sentinel-full-source-coverage.json
- Generator: scripts/build_full_source_coverage.py
- Current gate_status: blocked
- Current coverage count: 199 source responsibilities, including 33 tables and
  166 runtime modules
- Current blocker count: 116 blocked entries after Auth/RBAC, SaaS identity,
  Budget/Billing/Usage and Command/Query/Service-Orchestration classification
  passes
- First coverage reduction: application/auth/authorization_service.py and
  application/auth/exceptions.py are adapted to project-scoped MCP tools,
  explicit per-run approvals, state locks, audit evidence and structured
  blocker payloads.
- Role management, SSO/OAuth, system-user, tenant/user, API-key,
  service-account and invitation administration are source-reviewed and removed
  from the Agent target runtime by explicit user decision: the Workspace Agent
  does not host this SaaS human-identity layer. Do not replace these with
  internal role CRUD, OAuth login, tenant admin or API-key management claims.
- Budget/Billing/Usage coverage is source-reviewed and removed from the Agent
  target runtime by explicit user decision: the Agent has no budget or usage
  subsystem. Do not replace this with generic attempt-limit, quota, usage or
  cost-estimation claims.
- Command/query/task/audit/QG/queue orchestration is source-reviewed and
  adapted to explicit MCP tool contracts, deterministic state transitions,
  Agent-owned analysis, task takeover, validation payloads, report readback and
  project-scoped locks. Daemon WebSocket/tmux streaming, project uploads and
  config provisioning are source-reviewed and removed because the Agent works
  from cloned repositories and MCP state, with edits only through explicit
  approval.
- Consequence: AN-9, Slack, Agent Studio, schedule and connector tests remain
  blocked until every entry is migrated, adapted, blocked with a concrete owner
  and implementation task, or removed with explicit user acceptance.

## 3. QA Gate Ownership And Task Takeover

The local Code Sentinel flow uses QA gates in scanner and workflow phases:

- preflight and QG plugins decide whether normal analysis may continue
- failing gates create technical evidence: command, exit code, files, error
  summaries and issue records
- issue records become findings
- findings become standalone tasks or parent/subtasks
- tasks are then processed by the old task-processing worker through a task
  executor

Target ownership is different:

- QA gate execution is represented by agent-native scripts and normalized
  results.
- QA gate failure must create findings and tasks. It must not be reported as a
  completed run.
- The next runnable task is assigned back to the Workspace Agent as the current
  autonomous work item.
- The Agent performs the task analysis and decides the fix plan itself.
- The Agent may request edits, command runs, commits or PRs only through the
  explicit approval policy.
- No external executor may be imported, wrapped, called, installed or kept as a
  dormant target-runtime path.

Contract statement: QA gate failure creates findings and tasks for the Workspace Agent to take over.
Contract statement: The task is not delegated to Claude, tmux, Claude CLI, or any external AI executor.

Target task handoff record:

- task_id
- parent_task_id
- subtask_sequence
- finding_ids
- qa_gate_run_id
- assigned_to: workspace_agent
- takeover_reason
- required_context_files
- proposed_fix_plan_json
- approval_required
- validation_commands_json
- status
- next_autonomous_step

The Agent's response after a failed gate must include:

1. the failing gate
2. the finding IDs or signatures
3. the created task/subtask IDs
4. the task selected for takeover
5. whether write approval is required
6. the next validation command
7. why the run is blocked, in_progress or completed

## 4. Agent Work Logic State Machine

The Workspace Agent must not operate as a loose chat assistant. It must run a
repeatable Code Sentinel work cycle with explicit states, persisted evidence and
one selected work item at a time.

### 4.1 Cycle Entry

The Agent starts a cycle when one of these triggers occurs:

- scheduled autonomous run
- user asks it to continue a project
- MCP memory says there is a next_autonomous_step
- prior QA gate, scan, finding or task is still open
- repository ref changed since the last memory snapshot

At cycle start the Agent must:

1. acquire the project lock
2. load project state, memory, active run and prior findings/tasks
3. refresh repository truth
4. build project context
5. decide whether to scan, resume a task, validate, or ask for approval

Contract statement: Every autonomous cycle chooses exactly one current_focus before doing work.

### 4.2 Project Context And File Analysis

Before creating or executing tasks, the Agent must analyze the target project:

- AGENTS.md chain and local project rules
- README and docs that describe validation or architecture
- package and build files
- test, lint, typecheck and CI configuration
- git status, branch, diff and latest ref
- relevant source files around changed or suspicious areas
- existing findings, tasks, subtasks and QA gate history

The Agent must use this context to decide:

- which files are relevant
- which checks are available
- which findings are new or duplicates
- which existing task should continue
- which validation command proves progress
- whether a write is allowed or blocked by policy

Contract statement: The Agent analyzes files before creating fix plans or claiming project improvement.

### 4.3 Work Queue Selection

The Agent pulls work from shared state in this priority order:

1. blocking QA gate task that has not been handled
2. failed validation task with clear evidence
3. in_progress subtask assigned to workspace_agent
4. pending subtask under an active parent task
5. pending standalone task
6. high severity new finding that needs task creation
7. stale project memory requiring rescan
8. improvement scan when no blocking work exists

The selected work item becomes selected_task_for_agent_takeover and must be
recorded in the run report.

Contract statement: The Agent pulls the next task or subtask from shared state instead of inventing unrelated work.

### 4.4 Task Execution By The Agent

For the selected task or subtask, the Agent must:

1. load task, finding, file and prior validation evidence
2. inspect the affected files
3. create an agent-native fix plan
4. check approval before writes
5. perform allowed edits or return a blocked approval request
6. run the narrowest validation command
7. update task/subtask status from evidence
8. update parent task progress when a subtask completes
9. persist execution session, audit event and next_autonomous_step

Allowed task statuses:

- pending
- assigned_to_agent
- in_progress
- blocked_approval_required
- blocked_validation_unavailable
- failed_validation
- completed
- cancelled

The Agent must not mark a task completed unless validation evidence proves the
task condition is fixed or the task is explicitly non-code/static-only.

Contract statement: The Workspace Agent is the task executor and must persist task status after every attempt.

### 4.5 Project Improvement Mode

When there are no blocking QA gates and no pending tasks, the Agent may improve
the project only through the same controlled pipeline:

1. run project context refresh
2. run static/file analysis
3. create findings for real issues
4. create tasks/subtasks from those findings
5. select the highest priority task
6. execute through approval and validation gates

Improvement work must not bypass findings, tasks, approval, validation, or
audit. It is still Code Sentinel work, not ad-hoc refactoring.

Contract statement: Project improvement happens through findings and tasks, never as untracked edits.

### 4.6 Cycle Exit

The Agent exits the cycle with one of these statuses:

- completed: selected task is validated and no immediate follow-up remains
- in_progress: selected task was advanced and next_autonomous_step is known
- blocked: approval, lock, repo access, validation tool, or policy blocks work
- static_only: execution is unavailable and only analysis/reporting was possible

Every exit must include:

- run_id
- project_id
- current_focus
- selected_task_for_agent_takeover when applicable
- findings_created
- tasks_created
- validation_evidence
- write_actions
- blocker_code when blocked
- next_autonomous_step

Contract statement: Every cycle exits with persisted state and a concrete next_autonomous_step.

## 5. Target Runtime Flow

One autonomous cycle must follow this order:

1. Acquire project lock through MCP state.
2. Load project, memory, prior run state and latest ref.
3. Clone or access target repo.
4. Build project context from AGENTS.md chain, README, package files, test
   configs, CI configs, git status and relevant source files.
5. Create a scan job.
6. Run preflight and blocker gates.
7. If a QA gate fails, normalize the failure into findings.
8. Run scanner plugins and command-output parsers for additional findings when
   the gate state allows it.
9. Let the Agent analyze the context and create additional reasoned findings.
10. Persist scan findings.
11. Convert findings to tasks, parent tasks and subtasks.
12. Pick the next runnable task or subtask for Workspace Agent takeover.
13. Produce an agent-native fix plan.
14. Check approval before any write, branch, commit, PR or delete.
15. Execute allowed edits or return blocked with exact approval needed.
16. Run validation commands.
17. Persist QA gate results, validation attempts, execution session and audit
    events.
18. Emit JSON report with status, evidence, next_autonomous_step and blockers.
19. Release project lock.

## 6. Current Ist/Soll Gate

This plan is the operative source of truth. Separate review documents must not
be used as the implementation driver unless they are explicitly linked back into
this plan.

### 6.1 Ist

Current agent-code-sentinel runtime already has:

- local SQLite migration runner
- basic projects, runs, memories, findings, tasks, QA gate results,
  validation_attempts, approvals, artifacts and state_locks tables
- MCP state bridge for project, memory, run start, lock acquire/release and
  event append
- basic preflight and check detection
- QA gate summary from stored gate rows
- approval-check guard
- memory/resume-cycle bootstrap
- AN-1 agent analysis contract:
  - output_contract.py for shared severity, finding-field, signature and
    no-secret JSON rules
  - agent_analysis.py for Agent-provided context, reasoned finding candidates,
    command output and file evidence normalization into findings plus fix_plan
  - analyze-context CLI command for local Agent-runtime E2E execution
- AN-2 source-compatible finding model:
  - scan_jobs, scan_findings, plugin_executions and file_checks SQLite schema
  - scan_findings.py repository helpers for scan job creation, finding
    insert/list/update by signature and duplicate suppression
  - explicit compatibility mapping from scan_findings to the existing findings
    table for current reports/tasks compatibility
- AN-3 finding-to-task/subtask pipeline:
  - task_creation.py groups open findings by affected file
  - one finding creates one standalone task
  - multiple findings in one file create one parent task with ordered subtasks
  - task signatures prevent duplicate tasks on rerun
  - parent progress/status derives from child subtask statuses
- AN-5 quality gate workflow and task takeover:
  - validation_runner.py normalizes allowed validation command results
  - qg_workflow.py records validation attempts and QA gate state
  - failed gates create source-compatible findings and tasks through the AN-2
    and AN-3 pipelines
  - qg-workflow returns selected_task_for_agent_takeover for the Workspace Agent
  - run reports include selected_task_for_agent_takeover when applicable
- AN-6 agent execution sessions:
  - agent_execution_sessions SQLite schema for agent-native session records
  - execution_sessions.py persists script name, execution method, command,
    sanitized output JSON, error summary, modified files and attempt log
  - qg-workflow records validation sessions automatically
  - execution-session CLI records analysis/fix/validation session evidence
- AN-7 autonomous cycle orchestrator:
  - run-cycle acquires/releases project state locks around one autonomous step
  - cycle.py loads memory/run state, checks write approval when requested,
    processes validation results, selects the next runnable task/subtask and
    records cycle session evidence
  - cycle.py can now collect project-context evidence from a provided
    project_path, persist a scan_job, file_checks and plugin_executions, and
    include those ids/counts in cycle session plus audit evidence
  - task_execution.py accepts an Agent-native task_execution_result, validates
    the referenced task scope, requires explicit write approval for reported
    file changes, reuses the QA-gate workflow for validation evidence, updates
    task status to completed or failed_validation and records a task execution
    session
  - validation-failed cycles create findings/tasks instead of false completion
  - blocked write cycles persist the approval blocker and release the lock
- AN-8 MCP state expansion:
  - MCP-state tools expose scan jobs, findings, tasks, QA processing, execution
    sessions, reports, audit events and PR state
  - MCP-state now exposes state_approval_record and
    state_task_execution_result so the managed Agent can record explicit
    per-run approval evidence and route task result ingestion through the same
    validation/session/task-status logic as the local runtime
  - raw SQL remains unavailable through the MCP-state dispatch
  - project locks remain the write concurrency guard for scheduled runs
- simple report counts
- source inventory extraction
- docs/tests that prevent ClaudeExecutor, Claude CLI and tmux from entering
  target runtime code

### 6.2 Soll

The Agent runtime still must implement the actual Code Sentinel work logic:

- project_context.py for AGENTS.md chain, README/docs, configs, git truth and
  relevant file selection: implemented as the next runtime work-logic building
  block and exposed through the project-context CLI plus preflight integration.
- output_contract.py expansion for task, report and blocker shapes beyond the
  AN-1 analysis/finding contract
- scan_jobs.py expansion for scan job status lifecycle, totals and completion
  handling beyond the AN-2 repository bootstrap: implemented with create,
  progress update, get and list helpers while preserving the existing
  scan_findings compatibility API.
- plugin_executions.py and file_inventory.py runtime helpers for check/file
  evidence beyond the AN-2 schema: implemented with plugin execution records,
  deterministic source-file inventory, content hashes and file_check
  persistence helpers.
- task_workflow.py expansion for richer task/subtask/parent state transitions
  beyond the AN-3 status/progress bootstrap: implemented with allowed task
  statuses, assignment, next-runnable task selection, attempt counting and
  parent progress derivation for completed, blocked and failed subtasks.
- audit_events.py expansion beyond the AN-8 MCP audit-event table helpers:
  implemented as a dedicated audit module with idempotent append/list helpers,
  CLI commands, MCP-state delegation and autonomous-cycle exit evidence.
- cycle.py expansion for broader project-context scan/improvement planning
  beyond the AN-7 one-step local orchestrator: implemented for local
  project_path intake with project_context, file_inventory, scan_job,
  plugin_execution, file_check, cycle_session and audit_event persistence.
- task_execution.py result-ingestion path for Agent-native task work: implemented
  for approved file-change evidence, validation-result processing, task status
  update, attempt increment on failed validation, execution-session persistence
  and cycle audit/report output.
- Agent Studio/Slack packaging that uses the expanded MCP-state surface from
  the managed Workspace Agent

### 6.3 Gap Rule

The migration must not be called complete while these gaps remain:

1. The local runtime and MCP adapter must be the source of truth before any
   further managed Agent Studio/Slack configuration work. If local runtime or
   trend-mcp behavior is incomplete, do not try to repair it with prompts.
2. Project improvement mode must be locally proven end to end through
   findings/tasks/audit before the managed Agent packaging is updated again.
3. Slack/Studio evidence remains useful historical proof, but it is not the
   next implementation driver while local runtime/MCP acceptance is still being
   expanded.
4. Local run-cycle project evidence E2E is implemented and tested, but the
   managed Agent packaging must wait until the local runtime, MCP adapter,
   state schema, scripts and tests are complete for the current slice.

Contract statement: The current implementation is a bootstrap adapter until AN-1 through AN-8 are implemented and validated.

Local-first reset, 2026-06-08: pause connector-snapshot, Slack and Agent Studio
work. Continue locally in this order: agent-code-sentinel runtime, trend-mcp MCP
adapter, local E2E smoke, then managed Agent configuration only after local
gates pass.

Agent-owned analysis reset, 2026-06-08: the local Code Sentinel workweise is
ported as a state machine, not copied as its old executor stack. ScanWorker,
ScanExecutionService, QGWorkflowOrchestrator, TaskCreationService and
TaskProcessingWorker define the functional flow. ClaudeExecutor, Claude
sessions and tmux define the old execution boundary and must remain source
evidence only. In the target runtime, the Workspace Agent itself reads files,
analyzes evidence, creates structured findings, selects tasks/subtasks and
decides bounded fixes. agent-code-sentinel scripts persist and validate the
Agent's structured decisions; they must not delegate analysis or execution to
another AI process.

AN-1 status: implemented locally for Agent-owned analysis contracts and
normalization. Local E2E evidence now proves project_context output selects the
Agent-owned finding target and feeds the full analyze-to-state, finding, task,
state_run_cycle and report path. Remaining completeness gate: broaden this
through managed Agent packaging only after the local runtime and MCP adapter are
complete.

AN-2 status: implemented locally for SQLite source-compatible scan jobs,
scan findings, plugin executions, file checks and compatibility findings.
Remaining completeness gate: prove duplicate signature behavior and Postgres or
explicit Postgres-blocker parity for every state tool used by the local E2E.

AN-3 status: implemented locally for finding-to-task/subtask creation,
dedupe, parent tasks, ordered subtasks and parent progress. Remaining
completeness gate: prove task/subtask status transitions across successful,
blocked and failed-validation cycles.

AN-5 status: implemented locally for validation evidence normalization,
QA-gate persistence, failed-gate findings/tasks and
selected_task_for_agent_takeover. Remaining completeness gate: prove these
QA-gate outcomes are derived from actual repository/file evidence in the
autonomous cycle, not only from isolated fixture payloads.

AN-6 status: implemented locally for agent-native execution session persistence,
validation-session integration and MCP session readback. Remaining completeness
gate: prove every Agent-owned analysis/fix/validation phase in the local E2E
records sanitized session evidence without Claude/tmux/external executor fields.

AN-7 status: implemented locally for one-step autonomous cycle orchestration,
lock handling, write-approval blocking, validation-failed task takeover,
selected task reporting, cycle session evidence and Agent-native
task_execution_result ingestion. The local runtime can now record an approved
task result, route validation through QA gates and update the task to completed
or failed_validation. This is result-ingestion evidence, not proof that the
managed Workspace Agent has performed a real bounded repository edit end to end.

AN-8 status: implemented locally for SQLite-backed MCP-state tools covering
scan jobs, findings, tasks, QA gate processing, execution sessions, reports,
audit events and PR state. Remaining completeness gate: prove the same surface
through local trend-mcp smoke against the runtime repo for the full local
run-cycle path, and keep Postgres parity honest as implemented or blocked.
Historical AN-9 foundation and expanded-state E2E runs prove that a managed
Workspace Agent can consume some MCP DB state, but they are not the next driver
while local-first completion is active.

AN-9 status: foundation and expanded-state E2E passed through the managed
Workspace Agent. Foundation Slack harness run
`runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T06-59-34-555Z`
posted only the exact execution prompt, passed evaluation with no findings, and
returned clone_head `ef9b519`, actions_used `clone_repository`,
`state_project_get`, `state_memory_get`, `run_command`,
run_command_exit_code `0`, script_execution_mode
`python_executed_from_cloned_repo`, mcp_result_consumed `true`, and
stdout_json.status `script_mcp_db_e2e_passed`. The earlier
`blocked_cloned_repo_path_not_mounted_in_agent_container` conclusion is
superseded by this run.

AN-9 expanded-state E2E status: passed in the managed Workspace Agent after the
MCP direct-tool schemas were made explicit and the MCP service was rebuilt and
restarted. Formal Slack harness run
`runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T10-08-46-161Z`
passed evaluation with no findings and returned
`status=expanded_state_e2e_passed`, repository_name `agent-code-sentinel`,
clone_head `90b3ffd`, clone_ref `origin/main`, tools
`state_scan_job_create`, `state_scan_finding_upsert`,
`state_tasks_create_from_findings`, `state_execution_session_record`,
`state_tasks_list`, `state_execution_sessions_list`, `state_report_get`
and `state_lock_release`, findings_count `1`, tasks_count `1`,
execution_sessions_count `1`, report_counts.tasks `1`,
selected_task_for_agent_takeover present and lock_released `true`.

### 6.4 Immediate Implementation Order

The next implementation work must proceed in this order:

1. Local runtime/MCP completion gate: make the agent-code-sentinel runtime and
   trend-mcp adapter prove project-improvement run-cycle behavior locally,
   including state_run_cycle, analysis-derived QA review outcomes, task
   takeover, validation/session evidence, lock handling and report readback.
2. Local repository script packaging gate: keep all scripts, schema, tests and
   usage docs in the agent-code-sentinel repository so the managed Agent can
   clone and run them later without Studio-uploaded runtime files.
3. Agent-owned analysis pipeline gate: prove locally that repository/file
   analysis supplied by the Agent creates scan findings, groups them into
   tasks/subtasks, runs QA gate classification and returns a selected
   task_for_agent_takeover without any external AI executor.
4. Managed Agent packaging gate: only after the local gates pass, reconnect or
   refresh the unpublished MCP connector and rerun Slack/Studio E2E.

Current local progress:

- Improvement work-package takeover is implemented locally. run-cycle now
  assigns the selected task to the Workspace Agent, loads compatibility finding
  and scan-finding context when available, records affected-file evidence from
  project_path, returns validation command candidates and writes session/audit
  evidence.
- Agent-native task result ingestion is implemented locally. run-cycle now
  accepts task_execution_result, blocks reported file changes without an
  approved write_request, records approved file-change validation evidence,
  persists task_execution sessions, completes successful tasks and increments
  failed-validation attempts. This still does not prove a managed Workspace
  Agent Slack/Studio E2E has executed a real bounded edit from the cloned repo.
- MCP bridge for that result-ingestion path is implemented locally and exposed
  through trend-mcp schemas. state_approval_record persists explicit approval
  evidence; state_task_execution_result applies the approved task result and
  records validation/session/task status. Local Runtime tests and trend-mcp
  state-tool listing smoke are passing. The managed Slack/Studio E2E using
  these two tools is now proven by
  `runs/workspace-agents/code-sentinel/code-sentinel-task-exec-result-mcp-e2e-20260607c`.
  That run returned `status=task_execution_result_mcp_e2e_passed`,
  `clone_head=751b8c3`, `clone_ref=origin/main`,
  `approval_recorded=true`, `task_execution_status=passed`,
  `task_status_after_execution=completed`, `approval_enforced=true`,
  `validation_attempts_count=1`, `execution_sessions_count=2`,
  `selected_task_for_agent_takeover_after_execution=null`,
  `lock_released=true`, `write_actions=[]` and
  `unapproved_actions=[]`. The original harness evaluation falsely flagged
  the task/run id text as an `sk-` shaped secret; after the evaluator boundary
  was corrected, the same live Slack response evaluates as pass with no
  findings.
- Next open implementation slice: create the bounded-edit E2E contract where
  the managed Agent selects a real pending task, inspects the affected file from
  the cloned/target repository, performs only an explicitly approved minimal
  write, runs validation, then records task_execution_result and audit evidence.
- Bounded-edit managed Slack/Studio E2E is now proven by
  `runs/workspace-agents/code-sentinel/code-sentinel-bounded-edit-agent-e2e-20260607b`.
  The managed Agent cloned `nullsoft8411/agent-code-sentinel` at
  `clone_head=ee23f47`, created branch
  `code-sentinel/bounded-edit-e2e-20260607b`, wrote only
  `docs/bounded-edit-e2e-proof-b.md`, committed `40e62b6`, pushed the
  branch, opened PR `https://github.com/nullsoft8411/agent-code-sentinel/pull/2`,
  recorded approval, called `state_task_execution_result`, completed the task,
  recorded one validation attempt, recorded two execution sessions, released the
  lock and returned `status=bounded_edit_agent_e2e_passed` with
  `unapproved_actions=[]`.
- The previous bounded-edit attempt
  `runs/workspace-agents/code-sentinel/code-sentinel-bounded-edit-agent-e2e-20260607a`
  created PR `https://github.com/nullsoft8411/agent-code-sentinel/pull/1`
  but correctly blocked task completion with
  `VALIDATION_COMMAND_NOT_ALLOWED`. That blocker was resolved by allowing the
  narrow static validation prefix `read_file + git_diff` in the runtime
  validation allowlist and by proving the adapter path before rerunning the
  managed E2E.
- QA review outcomes for reuse, duplicate code, dead code and unused code are
  now explicit runtime report fields. `qa-gates` and `report` include
  `qa_review_outcomes` for the required gates `reuse`, `duplicate_code`,
  `dead_code` and `unused_code`; missing outcomes are reported as `missing`
  instead of being silently skipped. Focused CLI tests prove both the missing
  outcome blocker and the passing case where all four outcomes are recorded.
- QA review outcome persistence through managed MCP/Slack is now proven by
  `runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T13-06-48-048Z`.
  The managed Agent cloned `nullsoft8411/agent-code-sentinel` at
  `clone_head=162b21f`, used direct MCP state tools, called
  `state_qa_gate_process` for `reuse`, `duplicate_code`, `dead_code` and
  `unused_code`, read `state_report_get`, returned all four
  `qa_review_outcomes` as `passed`, returned `missing_review_outcomes=[]`,
  recorded `report_counts.qa_gates=4`, released the lock and completed the
  Slack evaluator with `status=pass` and no findings.
- Next open implementation slice: broaden the real project-improvement cycle so
  the managed Agent derives those QA review outcomes from actual repository
  analysis during task takeover, not from an isolated QA-outcome E2E fixture.
- Local task-takeover QA review derivation is now implemented in
  `improvement_work.py`. When `run-cycle` selects a task with a readable
  affected file, the runtime reads the file, records a source snapshot, derives
  `reuse`, `duplicate_code`, `dead_code` and `unused_code` review outcomes from
  that repository evidence, persists them through `qa_gate_results`, and exposes
  them through both `improvement_work_package.qa_review_outcomes` and
  `report.qa_review_outcomes`. Focused and full local tests pass, including the
  task-takeover E2E in `tests/test_autonomous_cycle.py`.
- Next open implementation slice: expose and prove this task-takeover
  `run-cycle` path through MCP/Slack so the managed Agent can trigger the same
  repo-analysis-derived QA outcomes from its cloned workspace instead of only
  through local CLI execution.
- MCP runtime dispatch now exposes `state_run_cycle` locally. The runtime
  `mcp-state` path calls the same `run_autonomous_cycle` implementation and a
  focused MCP-state test proves task-takeover review derivation from a readable
  project path. The `trend-mcp` adapter schema and direct tool registration now
  include `state_run_cycle`; local MCP smoke lists the tool with 44 total tools
  and no missing state tools, and authenticated live `tools/list` on the
  restarted service reports `has_state_run_cycle=true`.
- Managed Slack/Studio E2E is still blocked until the ChatGPT connector session
  refreshes its approved tool snapshot. Run
  `runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T13-21-16-711Z`
  returned `STATE_RUN_CYCLE_TOOL_UNAVAILABLE`: the Agent cloned the repo at
  `clone_head=79692eb` and saw existing state tools, but did not yet see
  `state_run_cycle`, so it correctly returned blocked without faking evidence.
- After the connector was refreshed in Agent Studio, a second direct-tool Slack
  run `runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T13-25-08-997Z`
  still returned `STATE_RUN_CYCLE_TOOL_UNAVAILABLE`. A wrapper fallback run
  `runs/workspace-agents/code-sentinel/code-sentinel-2026-06-07T13-27-13-319Z`
  also returned `APPROVED_TOOL_UNAVAILABLE`: the Agent could use
  `clone_repository`, `state_project_get`, `state_memory_get` and
  `state_report_get`, but neither direct `state_run_cycle` nor wrapper
  `code_sentinel_state` was callable in the Slack tool snapshot. No write
  actions were performed and no E2E success was claimed.
- Previous next slice superseded: refreshing/reconnecting unpublished MCPc is
  paused. The local-only state_run_cycle task-takeover path is now proven by
  `PYTHONPATH=src python3 -m pytest tests/test_mcp_state.py::test_local_agent_analysis_to_cycle_e2e tests/test_mcp_state.py::test_mcp_state_analyze_to_state_records_agent_supplied_findings tests/test_project_context.py -q`
  with `5 passed`, full target runtime validation with `66 passed`, and local
  trend-mcp smoke
  `CODE_SENTINEL_RUNTIME_ROOT=/home/pika/projekte/agent-code-sentinel npm run smoke:code-sentinel-state`
  with `CODE_SENTINEL_MCP_STATE_SMOKE_PASS`. This proves tool listing,
  state_analyze_to_state, state_run_cycle, selected_task_for_agent_takeover,
  report readback, lock release, execution session count, and QA review
  outcomes with `missing_review_outcomes=[]`.
- Next open implementation slice: continue local-first with repository script
  packaging and the next still-open coverage gate. Do not return to direct
  run-cycle review MCP/Slack E2E or wrapper fallback until the local runtime,
  scripts, schema and tests for that slice are complete.
- Repository script packaging gate is now covered for the local runtime cycle:
  `scripts/agent_runtime_cycle_smoke.py` runs from the cloned repository with
  `PYTHONPATH=src python3 scripts/agent_runtime_cycle_smoke.py`, seeds a
  temporary local state database and fixture repository, consumes
  project_context output, persists Agent-owned finding candidates through
  analyze_to_state, runs the autonomous cycle logic, and returns JSON evidence
  for selected_task_for_agent_takeover, QA review outcomes, report readback and
  lock release. `tests/test_repo_scripts.py` proves the script execution
  contract with `script_execution_mode=python_executed_from_cloned_repo` and
  `external_ai_executor_used=false`.
- Next open implementation slice: continue to the next still-open coverage
  gate in section 2.6, starting with finding persistence/task dedupe and then
  Postgres parity or explicit Postgres blocker evidence for every local E2E
  state tool.
- Finding persistence/task dedupe gate is now locally covered for the
  Agent-facing MCP-state path: `tests/test_mcp_state.py::test_mcp_state_analyze_to_state_dedupes_findings_and_tasks`
  submits the same Agent finding twice through `state_analyze_to_state` and
  proves the second call returns `created=false`, creates zero additional
  tasks, and leaves report counts at one finding and one task.
- Next open implementation slice: Postgres parity remains intentionally open.
  Either implement every state tool used by the local E2E for the Postgres
  backend, or add explicit blocker evidence per tool without claiming parity.
- Postgres parity is still not implemented, but unsupported local E2E state
  tools now have explicit blocker evidence. `tests/test_mcp_state.py::test_postgres_backend_blocks_unsupported_local_e2e_state_tools`
  proves `state_memory_get`, `state_analyze_to_state`, `state_run_cycle` and
  `state_report_get` return controlled `blocked` payloads with
  `state_backend=postgres` instead of pretending execution succeeded.
- Next open implementation slice: continue section 2.6 with the next uncovered
  runtime responsibility after dedupe/Postgres-blocker evidence, without
  claiming full Postgres parity.
- AN-3 parent/subtask status coverage is now locally proven for completed,
  blocked and failed-validation transitions. `tests/test_task_creation.py::test_task_status_updates_parent_progress`
  proves completed subtasks complete the parent, and
  `tests/test_task_creation.py::test_task_status_updates_parent_progress_for_blocked_and_failed_subtasks`
  proves `blocked_approval_required` derives parent status `blocked` and
  `failed_validation` derives parent status `failed_validation` with accurate
  progress JSON.
- Next open implementation slice: continue section 2.6 with AN-4 ScanJob and
  Plugin Execution coverage. The local gate must prove scan_job status
  lifecycle, plugin_execution records, file_check readback and static/blocker
  behavior from an executable fixture path.
- AN-4 ScanJob and Plugin Execution coverage is now locally proven for both
  pass and blocker paths. `tests/test_autonomous_cycle.py::test_run_cycle_collects_project_scan_file_plugin_and_audit_evidence`
  proves run-cycle persists a completed scan_job, project_context and
  file_inventory plugin_executions, file_checks, session and audit evidence.
  `tests/test_autonomous_cycle.py::test_run_cycle_blocks_when_project_evidence_collection_fails`
  proves a missing project path returns `blocking`, persists a failed scan_job,
  records blocking plugin_executions, creates no file_checks, releases the lock
  and does not report false success.
- Next open implementation slice: continue section 2.6 with the next uncovered
  runtime responsibility after AN-4, prioritizing any remaining AN-5 QA-gate
  failure-to-finding/task takeover gaps before broader managed-agent work.
- AN-5 QA-gate failure-to-task takeover is already locally covered by
  `tests/test_qg_workflow.py::test_qg_workflow_cli_e2e_creates_findings_tasks_and_selected_takeover`,
  `tests/test_qg_workflow.py::test_qg_workflow_passed_validation_records_exact_command_and_exit_code`
  and `tests/test_qg_workflow.py::test_qg_workflow_blocks_unapproved_validation_command`.
  Together they prove failed validation creates findings/tasks and selected
  task takeover, passed validation records exact command and exit_code, and
  disallowed validation commands return controlled blockers.
- Next open implementation slice: continue section 2.6 with AN-6 Agent
  Execution Sessions, especially sanitized session evidence and the guard that
  target runtime code stays free of ClaudeExecutor, Claude CLI and tmux paths.
- AN-6 Agent Execution Sessions are locally covered by
  `tests/test_execution_sessions.py::test_execution_session_cli_e2e_stores_sanitized_agent_native_record`,
  which proves sanitized command/output/attempt_log persistence, and by
  `tests/test_docs_contract.py::test_target_runtime_has_no_external_ai_executor_code`,
  which guards `src/code_sentinel_agent` against ClaudeExecutor, Claude CLI and
  tmux target-runtime paths.
- Next open implementation slice: continue section 2.6 with AN-7 Autonomous
  Cycle Orchestrator coverage, verifying the existing local cycle tests against
  the exact one-task takeover, lock, approval, validation and report gates.
- AN-7 Autonomous Cycle Orchestrator coverage is now locally proven across
  resume, task takeover, project evidence, lock contention, approval blocking,
  validation failure, approved task result completion and failed-validation
  retry paths. `tests/test_autonomous_cycle.py::test_run_cycle_blocks_when_project_lock_is_held_by_another_writer`
  specifically proves a second writer is blocked by an active project lock
  before creating the contender run or mutating state.
- Next open implementation slice: continue section 2.6 with AN-8 MCP State
  Expansion, verifying direct MCP-state tools cover the migrated state model
  and never expose raw SQL or generic command execution.
- AN-8 MCP State Expansion is locally covered for the migrated state model by
  `tests/test_mcp_state.py::test_mcp_state_expanded_tools_persist_findings_tasks_qa_execution_audit_and_pr`,
  which persists and reads scan jobs, findings, tasks, QA gate processing,
  execution sessions, audit events and PR state through allowlisted MCP-state
  tools. `tests/test_mcp_state.py::test_mcp_state_blocks_raw_sql_and_generic_command_tools`
  proves `raw_sql`, `sql_query` and `run_command` are blocked as unknown
  MCP-state tools and are absent from the allowlist.
- Runtime repo handoff is now committed and pushed:
  `agent-code-sentinel@c6057b7` implements the Agent-owned analysis
  contract, `state_analyze_to_state`, project-evidence cycle blocking,
  repository smoke script packaging and the local AN-1 through AN-8 coverage
  gates. Validation evidence before push: `PYTHONPATH=src python3 -m pytest
  tests -q` with `79 passed`; final-quality had `fail=0` with Pytest
  entrypoint warnings only.
- Local MCP adapter handoff is now committed and pushed:
  `trend-mcp@8fa5ffc` exposes `state_analyze_to_state` in the direct
  allowlisted state surface and expands the local smoke so it starts a local
  MCP server, lists required tools, persists Agent-supplied findings, runs
  `state_run_cycle`, reads `state_report_get`, proves
  `selected_task_for_agent_takeover`, session evidence, lock release and
  `missing_review_outcomes=[]`. Validation evidence before push:
  `npm run test:cli` with 5 tests passing, `npm run type-check`, and
  `CODE_SENTINEL_RUNTIME_ROOT=/home/pika/projekte/agent-code-sentinel npm run
  smoke:code-sentinel-state` with `CODE_SENTINEL_MCP_STATE_SMOKE_PASS`.
- Next open implementation slice: AN-9 Agent Studio Packaging remains paused
  until the user explicitly resumes managed Agent configuration. When resumed,
  refresh or reconnect only the unpublished MCP connector and never publish the
  MCP app. Do not claim managed Agent completion from local AN-8/MCP evidence.

Do not jump to Agent Studio packaging or MCP expansion before the local schema,
analysis, findings and task pipeline exist.

### 6.5 QA Review Findings Integrated Into This Plan

These findings are retained as historical QA/review guardrails. They are not
the active next implementation queue when section 6.1 and section 6.4 already
contain newer runtime evidence. Treat each item below as resolved unless a fresh
test or runtime smoke regresses it. The runtime must not fall back to docs-only
claims, missing task creation, summary-only QA gates, or bootstrap-only MCP
state.

#### High: Autonomous work logic exists as plan, not runtime

Current status: resolved as a local runtime gate by AN-7 evidence in section
6.4. Keep this as a regression guard for cycle.py.

Ist:

- cycle.py currently resumes memory, creates/loads a run and emits a preflight
  next step.
- It does not yet analyze files, select a task/subtask, execute the selected
  task, validate the result or update parent/subtask state.

Soll:

- cycle.py must execute the Agent Work Logic State Machine from section 4:
  acquire lock, load context, analyze files, create/update findings, select
  exactly one task/subtask, execute through approval and validation, persist
  execution/audit state and emit next_autonomous_step.

Plan correction:

- AN-7 is blocked until AN-1, AN-2, AN-3, AN-5 and AN-6 are implemented.
- Any result that only runs resume-cycle remains bootstrap evidence, not
  autonomous Code Sentinel evidence.

#### High: Finding-to-task/subtask pipeline is missing

Current status: resolved as a local runtime gate by AN-3 evidence in section
6.4. Keep this as a regression guard for task_creation.py and task_workflow.py.

Ist:

- The schema has flat findings and minimal tasks.
- There is no task_creation.py and no task_workflow.py.

Soll:

- Findings must be grouped by file.
- One finding creates one standalone task.
- Multiple findings in one file create one parent task and ordered subtasks.
- Duplicate signatures are skipped.
- Parent status derives from subtask state.

Plan correction:

- AN-3 must be implemented before any project improvement mode or task takeover
  can be marked functional.

#### High: QA-gate failure does not create findings/tasks

Current status: resolved as a local runtime gate by AN-5 evidence in section
6.4. Keep this as a regression guard for qg_workflow.py and validation_runner.py.

Ist:

- qa_gates.py currently reads stored gate rows and returns blocking gate names.
- It does not create findings, tasks or selected_task_for_agent_takeover.

Soll:

- A failed QA gate must create normalized findings, convert them to tasks or
  subtasks, select the next runnable task for Workspace Agent takeover and
  include validation evidence.

Plan correction:

- AN-5 must include a QA-fail-to-finding/task test and cannot pass from a
  summary-only gate report.

#### High: Source-compatible state model is incomplete

Current status: resolved as a local runtime gate by AN-2 and AN-8 evidence in
section 6.4. Keep this as a regression guard for scan_jobs, scan_findings,
plugin_executions, file_checks, execution_sessions, audit_events and PR state.

Ist:

- Current SQLite schema lacks scan_jobs, scan_findings, plugin_executions,
  file_checks, agent_execution_sessions, audit_events and PR state.

Soll:

- Source-compatible runtime state must exist before MCP expansion and before
  full cycle orchestration.

Plan correction:

- AN-2 schema migration is a hard dependency for AN-4, AN-5, AN-6, AN-7 and
  AN-8.

#### Medium: MCP state bridge exposes only bootstrap tools

Current status: resolved as a local MCP gate by AN-8 evidence in section 6.4 and
the pushed trend-mcp adapter handoff. Keep this as a regression guard for the
allowlisted direct state tools and the no raw SQL/generic command boundary.

Ist:

- MCP state exposes project, memory, run start, lock acquire/release and event
  append only.

Soll:

- MCP state must expose allowlisted tools for scan jobs, findings, tasks, QA
  gates, execution sessions, audit events and PR state.

Plan correction:

- AN-8 must not start before local schema and local runtime tests pass. Raw SQL,
  arbitrary command execution and generic write tools remain forbidden.

#### Medium: Reports cannot yet prove task takeover or project improvement

Current status: resolved as a local runtime gate by AN-7/AN-8 evidence in
section 6.4. Keep this as a regression guard for reports.py and state_report_get.

Ist:

- reports.py returns run/project and counts only.

Soll:

- Reports must include selected_task_for_agent_takeover, task/subtask state,
  findings created, QA gate evidence, validation attempts, execution session IDs,
  write actions, blockers and next_autonomous_step.

Plan correction:

- AN-7 must update reports.py; Slack/Agent smoke is not valid without this
  evidence.

#### Medium: Docs contract checks intent, not runtime behavior

Current status: resolved for AN-1 through AN-8 by focused tests and local E2E
evidence in section 6.4. Keep this as a regression guard: future AN phases still
require executable tests, not docs-only assertions.

Ist:

- tests/test_docs_contract.py protects plan wording and the no Claude/tmux rule.

Soll:

- Each AN phase must add executable behavior tests for its runtime claim.

Plan correction:

- No AN phase can be marked complete from docs-only assertions.

### 6.6 QA Review Completion Gate

Before claiming any phase complete, the implementation must show:

1. focused failing or gap-covering test added first
2. implementation committed to the local runtime, not only docs
3. pytest for the affected tests passing
4. final_quality_report PASS for changed productive files
5. phase-specific E2E test passing
6. reuse/dead-code/duplicate-code check result stated
7. risk and rollback path stated
8. no false complete wording if runtime evidence is missing

Contract statement: Every AN phase requires a focused unit/integration test and a phase-specific E2E test before it can be marked complete.

Active gate after local AN-1 through AN-8 evidence: do not start AN-9 managed
Agent Studio packaging until the user explicitly resumes managed configuration.
When it resumes, the next step is an unpublished connector refresh/reconnect and
Slack/Studio E2E; MCP publishing remains forbidden.

### 6.7 Phase E2E Gate Matrix

Each phase must include an E2E test that proves the Agent-facing behavior, not
only an internal unit. E2E tests may be local CLI E2E, MCP-state E2E, or
Slack/Studio E2E depending on the phase.

| Phase | Required E2E proof |
|---|---|
| AN-1 Agent Analysis Contract | CLI E2E: pass project context JSON to analyze-context and receive normalized findings plus fix_plan JSON with no secrets. |
| AN-2 Source-Compatible Finding Model | DB E2E: initialize DB, create scan job and scan findings, dedupe by signature, list findings by project/run. |
| AN-3 Finding To Task/Subtask Pipeline | DB/CLI E2E: seed findings, create standalone task and parent/subtasks, rerun to prove duplicate signatures do not duplicate tasks. |
| AN-4 ScanJob And Plugin Execution | CLI E2E: run scan on a fixture repo, persist scan_job, plugin_execution and findings, return blocker/static_only when checks are unavailable. |
| AN-5 Quality Gate Workflow And Task Takeover | CLI E2E: seed failing QA gate or validation output, create finding/task, return selected_task_for_agent_takeover and validation evidence. |
| AN-6 Agent Execution Sessions | CLI E2E: record an agent-native execution session with command/output JSON, status, artifacts and no external executor fields. |
| AN-7 Autonomous Cycle Orchestrator | Local runtime E2E: start with project memory plus queued findings/tasks, acquire lock, select one task/subtask, update state and emit next_autonomous_step. |
| AN-8 MCP State Expansion | MCP E2E: use PIKA MCP state tools to persist/read project, run, findings, tasks, QA, execution and audit state without raw SQL. |
| AN-9 Agent Studio Packaging | Slack/Studio E2E: Agent clones repo through MCP, reads shared state, runs agent-native script, reports findings/tasks/session evidence. |

E2E evidence must include command or prompt, environment surface, exit_code or
agent response ID, stdout_json or response JSON, and the exact state mutation
observed. If any part is unavailable, the phase is blocked, not complete.

## 7. Implementation Phases

### Phase AN-0: Contract Freeze

Goal:

- Make the "Agent replaces ClaudeExecutor" rule explicit and testable.

Tasks:

1. Keep this plan linked from README.
2. Add docs-contract tests that require the agent-native executor replacement
   wording.
3. Keep claude_executor.py in the inventory only as source evidence, not as a
   target module.

Acceptance:

- README links this plan.
- Tests prove the plan states Agent-native replacement.
- No target module is named claude_executor.py.

### Phase AN-1: Agent Analysis Contract

Goal:

- Define how the Agent turns repo context and tool output into findings.

Tasks:

1. Add agent_analysis.py with dataclasses for AnalysisInput, ReasonedFinding,
   FixPlan and AnalysisResult.
2. Add JSON schema-like output contract in output_contract.py.
3. Add tests for reasoned finding normalization, severity mapping and no-secret
   output.
4. Add CLI command analyze-context that accepts JSON input and returns JSON
   findings/fix-plan skeletons.

Acceptance:

- Agent can pass context JSON into Python and receive normalized findings.
- Findings contain signature, category, severity, file, line, title,
  description, source and evidence.
- Result can be persisted without external AI references.
- E2E: analyze-context processes a fixture context JSON and returns findings and
  fix_plan JSON with no secret leakage.

### Phase AN-2: Source-Compatible Finding Model

Goal:

- Port the Source ScanFindingModel semantics into SQLite/MCP-compatible state.

Tasks:

1. Add migration for scan_jobs, scan_findings, plugin_executions and richer
   file_checks.
2. Keep current findings table as compatibility layer or map it explicitly to
   scan_findings.
3. Add repository helpers for insert/list/update-status by signature.
4. Add tests for duplicate signature, linked scan_job_id and linked run_id.

Acceptance:

- A scan job can create findings.
- Duplicate findings do not create duplicate tasks.
- Finding state can be serialized through MCP state tools later.
- E2E: DB migration plus scan_job/scan_finding insert/list/dedupe works from a
  fresh database.

### Phase AN-3: Finding To Task/Subtask Pipeline

Goal:

- Port the behavior of TaskCreationService without SQLAlchemy or Redis.

Tasks:

1. Add task_creation.py.
2. Group findings by file.
3. Create standalone tasks for one finding.
4. Create parent task plus ordered subtasks for multiple findings in one file.
5. Enforce max subtasks per parent.
6. Add task/subtask status transitions and parent progress.

Acceptance:

- One finding creates one task.
- Multiple findings in one file create one parent and N subtasks.
- Duplicate signatures are skipped.
- Parent status derives from subtask state.
- E2E: seeded findings create the expected standalone task and parent/subtasks,
  then a rerun proves duplicates are not created.

### Phase AN-4: ScanJob And Plugin Execution

Goal:

- Port Source scan orchestration into agent-native scripts.

Tasks:

1. Add scan_jobs.py with trigger-to-scan-type mapping from
   ScanExecutionService.
2. Add scanner_registry.py for portable checks.
3. Add plugin_executions.py to persist check runs.
4. Add CLI scan that creates scan_job, runs allowed scanners, persists findings
   and returns JSON.

Acceptance:

- scan creates a scan job and plugin executions.
- It can run in static-only mode if command execution is unavailable.
- It creates findings from available evidence and reports unavailable checks as
  blockers, not success.
- E2E: scan on a fixture repo persists scan_job, plugin_execution and findings
  or returns a controlled blocker/static_only result with evidence.

### Phase AN-5: Quality Gate Workflow And Task Takeover

Goal:

- Port QG state machine, validation evidence and failed-gate task takeover.

Tasks:

1. Add qg_workflow.py.
2. Add validation_runner.py with allowed command descriptors.
3. Normalize lint/type/test outputs into findings and gate records.
4. Persist gate runs, fix sessions and validation attempts.
5. On failed gate, create findings and tasks through task_creation.py.
6. Assign the next runnable task/subtask to the Workspace Agent, not an external
   executor.

Acceptance:

- Preflight blocks unsafe work.
- Failed validation creates findings/tasks instead of false completion.
- Failed QA gate returns selected_task_for_agent_takeover.
- Passed validation includes exact command and exit_code.
- E2E: failing validation output creates a finding, task/subtask and
  selected_task_for_agent_takeover with validation evidence.

### Phase AN-6: Agent Execution Sessions

Goal:

- Replace Claude sessions with agent-native execution sessions.

Tasks:

1. Add agent_execution_sessions migration if not already present.
2. Add execution_sessions.py.
3. Persist script name, execution method, command, output JSON, error summary,
   files_modified, started_at and completed_at.
4. Add sanitized attempt logs.
5. Add a guard test that target runtime code does not import or reference
   ClaudeExecutor, Claude CLI or tmux.

Acceptance:

- Every agent-native analysis/fix/validation pass has a session record.
- No Claude model, token, tmux or external-AI session field is required.
- No dormant external-executor code exists in src/code_sentinel_agent.
- E2E: execution session record stores command/output/status/artifacts and
  exposes no Claude/tmux/external executor fields.

### Phase AN-7: Autonomous Cycle Orchestrator

Goal:

- Make the Agent run the Source workweise end-to-end.

Tasks:

1. Extend cycle.py to drive lock, context, scan, findings, tasks, next task,
   approval, validation, report and lock release.
2. Add resume-cycle behavior for stale memory, fresh memory and concurrent lock
   blockers.
3. Add work queue selection in priority order.
4. Add selected_task_for_agent_takeover to run reports.
5. Add improvement mode that still goes through findings/tasks.
6. Add tests for successful cycle, blocked write cycle and validation-failed
   cycle.

Acceptance:

- The Agent can continue work from shared state without losing prior findings.
- It does not start a second writer when a lock is held.
- It emits next_autonomous_step rather than stopping at analysis.
- It pulls exactly one next task/subtask from shared state.
- It never performs untracked improvement edits.
- E2E: local autonomous cycle starts from seeded project/memory/tasks, selects
  exactly one task/subtask, updates state and emits next_autonomous_step.

### Phase AN-8: MCP State Expansion

Goal:

- Expose the migrated state model through PIKA MCP.

Tasks:

1. Add MCP tools for scan jobs, findings, tasks, QA gates, execution sessions,
   audit events and PR state.
2. Keep raw SQL unavailable.
3. Preserve per-project locking.
4. Add E2E test: Agent clones repo, reads MCP state, runs agent-native scan, and
   writes state through MCP.

Acceptance:

- MCP DB has enough state for repeated scheduled runs.
- Two agent runs cannot silently fork state.
- The Agent can read prior findings/tasks from MCP.
- E2E: PIKA MCP state tools persist and retrieve findings, tasks, QA, execution
  and audit state without raw SQL or generic command execution.

### Phase AN-9: Agent Studio Packaging

Goal:

- Put the runtime contract into the Workspace Agent surface.

Tasks:

1. Upload or refresh runtime docs/files in Agent Studio.
2. Update core instructions and skills to say the Agent itself performs
   analysis and execution decisions.
3. Include explicit script usage contracts.
4. Run Slack/Studio smoke against agent-native execution.

Acceptance:

- Studio intake proves the updated instructions/files are present.
- Slack test proves the Agent uses MCP clone/state plus its own Python execution.
- Response includes findings/tasks/session evidence.
- E2E: Slack/Studio run returns clone/ref, MCP state evidence, script execution
  evidence and created/read findings/tasks/session evidence.

## 8. Runtime Layers Not Ported, Functionality Still Ported

These old product/runtime layers are not migration targets because the Workspace
Agent does not need them as infrastructure:

- external Claude CLI execution
- tmux as execution runtime
- ClaudeExecutor or any wrapper around it
- dormant external-AI executor modules
- Claude token/model budget as target accounting
- FastAPI product API
- React admin UI
- Redis Streams as required queue
- full tenant/RBAC SaaS implementation
- Stripe/billing
- Kubernetes deployment

This does not mean their Code Sentinel functionality is dropped. The functional
workweise is still migrated and adapted:

- API orchestration becomes CLI/script contracts plus Agent Studio/Slack
  instructions.
- PostgreSQL persistence semantics become SQLite and MCP shared-state schema.
- Redis stream delivery becomes state_events, locks, run lifecycle and
  next_autonomous_step.
- Tenant/RBAC safety becomes project/workspace scope policy, write approval and
  audit gates.
- Budget guard becomes attempt limits, write limits, validation gates and
  blocker states.
- Claude/tmux execution becomes Workspace Agent task takeover, agent-native
  analysis, fix planning, execution sessions and validation evidence.
- SaaS audit logs become audit_events and final JSON reports.

Contract statement: Infrastructure is not ported 1:1, but Code Sentinel's functional workweise is migrated into the Workspace Agent runtime.

## 9. Definition Of Done

The migration is not complete until:

1. The Agent can create findings itself from repo context, command output and
   static/source analysis.
2. Findings can be persisted in shared MCP-backed state.
3. Findings can create jobs, parent tasks and subtasks.
4. The Agent can choose and execute the next allowed autonomous task.
5. Write actions require explicit approval and are audited.
6. Validation evidence is persisted with command and exit_code.
7. Agent-native execution sessions replace old Claude sessions.
8. Repeated scheduled runs continue from prior MCP state.
9. QA gates include reuse, duplicate code, dead code and unused code outcomes.
10. Failed QA gates create findings/tasks and the selected task is taken over by
    the Workspace Agent.
11. Agent work cycles pull one task/subtask, analyze files, execute through
    approval and validation, then persist next_autonomous_step.
12. Project improvement work always goes through findings/tasks and audit.
13. Current Ist/Soll gap rule is empty or explicitly superseded by newer
    validated implementation evidence.
14. Every AN phase has a passing phase-specific E2E test.
15. No final response claims completion without runtime evidence.
