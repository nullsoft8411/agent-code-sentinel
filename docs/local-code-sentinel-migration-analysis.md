# Local Code Sentinel To Agent Code Sentinel Migration Analysis

Status: analysis_for_migration
Created: 2026-06-07
Source runtime: /home/pika/projekte/code-sentinel
Target runtime: /home/pika/projekte/agent-code-sentinel
Agent surface: ChatGPT Workspace Agent "Code Sentinel"
Shared state bridge: PIKA MCP state tools

## 1. Ziel

Das lokale Code-Sentinel-Projekt soll nicht 1:1 als SaaS in den Workspace Agent kopiert werden. Das Ziel ist, die komplette portable Arbeitsweise und so viel ausführbare Logik wie möglich in agent-code-sentinel zu migrieren, damit der Workspace Agent nach diesem Ablauf autonom arbeiten kann:

1. Projekt oder Repo bestimmen.
2. Repo über PIKA MCP klonen oder anderweitig verfügbar machen.
3. Gemeinsamen State über MCP DB lesen und schreiben.
4. Portable Python-Logik aus agent-code-sentinel in der Agent-Ausführungsumgebung starten.
5. Findings, Tasks, QA Gates, Validierung, Approvals, Reports und nächsten autonomen Schritt mit echter Evidenz ausgeben.
6. Keine falschen Completion-Claims, keine unapproved Writes, keine Secrets.

Der erste echte Beweis ist bereits erbracht: Der Agent konnte agent-code-sentinel klonen, MCP-State lesen und scripts/agent_mcp_result_probe.py agent-nativ ausführen. Die richtige Architektur ist also nicht "MCP run_command führt alles aus", sondern "MCP liefert Repo/State, Agent führt Runtime-Scripts aus".

## 2. AGENTS.md- und Regelkontext

Für diese Analyse gelten:

- /home/pika/AGENTS.md
- /home/pika/projekte/AGENTS.md
- /home/pika/projekte/agents/AGENTS.md
- /home/pika/projekte/code-sentinel/AGENTS.md fuer Source-Repo-Verhalten
- lokale Code-Sentinel-AGENTS-Regel: Python-/Service-Projekt mit domain, infrastructure, workers, API, Docker, Kubernetes; Reuse, Duplicate Code, Dead Code und unused code sind explizite QA-Felder.

Wichtige Source-Regeln:

- Tenant-Awareness, RBAC, Security, Audit, Budget, Worker-Retry und Task-Status sind Core-Risiken.
- Kein neuer Helper oder Script ohne vorherige Suche nach bestehender Implementierung.
- Keine Behauptung von Tests, Fixes, Commits oder PRs ohne Evidenz.
- Docker/Kubernetes/Postgres/Redis sind Source-Runtime, aber fuer den Agent nur soweit relevant, wie ihre Arbeitslogik portierbar ist.

## 3. Source Runtime Ist-Zustand

### 3.1 Produkt und Stack

Source README beschreibt Code-Sentinel als Multi-Tenant SaaS fuer automatisierte Code-Analyse und Quality-Gate-Workflows.

Beobachteter Stack:

- Backend: Python 3.11, FastAPI, SQLAlchemy 2, asyncpg, Alembic
- Datenbank: PostgreSQL
- Messaging: Redis Streams und PubSub
- Frontend: React, TypeScript, Vite, Tailwind
- Auth/RBAC: JWT, API Keys, Tenant Context
- Billing/Budget: Stripe, Budget Guards
- Execution: ClaudeExecutor, tmux/session store, QGTestRunner
- Deployment: Docker, Kubernetes
- Tests: 39 Python-Testdateien in unit, integration, e2e, security

### 3.2 Kernarchitektur

Source-Pfade:

