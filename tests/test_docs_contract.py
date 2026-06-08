import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_code_sentinel_migration_analysis_is_linked_and_grounded() -> None:
    readme = (ROOT / "README.md").read_text()
    analysis = (ROOT / "docs/local-code-sentinel-migration-analysis.md").read_text()

    assert "docs/local-code-sentinel-migration-analysis.md" in readme
    assert "/home/pika/projekte/code-sentinel" in analysis
    assert "/home/pika/projekte/agent-code-sentinel" in analysis
    assert "Agent-native Runtime" in analysis
    assert "MCP Rolle" in analysis
    assert "Phase M0" in analysis
    assert "Definition of Done" in analysis


def test_local_code_sentinel_source_inventory_covers_core_runtime() -> None:
    readme = (ROOT / "README.md").read_text()
    inventory_path = ROOT / "docs/local-code-sentinel-source-inventory.json"
    inventory = json.loads(inventory_path.read_text())

    assert "docs/local-code-sentinel-source-inventory.json" in readme
    assert inventory["source_root"] == "/home/pika/projekte/code-sentinel"
    assert inventory["generated_by"] == "scripts/extract_local_code_sentinel_inventory.py"

    tables = inventory["coverage"]["required_tables_present"]
    for table_name in [
        "projects",
        "health_bot_tasks",
        "health_bot_scan_jobs",
        "health_bot_scan_findings",
        "quality_gate_runs",
        "quality_gate_fix_sessions",
        "quality_gate_states",
        "qg_workflows",
        "health_bot_claude_sessions",
        "tracked_prs",
        "audit_logs",
        "health_bot_state",
    ]:
        assert tables[table_name] is True

    modules = inventory["coverage"]["required_runtime_modules_present"]
    for module_path in [
        "application/task_service.py",
        "application/scanner_service.py",
        "application/git_service.py",
        "application/services/task_creation_service.py",
        "application/services/scan_execution_service.py",
        "application/services/qg_workflow_orchestrator.py",
        "infrastructure/execution/claude_executor.py",
        "infrastructure/execution/qg_test_runner.py",
        "infrastructure/messaging/stream_service.py",
        "workers/task_processing_worker.py",
        "workers/stream_task_worker.py",
        "workers/scan_worker.py",
        "workers/scan_coordinator.py",
        "workers/pr_monitor_worker.py",
    ]:
        assert modules[module_path] is True


def test_full_source_coverage_gate_classifies_every_inventory_entry() -> None:
    readme = (ROOT / "README.md").read_text()
    inventory = json.loads((ROOT / "docs/local-code-sentinel-source-inventory.json").read_text())
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())

    assert "docs/local-code-sentinel-full-source-coverage.json" in readme
    assert coverage["generated_by"] == "scripts/build_full_source_coverage.py"
    assert coverage["inventory_source"] == "docs/local-code-sentinel-source-inventory.json"
    assert coverage["source_root"] == inventory["source_root"]

    expected_entries = len(inventory["models"]["tables"]) + len(inventory["runtime_modules"])
    assert coverage["summary"]["entries_total"] == expected_entries
    assert coverage["summary"]["kind_counts"]["table"] == len(inventory["models"]["tables"])
    assert coverage["summary"]["kind_counts"]["runtime_module"] == len(inventory["runtime_modules"])
    assert len(coverage["entries"]) == expected_entries

    allowed_statuses = {"migrated", "adapted", "blocked", "removed"}
    for entry in coverage["entries"]:
        assert entry["status"] in allowed_statuses
        assert entry["kind"] in {"table", "runtime_module"}
        assert entry["source"]
        assert entry["name"]
        if entry["status"] == "blocked":
            assert entry["blocker_code"]
            assert entry["next_task"]

    assert coverage["summary"]["blocked_total"] == 0
    assert coverage["gate_status"] == "passed"
    assert not [entry for entry in coverage["entries"] if entry["status"] == "blocked"]
    assert "managed Agent testing remain blocked" in coverage["blocking_rule"]


