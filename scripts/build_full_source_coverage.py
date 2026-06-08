#!/usr/bin/env python3
"""Build the full local Code Sentinel source coverage gate.

The output is intentionally conservative. It classifies every discovered table
and runtime module from the static source inventory. Unknown or not-yet-reviewed
areas are blocked instead of being treated as migrated.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


CORE_MIGRATED_TABLES = {
    "projects": ("migrated", ["src/code_sentinel_agent/db.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"]),
    "health_bot_state": ("migrated", ["src/code_sentinel_agent/memory.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"]),
    "health_bot_tasks": ("migrated", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_task_creation.py", "tests/test_task_workflow.py"]),
    "health_bot_scan_jobs": ("migrated", ["src/code_sentinel_agent/scan_jobs.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"]),
    "health_bot_scan_findings": ("migrated", ["src/code_sentinel_agent/scan_findings.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_mcp_state.py"]),
    "quality_gate_runs": ("migrated", ["src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/qa_gates.py"], ["tests/test_qg_workflow.py"]),
    "quality_gate_fix_sessions": ("adapted", ["src/code_sentinel_agent/execution_sessions.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_execution_sessions.py", "tests/test_qg_workflow.py"]),
    "quality_gate_states": ("adapted", ["src/code_sentinel_agent/qa_gates.py", "src/code_sentinel_agent/reports.py"], ["tests/test_qg_workflow.py"]),
    "qg_workflows": ("adapted", ["src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_qg_workflow.py", "tests/test_autonomous_cycle.py"]),
    "health_bot_claude_sessions": ("adapted", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_execution_sessions.py", "tests/test_docs_contract.py"]),
    "audit_logs": ("adapted", ["src/code_sentinel_agent/audit_events.py"], ["tests/test_audit_events.py", "tests/test_autonomous_cycle.py"]),
    "tracked_prs": ("adapted", ["src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"]),
    "health_bot_file_checks": ("adapted", ["src/code_sentinel_agent/file_inventory.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"]),
    "health_bot_file_analyses": ("adapted", ["src/code_sentinel_agent/file_inventory.py", "src/code_sentinel_agent/analysis_workflow.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"]),
    "health_bot_plugin_executions": ("adapted", ["src/code_sentinel_agent/plugin_executions.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"]),
    "health_bot_task_history": ("adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_audit_events.py", "tests/test_task_workflow.py"]),
    "health_bot_task_logs": ("adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/execution_sessions.py"], ["tests/test_audit_events.py", "tests/test_execution_sessions.py"]),
}

SAAS_IDENTITY_REMOVED_TABLES = {
    "api_keys",
    "service_accounts",
    "tenant_invitations",
    "tenant_usage_records",
    "tenants",
    "users",
}

SAAS_IDENTITY_REMOVED_MODULES = {
    "application/auth/role_service.py",
    "application/auth/sso_service.py",
    "application/auth/system_user_handlers.py",
    "application/commands/system_user_commands.py",
    "application/services/api_key_service.py",
    "application/services/oauth_service.py",
    "application/services/permission_checker.py",
    "application/services/service_account_service.py",
    "application/tenant/commands.py",
    "application/tenant/dtos.py",
    "application/tenant/tenant_service.py",
    "application/tenant/tenant_stats_service.py",
    "application/tenant/tenant_user_service.py",
    "infrastructure/persistence/api_key_repository.py",
    "infrastructure/persistence/invitation_repository.py",
    "infrastructure/persistence/models/api_key.py",
    "infrastructure/persistence/models/invitation.py",
    "infrastructure/persistence/models/service_account.py",
    "infrastructure/persistence/models/tenant.py",
    "infrastructure/persistence/models/user.py",
    "infrastructure/persistence/rls/tenant_filter.py",
    "infrastructure/persistence/service_account_repository.py",
    "infrastructure/persistence/tenant_aware_repository.py",
    "infrastructure/persistence/tenant_repository.py",
    "infrastructure/persistence/user_repository.py",
}

BUDGET_USAGE_ACCEPTANCE = "User explicitly stated: im agent haben wir kein budget oder usage."
SAAS_IDENTITY_ACCEPTANCE = (
    "User explicitly stated roles, auth and SSO do not exist in the Agent runtime; "
    "the Agent must migrate work logic, not the SaaS human-identity layer."
)
NON_AGENT_INFRA_ACCEPTANCE = (
    "User explicitly directed that the Agent works from the runtime repository and MCP state, "
    "without tmux, daemon terminal streaming, external executors or SaaS upload/provisioning flows."
)
SAAS_APP_INFRA_ACCEPTANCE = (
    "User target is an Agent runtime, not the local FastAPI SaaS product shell; "
    "repository cloning, GitHub actions, notifications and operator visibility are handled by MCP/GitHub tools and Agent reports."
)


MODULE_RULES: list[tuple[str, str, list[str], list[str], str]] = [
    ("application/auth/authorization_service.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_write_guard.py", "tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "RBAC permission checks are adapted to project-scoped MCP tools, explicit per-run approval, state locks and audit evidence"),
    ("application/auth/exceptions.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_write_guard.py", "tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "authorization exceptions are adapted to structured blocked payloads with blocker_code, reason and next_action"),
    ("application/services/task_creation_service.py", "migrated", ["src/code_sentinel_agent/task_creation.py"], ["tests/test_task_creation.py"], "finding-to-task grouping and dedupe are implemented locally"),
    ("application/task_service.py", "adapted", ["src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_task_workflow.py", "tests/test_autonomous_cycle.py"], "task service behavior is represented as local workflow helpers and cycle state"),
    ("application/scanner_service.py", "adapted", ["src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/scan_jobs.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "scanner orchestration becomes Agent-supplied analysis plus deterministic state persistence"),
    ("application/services/scan_execution_service.py", "adapted", ["src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan execution lifecycle is represented in local scan job and cycle evidence"),
    ("application/services/qg_workflow_orchestrator.py", "adapted", ["src/code_sentinel_agent/qg_workflow.py"], ["tests/test_qg_workflow.py"], "QG orchestration is represented by local validation and task takeover workflow"),
    ("application/commands/command_bus.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/output_contract.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py", "tests/test_cli_contract.py"], "CQRS command dispatch is adapted to explicit MCP tool contracts, structured payload validation and deterministic cycle state instead of an in-process command bus"),
    ("application/commands/task_commands.py", "adapted", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_task_creation.py", "tests/test_task_workflow.py", "tests/test_mcp_state.py"], "task command intent is represented by finding-to-task creation, task status transitions, task selection and validated task execution result persistence"),
    ("application/queries/interfaces.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "query service interfaces are adapted to read-only MCP state tools and report readback contracts"),
    ("application/queries/task_queries.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_task_workflow.py", "tests/test_cli_contract.py"], "task list/detail/count query DTO behavior is represented by state_tasks_list, next runnable task selection and report counters"),
    ("application/services/audit_logger.py", "adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_audit_events.py", "tests/test_mcp_state.py"], "audit logging is adapted to append/list audit events in shared Agent state"),
    ("application/services/audit_service.py", "adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/reports.py"], ["tests/test_audit_events.py", "tests/test_autonomous_cycle.py"], "audit service workflows are adapted to event append, report readback and cycle evidence without user/tenant audit APIs"),
    ("application/services/code_scanner_orchestrator.py", "adapted", ["src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/plugin_executions.py", "src/code_sentinel_agent/file_inventory.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scanner orchestration is adapted to Agent-supplied analysis, scan job progress, plugin execution evidence and file inventory state"),
    ("application/services/archive_service.py", "adapted", ["src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/reports.py"], ["tests/test_task_workflow.py", "tests/test_audit_events.py", "tests/test_cli_contract.py"], "archive/restore lifecycle is adapted to task status, audit evidence and report visibility; tenant cascade and email notification behavior are removed with the SaaS shell"),
    ("application/services/dependency_analysis_service.py", "adapted", ["src/code_sentinel_agent/agent_analysis.py", "src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/file_inventory.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "dependency analysis is adapted from Claude CLI analysis to Agent-owned file/dependency findings persisted through analyze-to-state"),
    ("application/services/github_app_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_mcp_state.py", "tests/test_write_guard.py", "tests/test_autonomous_cycle.py"], "GitHub App installation/token workflows are not ported, but repository/PR lifecycle responsibility is adapted to MCP/GitHub tools, approval records and pr_state readback"),
    ("application/services/quality_gate_lifecycle_service.py", "adapted", ["src/code_sentinel_agent/qa_gates.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_qg_workflow.py"], "quality-gate lifecycle state is adapted to local QA gate normalization and workflow persistence"),
    ("application/services/quality_gate_workflow_service.py", "adapted", ["src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/validation_runner.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_qg_workflow.py", "tests/test_mcp_state.py"], "multi-stage QG workflow is adapted to validation payload processing, finding/task creation and Agent task takeover without model selection or external execution"),
    ("application/services/qg_workflow_lock_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "workflow locking is adapted to project-scoped state locks with explicit owner, expiry and release evidence"),
    ("application/services/queue_state_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py", "tests/test_cli_contract.py"], "queue state is adapted to persisted runs, next_autonomous_step, selected tasks and report counters instead of a separate queue projection service"),
    ("application/services/phase_registry_adapter.py", "adapted", ["src/code_sentinel_agent/plugin_executions.py", "src/code_sentinel_agent/scan_jobs.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan phase registry adapter is adapted to scan job progress and plugin execution evidence instead of daemon signal handler injection"),
    ("application/services/plugin_executor_adapter.py", "adapted", ["src/code_sentinel_agent/plugin_executions.py", "src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/validation_runner.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_agent_analysis.py", "tests/test_qg_workflow.py"], "plugin executor adapter is adapted to deterministic plugin execution records, Agent-owned findings and validation normalization without direct daemon plugin execution"),
    ("application/services/recovery_service.py", "adapted", ["src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_autonomous_cycle.py", "tests/test_mcp_state.py", "tests/test_cli_contract.py"], "startup recovery is adapted to resumable Agent cycles, state locks, selected tasks and report next_autonomous_step instead of Redis stream replay"),
    ("application/services/scan_phase_registry.py", "adapted", ["src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/plugin_executions.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan phase registry is adapted to scan job and plugin execution status records"),
    ("application/services/session_registry.py", "adapted", ["src/code_sentinel_agent/execution_sessions.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_execution_sessions.py", "tests/test_mcp_state.py"], "terminal session registry is adapted to Agent-native execution sessions and task execution result records"),
    ("application/services/stats_service.py", "adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_cli_contract.py", "tests/test_mcp_state.py"], "dashboard statistics are adapted to report readback counters and read-only state tools"),
    ("application/ports/idempotency_store.py", "adapted", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/scan_findings.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_task_creation.py", "tests/test_scan_runtime_helpers.py", "tests/test_qg_workflow.py"], "idempotency is adapted to deterministic IDs, finding signatures and task/finding dedupe rather than a generic command idempotency port"),
    ("application/diagnostics/health_check_service.py", "adapted", ["src/code_sentinel_agent/project_context.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_project_context.py", "tests/test_mcp_state.py", "tests/test_cli_contract.py"], "health checks are adapted to project preflight, DB/backend detection and report readback instead of SaaS API health endpoints"),
    ("infrastructure/messaging/__init__.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "messaging package exports are adapted to explicit MCP state tool entrypoints and autonomous cycle helpers"),
    ("infrastructure/messaging/event_bus.py", "adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_audit_events.py", "tests/test_mcp_state.py"], "domain event publish/subscribe is adapted to explicit state mutations plus audit event append/list evidence"),
    ("infrastructure/messaging/event_handlers.py", "adapted", ["src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_autonomous_cycle.py", "tests/test_task_workflow.py"], "event handlers for task, PR and scan changes are adapted to deterministic task transitions, pr_state and cycle next steps"),
    ("infrastructure/messaging/event_router.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/output_contract.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "event routing and delivery guarantees are adapted to explicit MCP tool names, blocker payloads and persisted state instead of channel routing"),
    ("infrastructure/messaging/pr_queue.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "PR monitor queue is adapted to pr_state set/get and report readback instead of Redis PR queue workers"),
    ("infrastructure/messaging/redis_idempotency_store.py", "adapted", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/scan_findings.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_task_creation.py", "tests/test_scan_runtime_helpers.py", "tests/test_qg_workflow.py"], "Redis idempotency store is adapted to deterministic IDs, finding signatures and dedupe constraints in Agent state"),
    ("infrastructure/messaging/scan_queue.py", "adapted", ["src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan queue behavior is adapted to scan_job lifecycle and autonomous cycle selection instead of Redis queues"),
    ("infrastructure/messaging/signal_handlers.py", "adapted", ["src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_agent_analysis.py", "tests/test_qg_workflow.py", "tests/test_autonomous_cycle.py"], "Redis signal handlers are adapted to Agent-owned analysis, QG workflow processing and cycle state transitions"),
    ("infrastructure/messaging/signal_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "Redis signal publishing is adapted to controlled MCP state tools and next_autonomous_step persistence"),
    ("infrastructure/persistence/archive_repository.py", "adapted", ["src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_task_workflow.py", "tests/test_audit_events.py"], "archive repository persistence is adapted to task status and audit events rather than tenant/project soft-delete tables"),
    ("infrastructure/persistence/audit_repository.py", "adapted", ["src/code_sentinel_agent/audit_events.py"], ["tests/test_audit_events.py"], "audit repository is adapted to append/list audit_events in the Agent SQLite state"),
    ("infrastructure/persistence/base.py", "adapted", ["src/code_sentinel_agent/db.py", "migrations/001_init.sql"], ["tests/test_db_schema.py"], "SQLAlchemy declarative base is adapted to explicit SQLite migrations and schema bootstrap"),
    ("infrastructure/persistence/claude_session_repository.py", "adapted", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_execution_sessions.py"], "Claude session repository is adapted to Agent-native execution session persistence"),
    ("infrastructure/persistence/event_replay.py", "adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_cli_contract.py", "tests/test_audit_events.py"], "event replay and projection rebuilds are adapted to report readback and audit event evidence instead of event-sourced replay"),
    ("infrastructure/persistence/event_store.py", "adapted", ["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_audit_events.py", "tests/test_mcp_state.py"], "event store is adapted to direct state mutations and audit_events rather than generic event sourcing tables"),
    ("infrastructure/persistence/event_upcaster.py", "adapted", ["src/code_sentinel_agent/output_contract.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_cli_contract.py", "tests/test_mcp_state.py"], "event upcasting is adapted to stable JSON output contracts and explicit state-tool payload versions"),
    ("infrastructure/persistence/file_analysis_repository.py", "adapted", ["src/code_sentinel_agent/file_inventory.py", "src/code_sentinel_agent/analysis_workflow.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "file analysis repository is adapted to file inventory checks and Agent-supplied analysis persistence"),
    ("infrastructure/persistence/git_repository.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/approvals.py"], ["tests/test_mcp_state.py", "tests/test_write_guard.py"], "tracked PR repository behavior is adapted to pr_state tools and approval evidence"),
    ("infrastructure/persistence/health_repository.py", "adapted", ["src/code_sentinel_agent/project_context.py", "src/code_sentinel_agent/reports.py"], ["tests/test_project_context.py", "tests/test_cli_contract.py"], "health repository readback is adapted to project context and report evidence"),
    ("infrastructure/persistence/plugin_execution_repository.py", "adapted", ["src/code_sentinel_agent/plugin_executions.py"], ["tests/test_scan_runtime_helpers.py"], "plugin execution repository is adapted to Agent plugin_executions persistence"),
    ("infrastructure/persistence/project_repository.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/db.py"], ["tests/test_mcp_state.py", "tests/test_db_schema.py"], "project repository is adapted to projects table bootstrap and state_project_get contract"),
    ("infrastructure/persistence/projections/project_stats_projector.py", "adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_cli_contract.py", "tests/test_mcp_state.py"], "project stats projection is adapted to report readback counters without token cost or budget fields"),
    ("infrastructure/persistence/projections/task_detail_projector.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_mcp_state.py", "tests/test_task_workflow.py"], "task detail projection is adapted to state_tasks_list and workflow task payloads"),
    ("infrastructure/persistence/projections/task_list_projector.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_mcp_state.py", "tests/test_task_workflow.py"], "task list projection is adapted to read-only MCP task listing and runnable task selection"),
    ("infrastructure/persistence/qg_workflow_repository.py", "adapted", ["src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_qg_workflow.py", "tests/test_autonomous_cycle.py"], "QG workflow repository is adapted to qg_workflow persistence and cycle selected_task_for_agent_takeover evidence"),
    ("infrastructure/persistence/quality_gate_repository.py", "adapted", ["src/code_sentinel_agent/qa_gates.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_qg_workflow.py"], "quality gate repositories are adapted to local QA gate normalization and validation workflow state"),
    ("infrastructure/persistence/query_optimizer.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "query optimization/pagination is adapted to small bounded state readbacks and report counters"),
    ("infrastructure/persistence/query_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "query service is adapted to read-only MCP state tools and report readback"),
    ("infrastructure/persistence/read_models.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "SQL read models are adapted to JSON state payloads and report counters"),
    ("infrastructure/persistence/realtime_repository.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_mcp_state.py", "tests/test_audit_events.py"], "realtime repository is adapted to append-only events and explicit MCP state readback"),
    ("infrastructure/persistence/scan_finding_repository.py", "adapted", ["src/code_sentinel_agent/scan_findings.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_mcp_state.py"], "scan finding repository is adapted to scan_findings state and signature dedupe"),
    ("infrastructure/persistence/scan_job_repository.py", "adapted", ["src/code_sentinel_agent/scan_jobs.py"], ["tests/test_scan_runtime_helpers.py"], "scan job repository is adapted to scan_jobs lifecycle helpers"),
    ("infrastructure/persistence/scanner_repository.py", "adapted", ["src/code_sentinel_agent/file_inventory.py", "src/code_sentinel_agent/scan_jobs.py"], ["tests/test_scan_runtime_helpers.py"], "scanner repository is adapted to file checks and scan job state"),
    ("infrastructure/persistence/session.py", "adapted", ["src/code_sentinel_agent/db.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_db_schema.py", "tests/test_mcp_state.py"], "SQLAlchemy session factory is adapted to explicit SQLite bootstrap and MCP backend detection"),
    ("infrastructure/persistence/snapshot_store.py", "adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_cli_contract.py", "tests/test_audit_events.py"], "snapshot store is adapted to report snapshots and audit evidence instead of generic event-sourcing snapshots"),
    ("infrastructure/persistence/state_repository.py", "adapted", ["src/code_sentinel_agent/memory.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"], "state repository is adapted to memories, runs, locks and state tools"),
    ("infrastructure/persistence/stats_repository.py", "adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_cli_contract.py", "tests/test_mcp_state.py"], "stats repositories are adapted to report counters and read-only state tools without usage accounting"),
    ("infrastructure/persistence/task_repository.py", "adapted", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_task_creation.py", "tests/test_task_workflow.py"], "task repository is adapted to task creation, subtask progress and workflow state helpers"),
    ("infrastructure/persistence/types.py", "adapted", ["src/code_sentinel_agent/db.py", "src/code_sentinel_agent/output_contract.py"], ["tests/test_db_schema.py", "tests/test_cli_contract.py"], "custom SQLAlchemy JSON types are adapted to explicit JSON serialization in SQLite payload contracts"),
    ("infrastructure/persistence/unit_of_work.py", "adapted", ["src/code_sentinel_agent/db.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_db_schema.py", "tests/test_mcp_state.py"], "unit-of-work transaction boundary is adapted to small explicit SQLite transactions around state tool calls"),
    ("infrastructure/execution/qg_test_runner.py", "adapted", ["src/code_sentinel_agent/validation_runner.py"], ["tests/test_qg_workflow.py"], "validation output is normalized without importing production runner dependencies"),
    ("infrastructure/execution/claude_executor.py", "removed", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_docs_contract.py", "tests/test_execution_sessions.py"], "external AI executor is intentionally replaced by Workspace Agent task takeover"),
    ("infrastructure/messaging/stream_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "Redis stream delivery is represented by locks, runs, events and next_autonomous_step"),
    ("application/git_service.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_write_guard.py", "tests/test_autonomous_cycle.py"], "Git write behavior is mediated by MCP/GitHub tools and approval records"),
]

NON_AGENT_INFRA_REMOVED_MODULES = {
    "application/ports/resilience.py": "Circuit-breaker/WebSocket resilience was only needed for the removed daemon terminal path; Agent runtime returns structured blockers and state evidence instead.",
    "application/services/daemon_client.py": "Daemon WebSocket terminal streaming is removed with tmux/external executor flow; the Agent executes and reports task results natively.",
    "application/services/project_upload_service.py": "SaaS project upload/extract workflow is removed because the Agent runtime works from cloned repositories through MCP/Git tools.",
    "application/services/config_provisioner_service.py": "Config provisioning writes are removed; the Agent analyzes existing repo configuration and requests explicit approved edits when needed.",
    "application/services/model_selector.py": "Claude model selection and budget-aware model choice are removed because the Agent runtime has no external AI executor, budget or usage accounting.",
    "application/services/session_attachment_service.py": "Human attachment to Claude/tmux admin sessions is removed; Agent-native execution sessions store evidence without terminal attachment.",
    "infrastructure/execution/circuit_breaker.py": "Executor circuit breaker is removed with the external Claude/tmux execution subsystem; Agent runtime uses controlled blockers and validation records.",
    "infrastructure/execution/factory.py": "ClaudeExecutor factory with budget guard is removed because external AI executor and budget usage are not in the Agent runtime.",
    "infrastructure/execution/workspace.py": "Parallel git-worktree execution workspace model is removed; the managed Agent works from cloned repo state through MCP/Git tools with explicit approvals.",
    "infrastructure/execution/workspace_manager.py": "WorkspaceManager for multi-worker git worktrees is removed; task ownership is serialized by Agent state locks and approval-scoped edits.",
}

SAAS_APP_INFRA_REMOVED_MODULES = {
    "application/services/__init__.py": "Application service export/lazy-loader has no runtime responsibility in the Agent package.",
    "application/services/notification_service.py": "Redis-backed per-user in-app notifications are removed; operator visibility is provided by Agent response, reports and audit events.",
    "application/services/system_admin_service.py": "System-admin tenant/API-key SaaS workflows are removed with the SaaS identity layer.",
    "infrastructure/persistence/contact_repository.py": "Contact/sales persistence is removed with the SaaS product shell.",
    "infrastructure/persistence/github_app_repository.py": "GitHub App installation persistence is removed; repo/PR operations use MCP/GitHub tools plus pr_state evidence.",
    "infrastructure/persistence/logging_config_repository.py": "SaaS logging configuration persistence is removed; Agent runtime uses audit/report evidence.",
    "infrastructure/persistence/models/contact.py": "Contact and sales lead models are removed with the SaaS product shell.",
    "infrastructure/persistence/models/github_app_installation.py": "GitHub App installation models are removed; repository and PR state are represented through MCP/GitHub tools and pr_state.",
    "infrastructure/persistence/models/logging_config.py": "Logging config model is removed from the Agent runtime.",
    "infrastructure/persistence/models/webhook.py": "Webhook configuration and delivery models are removed from the Agent runtime.",
    "infrastructure/persistence/rls/postgres_rls.py": "Tenant RLS setup is removed with the SaaS tenant/RBAC layer.",
    "infrastructure/persistence/rls/rls_mixin.py": "Tenant RLS mixin is removed with the SaaS tenant/RBAC layer.",
    "infrastructure/persistence/rls/session_middleware.py": "Tenant-scoped DB session middleware is removed with the SaaS tenant/RBAC layer.",
    "infrastructure/persistence/system_admin_repository.py": "System-admin repository is removed with the SaaS identity/admin layer.",
    "infrastructure/persistence/webhook_repository.py": "Webhook repository is removed with the SaaS webhook delivery subsystem.",
}

REMOVED_APP_TABLES = {
    "contact_messages": "Marketing/contact intake is outside the Agent runtime.",
    "sales_leads": "Sales lead capture is outside the Agent runtime.",
    "github_app_installations": "GitHub App installation persistence is removed; repository and PR operations use MCP/GitHub tools plus pr_state evidence.",
    "github_app_repositories": "GitHub App repository persistence is removed; repository scope is managed by MCP allowlists and GitHub tooling.",
    "logging_configs": "SaaS logging configuration table is removed; Agent runtime stores audit/report evidence instead.",
    "webhook_configs": "Outbound webhook configuration is removed from the Agent runtime.",
    "webhook_deliveries": "Webhook delivery queue is removed from the Agent runtime.",
}

WORKER_RULES: dict[str, tuple[str, list[str], list[str], str]] = {
    "workers/__init__.py": ("adapted", ["src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_autonomous_cycle.py", "tests/test_mcp_state.py"], "worker package entrypoints are adapted to Agent cycle and MCP state tools"),
    "workers/__main__.py": ("adapted", ["src/code_sentinel_agent/cli.py"], ["tests/test_cli_contract.py"], "worker CLI launcher is adapted to repository scripts and Agent runtime CLI commands"),
    "workers/cli/commands.py": ("adapted", ["src/code_sentinel_agent/cli.py"], ["tests/test_cli_contract.py"], "worker CLI commands are adapted to Agent runtime CLI commands and reports"),
    "workers/cli/utils.py": ("adapted", ["src/code_sentinel_agent/cli.py", "src/code_sentinel_agent/db.py"], ["tests/test_cli_contract.py", "tests/test_db_schema.py"], "worker CLI utilities are adapted to local CLI formatting and SQLite bootstrap"),
    "workers/connectivity_health_worker.py": ("adapted", ["src/code_sentinel_agent/project_context.py", "src/code_sentinel_agent/reports.py"], ["tests/test_project_context.py", "tests/test_cli_contract.py"], "connectivity health worker is adapted to project preflight and report evidence"),
    "workers/pr_monitor_worker.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/reports.py"], ["tests/test_mcp_state.py", "tests/test_cli_contract.py"], "PR monitor worker is adapted to pr_state tools and report readback"),
    "workers/pr_poller.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"], "PR polling is adapted to explicit MCP/GitHub tool use and persisted pr_state"),
    "workers/pr_tracker_worker.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"], "PR tracker worker is adapted to pr_state set/get contracts"),
    "workers/preconditions.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/project_context.py"], ["tests/test_mcp_state.py", "tests/test_project_context.py"], "worker preconditions are adapted to project context and MCP blocker payloads"),
    "workers/projection_worker.py": ("adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_cli_contract.py", "tests/test_audit_events.py"], "projection worker is adapted to report generation and audit event readback"),
    "workers/scan_coordinator.py": ("adapted", ["src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan coordinator is adapted to scan job lifecycle and autonomous cycle selection"),
    "workers/scan_worker.py": ("adapted", ["src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/plugin_executions.py", "src/code_sentinel_agent/file_inventory.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "scan worker is adapted to Agent-owned analysis, plugin execution evidence and file checks"),
    "workers/session_cleanup_worker.py": ("adapted", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_execution_sessions.py"], "session cleanup is adapted to Agent-native execution session evidence and no external terminal attachment"),
    "workers/signal_subscription_worker.py": ("adapted", ["src/code_sentinel_agent/cycle.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_autonomous_cycle.py", "tests/test_mcp_state.py"], "signal subscription worker is adapted to state_run_cycle and MCP state transitions"),
    "workers/stream_task_worker.py": ("adapted", ["src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_task_workflow.py", "tests/test_autonomous_cycle.py"], "stream task worker is adapted to selected runnable task and Agent cycle task takeover"),
    "workers/task_processing_worker.py": ("adapted", ["src/code_sentinel_agent/task_execution.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "task processing worker is adapted to Agent task execution result, approvals and cycle next step"),
    "workers/worker_registry.py": ("adapted", ["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_cli_contract.py", "tests/test_mcp_state.py"], "worker registry is adapted to report/run state instead of a Redis worker registry"),
}

REMOVED_WORKER_MODULES = {
    "workers/archive_cleanup_worker.py": "Archive cleanup worker is removed with SaaS tenant/project archive deletion.",
    "workers/cleanup_worker.py": "Generic cleanup worker for old daemon/workspace resources is removed; Agent state cleanup is explicit and state-scoped.",
    "workers/webhook_delivery_worker.py": "Webhook delivery worker is removed with the SaaS webhook subsystem.",
}

PERSISTENCE_MODEL_RULES: dict[str, tuple[str, list[str], list[str], str]] = {
    "infrastructure/persistence/models/audit_log.py": ("adapted", ["src/code_sentinel_agent/audit_events.py"], ["tests/test_audit_events.py"], "audit log model is adapted to the Agent audit_events table"),
    "infrastructure/persistence/models/claude_session_model.py": ("adapted", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_execution_sessions.py"], "Claude session model is adapted to Agent-native execution sessions"),
    "infrastructure/persistence/models/file_analysis_model.py": ("adapted", ["src/code_sentinel_agent/file_inventory.py", "src/code_sentinel_agent/analysis_workflow.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "file analysis model is adapted to file inventory and Agent analysis state"),
    "infrastructure/persistence/models/file_check.py": ("adapted", ["src/code_sentinel_agent/file_inventory.py"], ["tests/test_scan_runtime_helpers.py"], "file check model is adapted to Agent file_checks state"),
    "infrastructure/persistence/models/helpers.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_creation.py"], ["tests/test_mcp_state.py", "tests/test_task_creation.py"], "CUID/helper generation is adapted to deterministic stable IDs and digests"),
    "infrastructure/persistence/models/plugin_execution_model.py": ("adapted", ["src/code_sentinel_agent/plugin_executions.py"], ["tests/test_scan_runtime_helpers.py"], "plugin execution model is adapted to plugin_executions table"),
    "infrastructure/persistence/models/project.py": ("adapted", ["src/code_sentinel_agent/db.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_db_schema.py", "tests/test_mcp_state.py"], "project model is adapted to projects state table and state_project_get"),
    "infrastructure/persistence/models/qg_workflow.py": ("adapted", ["src/code_sentinel_agent/qg_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_qg_workflow.py", "tests/test_autonomous_cycle.py"], "QG workflow model is adapted to qg_workflows and cycle state"),
    "infrastructure/persistence/models/quality_gate.py": ("adapted", ["src/code_sentinel_agent/qa_gates.py", "src/code_sentinel_agent/qg_workflow.py"], ["tests/test_qg_workflow.py"], "quality gate models are adapted to QA gate normalization and workflow persistence"),
    "infrastructure/persistence/models/scan_finding.py": ("adapted", ["src/code_sentinel_agent/scan_findings.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_mcp_state.py"], "scan finding model is adapted to scan_findings state"),
    "infrastructure/persistence/models/scan_job.py": ("adapted", ["src/code_sentinel_agent/scan_jobs.py"], ["tests/test_scan_runtime_helpers.py"], "scan job model is adapted to scan_jobs state"),
    "infrastructure/persistence/models/state.py": ("adapted", ["src/code_sentinel_agent/memory.py", "src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"], "state model is adapted to memories, runs, locks and MCP state tools"),
    "infrastructure/persistence/models/task.py": ("adapted", ["src/code_sentinel_agent/task_creation.py", "src/code_sentinel_agent/task_workflow.py"], ["tests/test_task_creation.py", "tests/test_task_workflow.py"], "task model is adapted to tasks, subtasks, progress and workflow helpers"),
    "infrastructure/persistence/models/tracked_pr.py": ("adapted", ["src/code_sentinel_agent/mcp_state.py"], ["tests/test_mcp_state.py"], "tracked PR model is adapted to pr_state set/get tools"),
}


def classify_table(table: dict[str, Any]) -> dict[str, Any]:
    table_name = table["table_name"]
    if table_name in CORE_MIGRATED_TABLES:
        status, target_modules, tests = CORE_MIGRATED_TABLES[table_name]
        return {
            "kind": "table",
            "source": table["file"],
            "name": table_name,
            "class_name": table["class_name"],
            "status": status,
            "target_modules": target_modules,
            "tests": tests,
            "reason": "covered by the current agent runtime state model",
        }
    if table_name in {"budget_limits", "usage_records", "health_bot_usage"}:
        return removed_entry(
            kind="table",
            source=table["file"],
            name=table_name,
            reason="Budget and usage accounting do not exist in the Agent target runtime by explicit user decision.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
            extra={"class_name": table["class_name"]},
            user_acceptance=BUDGET_USAGE_ACCEPTANCE,
        )
    if table_name in SAAS_IDENTITY_REMOVED_TABLES:
        return removed_entry(
            kind="table",
            source=table["file"],
            name=table_name,
            reason=(
                "Human tenant/user/API-key/service-account identity tables are removed because the "
                "Workspace Agent does not host SaaS login, role CRUD, SSO/OAuth, invitation or tenant-admin workflows. "
                "Project isolation, approvals, lock ownership and audit actors remain in the Agent state model."
            ),
            target_modules=["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/audit_events.py"],
            tests=["tests/test_docs_contract.py", "tests/test_mcp_state.py", "tests/test_write_guard.py"],
            extra={"class_name": table["class_name"]},
            user_acceptance=SAAS_IDENTITY_ACCEPTANCE,
        )
    if table_name in REMOVED_APP_TABLES:
        return removed_entry(
            kind="table",
            source=table["file"],
            name=table_name,
            reason=REMOVED_APP_TABLES[table_name],
            target_modules=["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/reports.py"],
            tests=["tests/test_docs_contract.py", "tests/test_audit_events.py", "tests/test_cli_contract.py"],
            extra={"class_name": table["class_name"]},
            user_acceptance=SAAS_APP_INFRA_ACCEPTANCE,
        )
    return blocked_entry(
        kind="table",
        source=table["file"],
        name=table_name,
        reason="table is present in local Code Sentinel but has no reviewed agent-runtime mapping yet",
        extra={"class_name": table["class_name"]},
    )


def classify_module(module: dict[str, Any]) -> dict[str, Any]:
    file_name = module["file"]
    for pattern, status, target_modules, tests, reason in MODULE_RULES:
        if file_name == pattern:
            return {
                "kind": "runtime_module",
                "source": file_name,
                "name": module["module"],
                "status": status,
                "target_modules": target_modules,
                "tests": tests,
                "reason": reason,
            }

    if file_name.startswith("domain/scanning/plugins/builtin/"):
        return blocked_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="scanner plugin category requires source-backed mapping to Agent analysis, deterministic parser, or explicit removal",
            extra={"blocker_code": "SCANNER_PLUGIN_MAPPING_REQUIRED"},
        )
    if file_name in {"application/billing/billing_service.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Stripe/customer/subscription billing workflows do not exist in the Agent target runtime by explicit user decision.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
        )
    if file_name in {"infrastructure/persistence/billing_repository.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Billing repository persistence is removed because billing does not exist in the Agent target runtime.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
        )
    if file_name in {"application/services/budget_guard_service.py", "application/services/budget_service_adapter.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Budget guard and usage recording are removed because the Agent target runtime has no budget or usage subsystem.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
        )
    if file_name in {"application/services/budget_factory.py", "application/services/cost_estimation_service.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Claude pricing/model cost estimation is removed because the Agent target runtime has no budget, usage or external AI cost accounting.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
        )
    if file_name in {"application/tenant/usage_service.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Tenant usage accounting is removed because the Agent target runtime has no usage subsystem.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
            user_acceptance=BUDGET_USAGE_ACCEPTANCE,
        )
    if file_name in {"infrastructure/persistence/models/budget.py", "infrastructure/persistence/models/usage.py"}:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="Budget and usage models are removed because the Agent target runtime has no budget or usage subsystem.",
            target_modules=[],
            tests=["tests/test_docs_contract.py"],
            user_acceptance=BUDGET_USAGE_ACCEPTANCE,
        )
    if file_name in SAAS_IDENTITY_REMOVED_MODULES:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason=(
                "SaaS identity, tenant administration, role CRUD, SSO/OAuth, API-key and service-account workflows "
                "are intentionally not ported. The Agent target relies on Workspace/MCP connector identity outside "
                "this runtime and keeps only project_id, approval, lock owner and audit actor evidence."
            ),
            target_modules=["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/audit_events.py"],
            tests=["tests/test_docs_contract.py", "tests/test_mcp_state.py", "tests/test_write_guard.py"],
            user_acceptance=SAAS_IDENTITY_ACCEPTANCE,
        )
    if file_name in NON_AGENT_INFRA_REMOVED_MODULES:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason=NON_AGENT_INFRA_REMOVED_MODULES[file_name],
            target_modules=["src/code_sentinel_agent/project_context.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_execution.py"],
            tests=["tests/test_project_context.py", "tests/test_mcp_state.py", "tests/test_docs_contract.py"],
            user_acceptance=NON_AGENT_INFRA_ACCEPTANCE,
        )
    if file_name in SAAS_APP_INFRA_REMOVED_MODULES:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason=SAAS_APP_INFRA_REMOVED_MODULES[file_name],
            target_modules=["src/code_sentinel_agent/audit_events.py", "src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"],
            tests=["tests/test_docs_contract.py", "tests/test_audit_events.py", "tests/test_cli_contract.py"],
            user_acceptance=SAAS_APP_INFRA_ACCEPTANCE,
        )
    if file_name in WORKER_RULES:
        status, target_modules, tests, reason = WORKER_RULES[file_name]
        return {
            "kind": "runtime_module",
            "source": file_name,
            "name": module["module"],
            "status": status,
            "target_modules": target_modules,
            "tests": tests,
            "reason": reason,
        }
    if file_name in REMOVED_WORKER_MODULES:
        return removed_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason=REMOVED_WORKER_MODULES[file_name],
            target_modules=["src/code_sentinel_agent/reports.py", "src/code_sentinel_agent/mcp_state.py"],
            tests=["tests/test_docs_contract.py", "tests/test_cli_contract.py", "tests/test_mcp_state.py"],
            user_acceptance=SAAS_APP_INFRA_ACCEPTANCE,
        )
    if file_name in PERSISTENCE_MODEL_RULES:
        status, target_modules, tests, reason = PERSISTENCE_MODEL_RULES[file_name]
        return {
            "kind": "runtime_module",
            "source": file_name,
            "name": module["module"],
            "status": status,
            "target_modules": target_modules,
            "tests": tests,
            "reason": reason,
        }
    if file_name.startswith("application/auth/") or "auth" in file_name:
        return blocked_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="auth/RBAC behavior must be mapped to Agent project isolation, approval and audit rules before AN-9",
            extra={"blocker_code": "AUTH_RBAC_MAPPING_REQUIRED"},
        )
    if file_name.startswith("workers/"):
        return blocked_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="worker lifecycle must be mapped to MCP state, schedule, cleanup, retry, projection or explicit removal",
            extra={"blocker_code": "WORKER_MAPPING_REQUIRED"},
        )
    if "repository" in file_name:
        return blocked_entry(
            kind="runtime_module",
            source=file_name,
            name=module["module"],
            reason="repository contract must be mapped to SQLite/Postgres adapter helpers or explicit removal",
            extra={"blocker_code": "REPOSITORY_MAPPING_REQUIRED"},
        )

    return blocked_entry(
        kind="runtime_module",
        source=file_name,
        name=module["module"],
        reason="runtime module has not yet received source-backed migration classification",
    )


def blocked_entry(*, kind: str, source: str, name: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "source": source,
        "name": name,
        "status": "blocked",
        "blocker_code": "FULL_SOURCE_COVERAGE_REQUIRED",
        "reason": reason,
        "next_task": "review source responsibility and classify as migrated, adapted, blocked with owner, or removed with user acceptance",
        "target_modules": [],
        "tests": [],
    }
    if extra:
        payload.update(extra)
    return payload


def removed_entry(
    *,
    kind: str,
    source: str,
    name: str,
    reason: str,
    target_modules: list[str],
    tests: list[str],
    extra: dict[str, Any] | None = None,
    user_acceptance: str = BUDGET_USAGE_ACCEPTANCE,
) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "source": source,
        "name": name,
        "status": "removed",
        "reason": reason,
        "target_modules": target_modules,
        "tests": tests,
        "user_acceptance": user_acceptance,
    }
    if extra:
        payload.update(extra)
    return payload


def build_coverage(inventory: dict[str, Any]) -> dict[str, Any]:
    entries = [classify_table(table) for table in inventory["models"]["tables"]]
    entries.extend(classify_module(module) for module in inventory["runtime_modules"])
    status_counts = Counter(entry["status"] for entry in entries)
    kind_counts = Counter(entry["kind"] for entry in entries)
    blocked_entries = [entry for entry in entries if entry["status"] == "blocked"]
    return {
        "source_root": inventory["source_root"],
        "inventory_source": "docs/local-code-sentinel-source-inventory.json",
        "generated_by": "scripts/build_full_source_coverage.py",
        "gate_status": "blocked" if blocked_entries else "passed",
        "summary": {
            "entries_total": len(entries),
            "blocked_total": len(blocked_entries),
            "status_counts": dict(sorted(status_counts.items())),
            "kind_counts": dict(sorted(kind_counts.items())),
        },
        "blocking_rule": "AN-9 and managed Agent testing remain blocked while blocked_total > 0.",
        "entries": sorted(entries, key=lambda item: (item["kind"], item["source"], item["name"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="docs/local-code-sentinel-source-inventory.json")
    parser.add_argument("--output", default="docs/local-code-sentinel-full-source-coverage.json")
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    output_path = Path(args.output)
    coverage = build_coverage(json.loads(inventory_path.read_text(encoding="utf-8")))
    output_path.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": coverage["gate_status"], **coverage["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