- api/main.py: FastAPI Composition
- presentation/api/controllers/*: HTTP/API Grenzen
- presentation/daemon/orchestrator.py: Runtime Composition Root
- workers/task_processing_worker.py: Task-Fix-Worker
- workers/stream_task_worker.py: Redis Stream Worker
- workers/scan_worker.py: Scan/QG/Rework Worker
- workers/scan_coordinator.py: Event-driven Scan Scheduling
- workers/pr_monitor_worker.py: PR Status Monitoring
- application/task_service.py: Task Use Cases
- application/scanner_service.py: Scanner Use Cases
- application/git_service.py: Branch, Commit, PR, Sync
- application/services/task_creation_service.py: Finding zu Task/Subtask
- application/services/code_scanner_orchestrator.py: Plugin-Orchestrierung
- infrastructure/messaging/stream_service.py: Redis Streams, ACK/NACK, DLQ
- infrastructure/execution/claude_executor.py: bisheriger Fix Executor
- infrastructure/execution/qg_test_runner.py: Test/QG Ausführung
- infrastructure/persistence/models/*.py: echtes PostgreSQL-Schema
- infrastructure/persistence/*_repository.py: DB-Zugriff und Mapping

### 3.3 Source DB-Schema: relevante Tabellenfamilien

Extrahierte Tabellen aus SQLAlchemy-Modellen:

Runtime/State:

- health_bot_state
- projects
- health_bot_tasks
- health_bot_task_logs
- health_bot_task_history
- qg_workflows
- quality_gate_runs
- quality_gate_fix_sessions
- quality_gate_states
- health_bot_scan_jobs
- health_bot_scan_findings
- health_bot_file_checks
- health_bot_file_analyses
- health_bot_plugin_executions
- health_bot_claude_sessions
- tracked_prs
- audit_logs

Security/Tenant/Budget:

- tenants
- tenant_usage_records
- users
- api_keys
- service_accounts
- budget_limits
- usage_records

Integrations:

- github_app_installations
- github_app_repositories
- webhook_configs
- webhook_deliveries

Nicht-agent-notwendig, aber als Referenz wichtig:

- contact_messages
- sales_leads
- logging_configs
- billing-related records

### 3.4 Source Arbeitsweise

Code-Sentinel ist kein einzelner Scanner. Es ist ein kontrollierter autonomer Workflow:

1. Projekt/Repo aufnehmen.
2. Projektregeln und Tooling erkennen.
3. ScanJob erstellen.
4. Blocker-first Plugins ausführen.
5. Findings erfassen.
6. Findings in Tasks/Subtasks übersetzen.
7. Task priorisieren.
8. Optional isolierte Worktree-/Branch-Umgebung anlegen.
9. Execution Preflight ausführen.
10. Fix ausführen.
11. Tests und Quality Gates ausführen.
12. Ergebnisse, Logs, Sessions, Kosten und Status persistieren.
13. Commit/PR oder lokaler Status.
14. PR-Monitoring/Rework.
15. Audit, Memory/State und Next Step fortschreiben.

## 4. Target Runtime Ist-Zustand agent-code-sentinel

Aktuelle Module:

- src/code_sentinel_agent/cli.py
- src/code_sentinel_agent/db.py
- src/code_sentinel_agent/mcp_state.py
- src/code_sentinel_agent/postgres_state.py
- src/code_sentinel_agent/check_detection.py
- src/code_sentinel_agent/preflight.py
- src/code_sentinel_agent/qa_gates.py
- src/code_sentinel_agent/approvals.py
- src/code_sentinel_agent/memory.py
- src/code_sentinel_agent/cycle.py
- src/code_sentinel_agent/reports.py
- scripts/agent_mcp_result_probe.py

Aktuelle SQLite-Tabellen:

- projects
- runs
- memories
- findings
- tasks
- qa_gate_results
- validation_attempts
- approvals
- artifacts
- state_locks

Aktuelle Tests:

- test_db_schema.py
- test_cli_contract.py
- test_mcp_state.py
- test_write_guard.py
- test_autonomous_cycle.py

Aktuelle Fähigkeit:

- SQLite init
- MCP state read/write subset
- project/memory read
- state locks
- run start
- event append
- basic check detection
- basic preflight
- QA gate summary
- approval-check
- memory-delta
- report
- resume-cycle
- agent-native MCP-result probe

## 5. Gap-Analyse: Source zu Target

### 5.1 Bereits migriert oder teil-migriert

| Source-Konzept | Target-Status | Bewertung |
|---|---:|---|
| projects | vorhanden, minimal | braucht mehr Felder |
| runs | vorhanden | braucht lifecycle/events/attempt detail |
| memories | vorhanden | braucht repo-state Struktur |
| findings | vorhanden | braucht scanner metadata/status/task linkage |
| tasks | vorhanden | braucht severity/type/source/retry/git fields |
| qa_gate_results | vorhanden | braucht gate taxonomy und command evidence |
| validation_attempts | vorhanden | braucht richer stdout/stderr/artifact fields |
| approvals | vorhanden | braucht consumed semantics und exact write policies |
| artifacts | vorhanden | braucht kind taxonomy, hash, source labels |
| state_locks | vorhanden | korrekt für shared MCP state |

### 5.2 Noch nicht ausreichend migriert

| Source-Konzept | Source-Pfad | Target-Gap |
|---|---|---|
| Finding zu Task | application/services/task_creation_service.py | Kein task_creation Modul, keine Dedupe-Signaturen, keine Parent/Subtask-Erzeugung |
| ScanJob | models/scan_job.py, scan_execution_service | Kein ScanJob-Modell im Agent-Repo |
| ScanFinding | models/scan_finding.py | Target findings sind zu generisch |
| PluginExecution | models/plugin_execution_model.py | Keine Plugin-/check execution records |
| FileCheck/FileAnalysis | models/file_check.py, file_analysis_model.py | Keine file inventory/hotspot state |
| QGWorkflow | models/qg_workflow.py | Kein Phasenmodell preflight/qg/fix |
| QualityGateRun/FixSession/State | models/quality_gate.py | Target hat nur flache qa_gate_results |
| TaskProcessingWorker state | workers/task_processing_worker.py | Kein agent-runner cycle fuer task workflow |
| Redis stream delivery | stream_service.py, stream_task_worker.py | Muss als SQLite/MCP event queue emuliert werden |
| PR tracking | tracked_pr.py, pr_monitor_worker.py | Kein PR status/rework Modell |
| ClaudeSession/ExecutionSession | claude_session_model.py, claude_executor.py | Kein agent execution session model |
| Audit logs | audit_log.py | Keine audit_events Tabelle |
| Budget/Usage | budget.py, usage.py | Kein Budget Guard Aequivalent; fuer Agent nur policy reference oder lightweight ledger |
| Tenant/RBAC | tenant.py, user.py | Nicht 1:1 nötig, aber Workspace/Project scope policy fehlt als Datenmodell |
| GitService | application/git_service.py | Nur indirekt über MCP/GitHub; agent runtime braucht command contracts/status |
| Scanner plugins | domain/scanning/plugins | Keine portable plugin registry im Agent-Repo |
| QG test runner | qg_test_runner.py | Noch kein echter command runner fuer agent-native checks |

### 5.3 Bewusst nicht 1:1 migrieren

Diese Teile bleiben reference_only oder MCP/external:

- FastAPI Produkt-API
- React Admin UI
- Kubernetes Deployment
- Docker Compose Produktstack
- Stripe/Billing
- vollständige Tenant/RLS Implementierung
- Redis Streams als echte Infrastruktur
- tmux Session Management
- Claude CLI Executor
- raw secrets/env/auth implementations
- GitHub App Installation internals

Sie werden aber als Arbeitsprinzipien migriert:

- Scope/Tenant Safety -> project/workspace policy
- Budget Guard -> run budget/status policy
- Redis delivery -> state_events/run queue/locks
- tmux sessions -> agent_execution_sessions/artifacts
- API audit -> audit_events
- GitHub App -> MCP/GitHub connector contracts

## 6. Korrekte Zielarchitektur

### 6.1 Agent-native Runtime

Agent-code-sentinel muss ein Python-Paket bleiben, das in der ChatGPT-Agent-Ausführungsumgebung laufen kann.

Agent-native Scripts:

- duerfen MCP Ergebnisse als JSON entgegennehmen
- duerfen Repo-Dateien analysieren, wenn der Agent Zugriff auf den Clone/Files hat
- geben ausschließlich JSON aus
- liefern exit_code, status, blocker_code, evidence, next_action
- duerfen keine Secrets ausgeben
- duerfen ohne Approval keine Writes ausführen

### 6.2 MCP Rolle

PIKA MCP ist nicht der Haupt-Executor. MCP ist Bridge fuer:

- clone_repository
- read/list files falls Agent eigenen Clone-Pfad nicht direkt lesen kann
- state_project_get
- state_memory_get
- state_lock_acquire/release
- state_run_start
- state_append_event
- perspektivisch weitere state_* Tools
- optional GitHub/PR/File Writes mit expliziter Freigabe

MCP run_command ist fuer diesen Migrationspfad nicht die primäre Runtime. Es kann als separater MCP-Server-Smoke existieren, aber nicht als Beweis fuer agent-native Code-Sentinel-Logik.

### 6.3 Persistence

Zwei Persistenzmodi:

1. Agent-local SQLite fuer lokale Tests und packaged runtime.
2. Shared MCP DB fuer echte Agent-Runs und parallele Schedules.

Agent-local DB bleibt Entwicklertest. Shared MCP DB ist die Wahrheit für wiederkehrende Workspace-Agent-Runs.

## 7. Ziel-Schema fuer agent-code-sentinel

### 7.1 Pflichttabellen Phase 1

Aktuelle Tabellen behalten und erweitern:

projects:

- id
- target
- repository_url
- default_branch
- active_ref
- status
- project_type
- languages_json
- framework
- scan_patterns_json
- exclude_patterns_json
- write_policy
- created_at
- updated_at

runs:

- id
- project_id
- mode
- status
- current_phase
- current_focus
- blocker_code
- blocker_reason
- next_autonomous_step
- attempt_count
- max_attempts
- started_at
- completed_at
- created_at
- updated_at

state_events:

- id
- project_id
- run_id
- event_type
- status
- payload_json
- created_at

agent_execution_sessions:

- id
- project_id
- run_id
- script_name
- execution_method
- status
- command
- exit_code
- stdout_json
- stderr_summary
- started_at
- completed_at

audit_events:

- id
- project_id
- run_id
- action
- resource_type
- resource_id
- details_json
- created_at

### 7.2 Pflichttabellen Phase 2

scan_jobs:

- id
- project_id
- run_id
- scan_type
- triggered_by
- status
- files_total
- files_scanned
- files_skipped
- issues_found
- started_at
- completed_at
- error_message

scan_findings:

- id
- scan_job_id
- project_id
- run_id
- task_id
- scanner_name
- rule_id
- file_path
- line_number
- column_number
- severity
- status
- title
- message
- suggestion
- code_snippet
- extra_json
- signature

plugin_executions:

- id
- project_id
- run_id
- scan_job_id
- plugin_id
- plugin_name
- plugin_priority
- status
- started_at
- completed_at
- issue_count
- files_scanned
- checks_run_json
- error_message

file_checks:

- id
- project_id
- file_path
- file_hash
- last_checked_at
- issues_found
- duplicate_code_found
- dead_code_found
- security_issues
- dependency_findings_count
- analysis_results_json
- skip_scanning
- skip_reason

### 7.3 Pflichttabellen Phase 3

tasks erweitern:

- task_type
- severity
- description
- detection_source
- source_file
- source_line_number
- error_pattern
- status
- priority
- attempts
- max_attempts
- fix_branch
- commit_hash
- pr_url
- analysis_result_json
- fix_strategy
- implemented_fix
- tests_passed
- test_results_json
- parent_task_id
- subtask_sequence
- subtasks_total
- rework_feedback
- continue_session

qg_workflows:

- id
- project_id
- run_id
- current_phase
- work_branch
- base_branch
- preflight_completed
- qg_check_completed
- fix_completed
- error_message
- started_at
- completed_at

quality_gate_runs:

- id
- project_id
- run_id
- workflow_id
- gate_type
- status
- error_count
- warning_count
- files_affected
- raw_output
- branch_name
- command
- exit_code
- attempt_number
- total_fix_iterations
- files_fixed_json
- error_message

quality_gate_fix_sessions:

- id
- qg_run_id
- execution_session_id
- status
- iteration
- files_fixed_json
- errors_before
- errors_after
- error_message

pr_states:

- id
- project_id
- run_id
- task_id
- pr_number
- pr_url
- branch_name
- status
- checks_status
- mergeable
- feedback_count
- rework_count
- review_feedback
- last_checked_at

## 8. Ziel-Module fuer agent-code-sentinel

### 8.1 Bereits vorhanden, erweitern

- db.py: Migration Runner, Schema-Versionierung
- mcp_state.py: shared state tools
- postgres_state.py: optional backend
- check_detection.py: Tooling Detection
- preflight.py: Projektstart
- qa_gates.py: Gate-Auswertung
- approvals.py: Write Guard
- memory.py: Memory Delta
- cycle.py: Resume Cycle
- reports.py: Run Report
- cli.py: JSON CLI

### 8.2 Neu benoetigte Module

- project_context.py
  - AGENTS.md, README, package files, CI, test configs laden
  - Repo root rules chain ausgeben
- scanner_registry.py
  - portable plugin registry fuer blocker/normal checks
- scan_jobs.py
  - ScanJob lifecycle und status
- scan_findings.py
  - Finding model, signatures, status
- task_creation.py
  - Finding -> Task/Subtask
- task_workflow.py
  - Task lifecycle ohne daemon
- qg_workflow.py
  - preflight -> qg_check -> fix -> validate state machine
- validation_runner.py
  - agent-native allowed command descriptor, result normalization
- execution_sessions.py
  - script run evidence
- audit_events.py
  - sanitized event log
- pr_state.py
  - PR metadata/status ohne GitHub App internals
- file_inventory.py
  - file checks, hotspots, stale file state
- policy.py
  - scope/write/budget/tenant-like safety rules
- output_contract.py
  - common JSON status/blocker schemas

## 9. Migrationsphasen

### Phase M0: Contract Repair und Test Harness

Ziel:

- Workflow-Case muss den richtigen Pfad testen: MCP clone + MCP DB + agent-native Python.
- Kein MCP run_command als Runtime-Beweis.

Tasks:

1. workflows/code-sentinel-agent-repo-mcp-script-e2e.json auf native execution behalten.
2. tests/docs-contract.test.js gegen neuen Marker AGENT_REPO_NATIVE_SCRIPT_E2E validieren.
3. Live Slack direct prompt evidence als Referenz dokumentieren.
4. Falls Harness verwendet wird, Evaluator auf native script_execution_mode ausrichten.

Acceptance:

- node --test tests/docs-contract.test.js tests/workflow-case-validation.test.js passed.
- Live direct Slack proof bleibt referenzierbar: clone_head 42461c5, exit_code 0, script_mcp_db_e2e_passed.

### Phase M1: Schema Migration Core

Ziel:

- Source-Schema-Kern in agent-compatible SQLite/Postgres State spiegeln.

Tasks:

1. Neue Migration 003_runtime_schema.sql.
2. Tabellen state_events, agent_execution_sessions, audit_events.
3. projects/runs/tasks/findings/qg Felder erweitern.
4. Tests fuer idempotente Migration und roundtrip.

Acceptance:

- pytest tests/test_db_schema.py.
- Keine alten Tabellen brechen.
- final_quality_report PASS.

### Phase M2: Finding/Task Pipeline

Ziel:

- Source TaskCreationService Arbeitsweise portieren.

Tasks:

1. scan_findings.py mit Signaturbildung.
2. task_creation.py mit grouping, dedupe, parent/subtask.
3. CLI: finding add, task create-from-findings.
4. Tests fuer single finding, multiple findings, duplicate signature, max subtasks.

Acceptance:

- JSON output listet created/skipped/deduped.
- Tasks verlinken auf findings.
- Duplicate Signaturen erzeugen keine parallelen Tasks.

### Phase M3: ScanJob und Plugin Execution

Ziel:

- Source ScanExecutionService/PluginExecution ohne Redis/API portieren.

Tasks:

1. scan_jobs.py.
2. plugin_executions.py.
3. scanner_registry.py fuer portable checks.
4. CLI: scan start, scan complete, plugin record.
5. Blocker-first order als Tests.

Acceptance:

- Blocker Plugin stoppt normale Plugins.
- Plugin execution records enthalten status, checks_run, issue_count.
- ScanJob issues_found aktualisiert.

### Phase M4: Project Context und Preflight Deepening

Ziel:

- Agent liest Repo-Regeln und Tooling so wie Code-Sentinel Preflight.

Tasks:

1. project_context.py.
2. AGENTS.md chain loader.
3. README/CI/package/test config detector.
4. check_detection.py erweitern um Node/Python/Go/Rust/PHP/Java Shell detection.
5. preflight.py gibt blockers, checks, first_work_path.

Acceptance:

- Fixture-Repos fuer Node/Python/Go.
- Preflight blockt missing path und unklare Regeln.
- Output enthält project_rules_loaded und validation_candidates.

### Phase M5: QA Gate Runtime

Ziel:

- QualityGateRun/State/FixSession Logik portieren.

Tasks:

1. quality_gate_runs Tabelle nutzen.
2. qa_gates.py erweitert gates:
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
3. validation_runner.py fuer command descriptors.
4. Tests fuer blocking/continuation/advisory.

Acceptance:

- Kein "tests passed" ohne command/exit_code.
- Gate result enthält evidence, why_it_matters, next_action.
- Reuse/dead/duplicate/unused immer im finalen Report.

### Phase M6: Agent Execution Sessions

Ziel:

- Agent-native Script-Ausführung als persistente Evidence.

Tasks:

1. execution_sessions.py.
2. CLI: execution record.
3. scripts runner contract fuer agent-native use.
4. stdout_json parser und artifact linking.
5. Tests fuer exit_code 0/1/2.

Acceptance:

- Der erfolgreiche Slack-Probe kann als execution_session persistiert werden.
- status blocked/static_only/passed korrekt.

### Phase M7: Autonomous Cycle Orchestrator

Ziel:

- Source TaskProcessingWorker als single-run agent cycle portieren.

Tasks:

1. task_workflow.py state machine.
2. cycle.py erweitert:
   - acquire lock
   - load memory
   - project context
   - scan/preflight
   - create tasks
   - qa gates
   - approval check
   - report next step
3. Keine long-running daemon Annahme.
4. Tests fuer resume, blocker, retry boundary.

Acceptance:

- Ein Lauf erzeugt deterministischen JSON Report.
- Bei fehlender approval: PR-ready plan, keine Writes.
- Bei validiertem blocker: status blocked mit next_action.

### Phase M8: PR/Git State Without Product GitHub App

Ziel:

- PR-Arbeitsweise portieren, nicht GitHub App internals.

Tasks:

1. pr_state.py.
2. CLI: pr record, pr status update.
3. GitHub/MCP tool result ingestion.
4. Rework feedback state.
5. Tests fuer PR created/merged/closed/rework_requested statuses.

Acceptance:

- Keine PR-Behauptung ohne tool evidence.
- Rework erzeugt task state, aber keine neuen Writes ohne Freigabe.

### Phase M9: MCP Tool Expansion

Ziel:

- Shared DB deckt Source-Runtime-Tabellen ab.

Neue MCP Tools ableiten aus Schema, nicht Bauchgefühl:

- state_run_get
- state_run_update
- state_event_list
- state_scan_job_create
- state_scan_job_update
- state_finding_add
- state_findings_list
- state_task_create
- state_tasks_list
- state_qa_gate_record
- state_execution_session_record
- state_audit_append
- state_report_get
- state_pr_state_record

Acceptance:

- tools/list zeigt neue Tools.
- Direkter MCP smoke fuer create/list/update.
- Agent-native script kann MCP results konsumieren.

### Phase M10: Packaging fuer Agent Studio

Ziel:

- Agent kann Runtime zuverlässig nutzen.

Optionen:

1. Repo clone + agent-native execution, aktueller Proof.
2. Uploaded zip/runtime pack als fallback.
3. Einzelne uploaded scripts fuer kleine probes.

Tasks:

- README agent usage.
- runtime pack manifest.
- script entrypoint contract.
- file upload/update runbook.
- Slack direct prompt and harness workflow.

Acceptance:

- Slack direct prompt liefert exit_code 0.
- Harness-Case nutzt korrekten nativen Pfad.
- Kein run_command required.

## 10. Teststrategie

### Lokale Tests agent-code-sentinel

- PYTHONPATH=src python3 -m pytest tests -q
- Fokus je Phase:
  - test_db_schema.py
  - test_mcp_state.py
  - test_cli_contract.py
  - test_write_guard.py
  - neue tests/test_task_creation.py
  - neue tests/test_scan_jobs.py
  - neue tests/test_qg_workflow.py
  - neue tests/test_execution_sessions.py

### Agents Repo Tests

- node --test tests/docs-contract.test.js tests/workflow-case-validation.test.js
- Slack direct prompt fuer native execution.
- Harness nur nach Contract-Korrektur.

### Trend MCP Tests

- npm run type-check
- npm run test:cli
- npm run build
- direct tools/list and tools/call smoke

## 11. Reuse/Dead/Duplicate Bewertung

Reuse:

- Source local project APIs wurden analysiert: models, repositories, workers, scanner services, docs.
- Existing agent-code-sentinel adapter wird erweitert, nicht ersetzt.
- Bestehende MCP state tools bleiben Basis.
- Existing tests werden erweitert.

Dead/unused risk:

- Aktuelle .pytest_cache und __pycache__ im agent-code-sentinel Workspace sind nicht Bestandteil der Migration und duerfen nicht committed werden.
- Jede neue Phase braucht final_quality_report gegen geänderte Dateien.
- Unused exports wie GithubProjectId duerfen nicht wieder eingeführt werden.

Duplicate risk:

- Keine zweite Agent-Runtime neben agent-code-sentinel.
- Keine zweite MCP-Service-Implementierung.
- Kein paralleles "run_command runtime" als Hauptpfad.
- Keine doppelten task/finding modules ohne Migration alter flacher Funktionen.

## 12. Offene Blocker und Entscheidungen

Blocker A: Agent-Dateizugriff auf MCP clone path

Der Slack-Beweis zeigt, dass der Agent das Script aus dem geklonten Repo agent-nativ ausführen konnte. Trotzdem muss der Workflow robust dokumentieren, was passiert, wenn der clone path nicht agent-executable ist: CLONED_REPO_NOT_AGENT_EXECUTABLE.

Blocker B: Shared DB Umfang

Aktuelle MCP DB deckt nur Projekt/Memory/Lock/Run/Event ab. Für komplette Workweise müssen Findings, Tasks, QG, Execution Sessions, Audit und PR State in MCP bereitgestellt werden.

Blocker C: Write Policy

Write-Actions sind noch nicht Teil der Migration. Vor jeder File-/Branch-/Commit-/PR-Aktion braucht es approval records und Slack/Studio prompt contract.

Blocker D: Full SaaS features

Tenant, RBAC, Billing, Stripe, FastAPI, UI, Kubernetes und Redis werden nicht in den Agent portiert. Nur ihre Arbeitsprinzipien werden in lightweight policy/state übersetzt.

## 13. Definition of Done fuer die vollständige Migration

Die Migration ist erst fertig, wenn alle Punkte erfüllt sind:

1. agent-code-sentinel enthält portable Runtime-Module fuer project context, scans, findings, tasks, QA gates, execution sessions, approvals, reports, cycle.
2. SQLite Tests decken Schema und CLI-Roundtrips ab.
3. MCP Tools decken shared state fuer Project, Memory, Runs, Events, Findings, Tasks, QA, Execution, Audit und PR State ab.
4. Agent kann Repo klonen, MCP DB lesen, Script agent-nativ ausführen und Ergebnisse persistieren.
5. Agent kann bei fehlender Freigabe PR-ready Plan liefern statt Writes auszuführen.
6. Agent kann nach Memory/State fortsetzen, ohne alte Memory als Wahrheit zu behandeln.
7. Jede Phase hat Tests, final_quality_report und Slack/MCP Evidence.
8. Kein falsches Complete: vollständige Migration erst nach M0-M10 Evidence.