def test_full_source_coverage_has_specific_auth_rbac_mapping() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {entry["source"]: entry for entry in coverage["entries"]}

    assert entries["application/auth/authorization_service.py"]["status"] == "adapted"
    assert "explicit per-run approval" in entries["application/auth/authorization_service.py"]["reason"]
    assert "src/code_sentinel_agent/approvals.py" in entries["application/auth/authorization_service.py"]["target_modules"]

    assert entries["application/auth/exceptions.py"]["status"] == "adapted"
    assert "blocker_code" in entries["application/auth/exceptions.py"]["reason"]

    for source in [
        "application/auth/role_service.py",
        "application/auth/sso_service.py",
        "application/services/oauth_service.py",
        "application/auth/system_user_handlers.py",
        "application/commands/system_user_commands.py",
        "application/services/api_key_service.py",
        "application/services/permission_checker.py",
        "application/services/service_account_service.py",
        "application/tenant/tenant_service.py",
        "infrastructure/persistence/models/user.py",
        "infrastructure/persistence/models/tenant.py",
        "infrastructure/persistence/models/api_key.py",
        "infrastructure/persistence/models/service_account.py",
    ]:
        assert entries[source]["status"] == "removed"
        assert "roles, auth and SSO do not exist in the Agent runtime" in entries[source]["user_acceptance"]
        assert "src/code_sentinel_agent/approvals.py" in entries[source]["target_modules"]

    by_name = {entry["name"]: entry for entry in coverage["entries"]}
    for table_name in ["users", "tenants", "api_keys", "service_accounts", "tenant_invitations", "tenant_usage_records"]:
        assert by_name[table_name]["status"] == "removed"
        assert "not the SaaS human-identity layer" in by_name[table_name]["user_acceptance"]


def test_full_source_coverage_has_specific_budget_billing_mapping() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {entry["source"]: entry for entry in coverage["entries"]}
    by_name = {entry["name"]: entry for entry in coverage["entries"]}

    for source in [
        "application/billing/billing_service.py",
        "infrastructure/persistence/billing_repository.py",
        "application/services/budget_guard_service.py",
        "application/services/budget_service_adapter.py",
        "application/services/budget_factory.py",
        "application/services/cost_estimation_service.py",
        "application/tenant/usage_service.py",
        "infrastructure/persistence/models/budget.py",
        "infrastructure/persistence/models/usage.py",
    ]:
        assert entries[source]["status"] == "removed"
        assert "kein budget oder usage" in entries[source]["user_acceptance"]

    for table_name in ["budget_limits", "usage_records", "health_bot_usage"]:
        assert by_name[table_name]["status"] == "removed"
        assert "kein budget oder usage" in by_name[table_name]["user_acceptance"]


def test_full_source_coverage_maps_command_query_and_orchestration_slice() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {entry["source"]: entry for entry in coverage["entries"]}

    adapted_sources = {
        "application/commands/command_bus.py": "explicit MCP tool contracts",
        "application/commands/task_commands.py": "task command intent",
        "application/queries/interfaces.py": "read-only MCP state tools",
        "application/queries/task_queries.py": "state_tasks_list",
        "application/services/audit_logger.py": "append/list audit events",
        "application/services/audit_service.py": "event append",
        "application/services/code_scanner_orchestrator.py": "Agent-supplied analysis",
        "application/services/quality_gate_lifecycle_service.py": "quality-gate lifecycle",
        "application/services/quality_gate_workflow_service.py": "Agent task takeover",
        "application/services/qg_workflow_lock_service.py": "project-scoped state locks",
        "application/services/queue_state_service.py": "next_autonomous_step",
        "application/ports/idempotency_store.py": "deterministic IDs",
        "application/diagnostics/health_check_service.py": "project preflight",
    }
    for source, reason_fragment in adapted_sources.items():
        assert entries[source]["status"] == "adapted"
        assert reason_fragment in entries[source]["reason"]
        assert entries[source]["target_modules"]
        assert entries[source]["tests"]

    for source in [
        "application/ports/resilience.py",
        "application/services/daemon_client.py",
        "application/services/project_upload_service.py",
        "application/services/config_provisioner_service.py",
    ]:
        assert entries[source]["status"] == "removed"
        assert "without tmux, daemon terminal streaming" in entries[source]["user_acceptance"]


