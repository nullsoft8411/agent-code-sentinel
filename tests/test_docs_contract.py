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

    assert coverage["gate_status"] == "blocked"
    assert coverage["summary"]["blocked_total"] > 0
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
