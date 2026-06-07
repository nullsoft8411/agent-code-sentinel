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