def test_full_source_coverage_maps_services_execution_messaging_slice() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {entry["source"]: entry for entry in coverage["entries"]}
    by_name = {entry["name"]: entry for entry in coverage["entries"]}

    adapted_sources = {
        "application/services/archive_service.py": "archive/restore lifecycle",
        "application/services/dependency_analysis_service.py": "Agent-owned file/dependency findings",
        "application/services/github_app_service.py": "repository/PR lifecycle responsibility",
        "application/services/phase_registry_adapter.py": "scan job progress",
        "application/services/plugin_executor_adapter.py": "deterministic plugin execution records",
        "application/services/recovery_service.py": "resumable Agent cycles",
        "application/services/scan_phase_registry.py": "scan job and plugin execution status",
        "application/services/session_registry.py": "Agent-native execution sessions",
        "application/services/stats_service.py": "report readback counters",
    }
    for source, reason_fragment in adapted_sources.items():
        assert entries[source]["status"] == "adapted"
        assert reason_fragment in entries[source]["reason"]
        assert entries[source]["target_modules"]
        assert entries[source]["tests"]

    for source in [
        "application/services/model_selector.py",
        "application/services/session_attachment_service.py",
        "infrastructure/execution/circuit_breaker.py",
        "infrastructure/execution/factory.py",
        "infrastructure/execution/workspace.py",
        "infrastructure/execution/workspace_manager.py",
    ]:
        assert entries[source]["status"] == "removed"
        assert "without tmux, daemon terminal streaming" in entries[source]["user_acceptance"]

    for source in [
        "application/services/__init__.py",
        "application/services/notification_service.py",
        "application/services/system_admin_service.py",
    ]:
        assert entries[source]["status"] == "removed"
        assert "not the local FastAPI SaaS product shell" in entries[source]["user_acceptance"]

    for table_name in [
        "health_bot_file_checks",
        "health_bot_file_analyses",
        "health_bot_plugin_executions",
        "health_bot_task_history",
        "health_bot_task_logs",
    ]:
        assert by_name[table_name]["status"] == "adapted"
        assert by_name[table_name]["target_modules"]

    for table_name in [
        "contact_messages",
        "sales_leads",
        "github_app_installations",
        "github_app_repositories",
        "logging_configs",
        "webhook_configs",
        "webhook_deliveries",
    ]:
        assert by_name[table_name]["status"] == "removed"
        assert "not the local FastAPI SaaS product shell" in by_name[table_name]["user_acceptance"]


def test_full_source_coverage_maps_messaging_and_persistence_slice() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {(entry["kind"], entry["source"]): entry for entry in coverage["entries"]}

    adapted_sources = {
        "infrastructure/messaging/event_bus.py": "audit event append/list",
        "infrastructure/messaging/event_handlers.py": "deterministic task transitions",
        "infrastructure/messaging/event_router.py": "explicit MCP tool names",
        "infrastructure/messaging/pr_queue.py": "pr_state set/get",
        "infrastructure/messaging/redis_idempotency_store.py": "deterministic IDs",
        "infrastructure/messaging/scan_queue.py": "scan_job lifecycle",
        "infrastructure/messaging/signal_handlers.py": "Agent-owned analysis",
        "infrastructure/messaging/signal_service.py": "controlled MCP state tools",
        "infrastructure/persistence/audit_repository.py": "Agent SQLite state",
        "infrastructure/persistence/base.py": "explicit SQLite migrations",
        "infrastructure/persistence/claude_session_repository.py": "Agent-native execution session",
        "infrastructure/persistence/event_store.py": "direct state mutations",
        "infrastructure/persistence/file_analysis_repository.py": "file inventory checks",
        "infrastructure/persistence/project_repository.py": "state_project_get",
        "infrastructure/persistence/models/task.py": "workflow helpers",
        "infrastructure/persistence/models/scan_job.py": "scan_jobs state",
        "infrastructure/persistence/models/scan_finding.py": "scan_findings state",
        "infrastructure/persistence/models/plugin_execution_model.py": "plugin_executions table",
        "infrastructure/persistence/models/tracked_pr.py": "pr_state set/get",
    }
    for source, reason_fragment in adapted_sources.items():
        entry = entries[("runtime_module", source)]
        assert entry["status"] == "adapted"
        assert reason_fragment in entry["reason"]
        assert entry["target_modules"]
        assert entry["tests"]

    for source in [
        "infrastructure/persistence/contact_repository.py",
        "infrastructure/persistence/github_app_repository.py",
        "infrastructure/persistence/logging_config_repository.py",
        "infrastructure/persistence/models/contact.py",
        "infrastructure/persistence/models/github_app_installation.py",
        "infrastructure/persistence/models/logging_config.py",
        "infrastructure/persistence/models/webhook.py",
    ]:
        entry = entries[("runtime_module", source)]
        assert entry["status"] == "removed"
        assert "not the local FastAPI SaaS product shell" in entry["user_acceptance"]


