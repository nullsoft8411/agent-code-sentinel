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


MODULE_RULES: list[tuple[str, str, list[str], list[str], str]] = [
    ("application/auth/authorization_service.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/audit_events.py"], ["tests/test_write_guard.py", "tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "RBAC permission checks are adapted to project-scoped MCP tools, explicit per-run approval, state locks and audit evidence"),
    ("application/auth/exceptions.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_write_guard.py", "tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "authorization exceptions are adapted to structured blocked payloads with blocker_code, reason and next_action"),
    ("application/services/task_creation_service.py", "migrated", ["src/code_sentinel_agent/task_creation.py"], ["tests/test_task_creation.py"], "finding-to-task grouping and dedupe are implemented locally"),
    ("application/task_service.py", "adapted", ["src/code_sentinel_agent/task_workflow.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_task_workflow.py", "tests/test_autonomous_cycle.py"], "task service behavior is represented as local workflow helpers and cycle state"),
    ("application/scanner_service.py", "adapted", ["src/code_sentinel_agent/analysis_workflow.py", "src/code_sentinel_agent/scan_jobs.py"], ["tests/test_agent_analysis.py", "tests/test_scan_runtime_helpers.py"], "scanner orchestration becomes Agent-supplied analysis plus deterministic state persistence"),
    ("application/services/scan_execution_service.py", "adapted", ["src/code_sentinel_agent/scan_jobs.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_scan_runtime_helpers.py", "tests/test_autonomous_cycle.py"], "scan execution lifecycle is represented in local scan job and cycle evidence"),
    ("application/services/qg_workflow_orchestrator.py", "adapted", ["src/code_sentinel_agent/qg_workflow.py"], ["tests/test_qg_workflow.py"], "QG orchestration is represented by local validation and task takeover workflow"),
    ("infrastructure/execution/qg_test_runner.py", "adapted", ["src/code_sentinel_agent/validation_runner.py"], ["tests/test_qg_workflow.py"], "validation output is normalized without importing production runner dependencies"),
    ("infrastructure/execution/claude_executor.py", "removed", ["src/code_sentinel_agent/execution_sessions.py"], ["tests/test_docs_contract.py", "tests/test_execution_sessions.py"], "external AI executor is intentionally replaced by Workspace Agent task takeover"),
    ("infrastructure/messaging/stream_service.py", "adapted", ["src/code_sentinel_agent/mcp_state.py", "src/code_sentinel_agent/cycle.py"], ["tests/test_mcp_state.py", "tests/test_autonomous_cycle.py"], "Redis stream delivery is represented by locks, runs, events and next_autonomous_step"),
    ("application/git_service.py", "adapted", ["src/code_sentinel_agent/approvals.py", "src/code_sentinel_agent/task_execution.py"], ["tests/test_write_guard.py", "tests/test_autonomous_cycle.py"], "Git write behavior is mediated by MCP/GitHub tools and approval records"),
]


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
