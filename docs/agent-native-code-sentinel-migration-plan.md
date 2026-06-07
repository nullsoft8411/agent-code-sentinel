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
- execution_sessions.py: persists agent-native execution sessions without
  referencing an external AI provider.
- policy.py: enforces write scope, command scope, approval and budget-like
  attempt limits.

The Agent is the intelligence layer. Python scripts provide deterministic
state, extraction, normalization, validation and reporting.

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
- simple report counts
- source inventory extraction
- docs/tests that prevent ClaudeExecutor, Claude CLI and tmux from entering
  target runtime code

### 6.2 Soll

The Agent runtime still must implement the actual Code Sentinel work logic:

- project_context.py for AGENTS.md chain, README/docs, configs, git truth and
  relevant file selection
- output_contract.py expansion for task, report and blocker shapes beyond the
  AN-1 analysis/finding contract
- scan_jobs.py expansion for scan job status lifecycle, totals and completion
  handling beyond the AN-2 repository bootstrap
- plugin_executions.py and file_inventory.py runtime helpers for check/file
  evidence beyond the AN-2 schema
- task_workflow.py expansion for richer task/subtask/parent state transitions
  beyond the AN-3 status/progress bootstrap
- execution_sessions.py for agent-native run evidence
- audit_events.py for approval, write, validation and state transition audit
- cycle.py orchestration that pulls exactly one next task/subtask, analyzes
  files, executes through approval/validation, persists state and emits
  next_autonomous_step
- MCP tools for scan jobs, findings, tasks, QA gates, execution sessions, audit
  events and PR state

### 6.3 Gap Rule

The migration must not be called complete while these gaps remain:

1. No selected_task_for_agent_takeover in runtime reports.
2. No executable finding-to-task/subtask pipeline.
3. No QA-gate-failure-to-task behavior.
4. No source-compatible scan_jobs, scan_findings or plugin_executions schema.
5. No agent-native execution_sessions table/module.
6. No validation_runner that records command, cwd, exit_code and summaries.
7. No MCP state tools for findings, tasks, QA, execution and audit.
8. No project improvement mode that goes through findings/tasks/audit.

Contract statement: The current implementation is a bootstrap adapter until AN-1 through AN-8 are implemented and validated.

AN-1 status: implemented locally for analysis-only execution. It does not yet
persist scan findings, create tasks/subtasks or drive QA-gate takeover; those
remain AN-2, AN-3 and AN-5 work.

AN-2 status: implemented locally for SQLite schema and repository E2E. It does
not yet create tasks/subtasks, normalize QA-gate failures into tasks or expose
the richer finding state through MCP; those remain AN-3, AN-5 and AN-8 work.

AN-3 status: implemented locally for finding-to-task/subtask creation and
status/progress updates. It does not yet convert failing QA gates into findings
and selected_task_for_agent_takeover; that remains AN-5 work.

AN-5 status: implemented locally for validation evidence normalization,
QA-gate persistence, failed-gate findings/tasks and
selected_task_for_agent_takeover. It does not yet persist full agent execution
sessions or drive the autonomous cycle; those remain AN-6 and AN-7 work.

### 6.4 Immediate Implementation Order

The next implementation work must proceed in this order:

1. AN-6 Agent Execution Sessions.
2. AN-7 Autonomous Cycle Orchestrator.
3. AN-8 MCP State Expansion.
4. AN-9 Agent Studio Packaging.

Do not jump to Agent Studio packaging or MCP expansion before the local schema,
analysis, findings and task pipeline exist.

### 6.5 QA Review Findings Integrated Into This Plan

These findings are the current QA/review result and must drive the next
implementation tasks.

#### High: Autonomous work logic exists as plan, not runtime

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