def test_full_source_coverage_maps_final_persistence_and_worker_slice() -> None:
    coverage = json.loads((ROOT / "docs/local-code-sentinel-full-source-coverage.json").read_text())
    entries = {(entry["kind"], entry["source"]): entry for entry in coverage["entries"]}

    adapted_sources = {
        "infrastructure/persistence/projections/project_stats_projector.py": "report readback counters",
        "infrastructure/persistence/qg_workflow_repository.py": "selected_task_for_agent_takeover",
        "infrastructure/persistence/quality_gate_repository.py": "QA gate normalization",
        "infrastructure/persistence/query_service.py": "read-only MCP state tools",
        "infrastructure/persistence/scan_finding_repository.py": "signature dedupe",
        "infrastructure/persistence/scan_job_repository.py": "scan_jobs lifecycle",
        "infrastructure/persistence/session.py": "MCP backend detection",
        "infrastructure/persistence/state_repository.py": "memories, runs, locks",
        "infrastructure/persistence/task_repository.py": "subtask progress",
        "infrastructure/persistence/unit_of_work.py": "state tool calls",
        "workers/scan_worker.py": "Agent-owned analysis",
        "workers/task_processing_worker.py": "Agent task execution result",
        "workers/stream_task_worker.py": "selected runnable task",
        "workers/pr_monitor_worker.py": "pr_state tools",
        "workers/projection_worker.py": "report generation",
        "workers/worker_registry.py": "report/run state",
    }
    for source, reason_fragment in adapted_sources.items():
        entry = entries[("runtime_module", source)]
        assert entry["status"] == "adapted"
        assert reason_fragment in entry["reason"]
        assert entry["target_modules"]
        assert entry["tests"]

    for source in [
        "infrastructure/persistence/rls/postgres_rls.py",
        "infrastructure/persistence/rls/rls_mixin.py",
        "infrastructure/persistence/rls/session_middleware.py",
        "infrastructure/persistence/system_admin_repository.py",
        "infrastructure/persistence/webhook_repository.py",
        "workers/archive_cleanup_worker.py",
        "workers/cleanup_worker.py",
        "workers/webhook_delivery_worker.py",
    ]:
        entry = entries[("runtime_module", source)]
        assert entry["status"] == "removed"
        assert entry["user_acceptance"]


def test_agent_native_migration_plan_replaces_external_ai_execution() -> None:
    readme = (ROOT / "README.md").read_text()
    plan = (ROOT / "docs/agent-native-code-sentinel-migration-plan.md").read_text()

    assert "docs/agent-native-code-sentinel-migration-plan.md" in readme
    assert "The Agent itself is the executor" in plan
    assert "The migration target is not \"copy ClaudeExecutor\"" in plan
    assert "Findings can create jobs, parent tasks and subtasks" in plan
    assert "Agent-native execution sessions replace old Claude sessions" in plan
    assert "external Claude CLI execution" in plan
    assert "QA gate failure creates findings and tasks" in plan
    assert "selected_task_for_agent_takeover" in plan
    assert "The task is not delegated to Claude, tmux, Claude CLI, or any external AI executor" in plan
    assert "Infrastructure is not ported 1:1, but Code Sentinel's functional workweise is migrated" in plan
    assert "PostgreSQL persistence semantics become SQLite and MCP shared-state schema" in plan
    assert "Claude/tmux execution becomes Workspace Agent task takeover" in plan
    assert "Agent Work Logic State Machine" in plan
    assert "Every autonomous cycle chooses exactly one current_focus" in plan
    assert "The Agent pulls the next task or subtask from shared state" in plan
    assert "Project improvement happens through findings and tasks, never as untracked edits" in plan
    assert "Every cycle exits with persisted state and a concrete next_autonomous_step" in plan
    assert "QA Review Findings Integrated Into This Plan" in plan
    assert "High: QA-gate failure does not create findings/tasks" in plan
    assert "AN-2 schema migration is a hard dependency for AN-4, AN-5, AN-6, AN-7 and" in plan
    assert "No AN phase can be marked complete from docs-only assertions" in plan
    assert "Every AN phase requires a focused unit/integration test and a phase-specific E2E test" in plan
    assert "Phase E2E Gate Matrix" in plan
    assert "Slack/Studio E2E" in plan
    assert "Every AN phase has a passing phase-specific E2E test" in plan


def test_target_runtime_has_no_external_ai_executor_code() -> None:
    forbidden = [
        "ClaudeExecutor",
        "claude_executor",
        "Claude CLI",
        "tmux",
    ]
    target_files = list((ROOT / "src/code_sentinel_agent").rglob("*.py"))

    assert target_files
    for path in target_files:
        content = path.read_text()
        for phrase in forbidden:
            assert phrase not in content, f"{phrase} must not exist in {path.relative_to(ROOT)}"
