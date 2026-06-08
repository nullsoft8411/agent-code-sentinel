#!/usr/bin/env python3
"""Run an agent-native Code Sentinel cycle smoke from a cloned checkout.

This script is intentionally self-contained for Workspace Agent execution:
clone the repository, run this file with Python, and inspect the JSON stdout.
It does not call external AI executors and does not require secrets.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = RUNTIME_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_sentinel_agent.analysis_contract import AnalysisContractInput, build_analysis_contract
from code_sentinel_agent.analysis_workflow import analyze_to_state
from code_sentinel_agent.cycle import run_autonomous_cycle
from code_sentinel_agent.db import initialize_database
from code_sentinel_agent.project_context import project_context
from code_sentinel_agent.reports import report


PROJECT_ID = "proj-agent-runtime-smoke"
RUN_ID = "run-agent-runtime-smoke"
LATEST_REF = "main@agent-runtime-smoke"
AFFECTED_FILE = "src/agent_runtime_smoke/app.py"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-code-sentinel-runtime-") as temp:
        root = Path(temp)
        db_path = root / "state.db"
        project_path = _seed_project(root / "target-repo")
        _seed_state(db_path, project_path)

        context_code, context_payload = project_context(project_path, max_files=20)
        if context_code != 0:
            return _emit_runtime_blocked("PROJECT_CONTEXT_FAILED", context_payload)
        selected_file = _selected_file(context_payload)
        if selected_file is None:
            return _emit_runtime_blocked("AFFECTED_FILE_NOT_IN_PROJECT_CONTEXT", context_payload)

        contract_payload = build_analysis_contract(
            AnalysisContractInput(
                project_id=PROJECT_ID,
                run_id=RUN_ID,
                target_project=str(project_path),
                files_to_analyze=[selected_file["path"]],
                validation_commands=["python3 -m pytest tests -q"],
            )
        )
        analysis_payload = {
            **contract_payload["analysis_payload_template"],
            "latest_ref": LATEST_REF,
            "finding_candidates": [
                {
                    "category": "code_quality",
                    "severity": "high",
                    "file_path": selected_file["path"],
                    "line_number": 1,
                    "title": "Runtime smoke task selected from project context",
                    "description": "Agent-owned analysis selected a bounded file for task takeover.",
                    "evidence": f"{selected_file['path']}:1 selected from project_context file_inventory",
                    "source": "agent_reasoning",
                    "rule_id": "agent_runtime_smoke_review",
                }
            ],
        }
        analyze_code, analyze_payload = analyze_to_state(db_path, analysis_payload)
        if analyze_code != 0:
            return _emit_runtime_blocked("ANALYZE_TO_STATE_FAILED", analyze_payload)

        cycle_code, cycle_payload = run_autonomous_cycle(
            db_path,
            {
                "project_id": PROJECT_ID,
                "run_id": RUN_ID,
                "latest_ref": LATEST_REF,
                "project_path": str(project_path),
                "owner": "agent-runtime-smoke",
            },
        )
        if cycle_code != 0:
            return _emit_runtime_blocked("RUN_CYCLE_FAILED", cycle_payload)

        report_code, report_payload = report(db_path, RUN_ID)
        if report_code != 0:
            return _emit_runtime_blocked("REPORT_FAILED", report_payload)

        checks = {
            "project_context_consumed": selected_file["path"] == AFFECTED_FILE,
            "agent_finding_persisted": analyze_payload["analysis"]["counts"]["findings"] == 1,
            "task_created": analyze_payload["task_creation"]["counts"]["created_tasks"] == 1,
            "task_takeover_selected": (
                cycle_payload["selected_task_for_agent_takeover"]["affected_file"] == AFFECTED_FILE
            ),
            "qa_review_outcomes_complete": report_payload["missing_review_outcomes"] == [],
            "lock_released": cycle_payload["lock_released"] is True,
        }
        status = "agent_runtime_cycle_smoke_passed" if all(checks.values()) else "agent_runtime_cycle_smoke_failed"
        print(
            json.dumps(
                {
                    "status": status,
                    "script_name": "scripts/agent_runtime_cycle_smoke.py",
                    "script_execution_mode": "python_executed_from_cloned_repo",
                    "external_ai_executor_used": False,
                    "project_id": PROJECT_ID,
                    "run_id": RUN_ID,
                    "latest_ref": LATEST_REF,
                    "project_context_summary": context_payload["context_summary"],
                    "selected_context_file": selected_file,
                    "analyze_to_state": {
                        "mode": analyze_payload["mode"],
                        "findings": analyze_payload["analysis"]["counts"]["findings"],
                        "created_tasks": analyze_payload["task_creation"]["counts"]["created_tasks"],
                    },
                    "cycle": {
                        "status": cycle_payload["status"],
                        "selected_task_for_agent_takeover": cycle_payload["selected_task_for_agent_takeover"],
                        "lock_released": cycle_payload["lock_released"],
                    },
                    "report": {
                        "counts": report_payload["counts"],
                        "missing_review_outcomes": report_payload["missing_review_outcomes"],
                        "qa_review_outcomes": report_payload["qa_review_outcomes"],
                    },
                    "checks": checks,
                    "write_actions": [],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if all(checks.values()) else 1


def _seed_project(project_path: Path) -> Path:
    (project_path / "src" / "agent_runtime_smoke").mkdir(parents=True)
    (project_path / "tests").mkdir()
    (project_path / "AGENTS.md").write_text(
        "# Runtime Smoke Rules\n\nPreserve validation evidence before completion.\n",
        encoding="utf-8",
    )
    (project_path / "README.md").write_text("# Runtime Smoke Project\n", encoding="utf-8")
    (project_path / "pyproject.toml").write_text("[project]\nname = 'agent-runtime-smoke'\n", encoding="utf-8")
    (project_path / "src" / "agent_runtime_smoke" / "__init__.py").write_text("", encoding="utf-8")
    (project_path / AFFECTED_FILE).write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    (project_path / "tests" / "test_app.py").write_text(
        "from agent_runtime_smoke.app import main\n\ndef test_main():\n    assert main() == 'ok'\n",
        encoding="utf-8",
    )
    return project_path


def _seed_state(db_path: Path, project_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch, status) values (?, ?, ?, ?)",
            (PROJECT_ID, str(project_path), "main", "active"),
        )
        conn.execute(
            "insert into memories(id, project_id, latest_ref, memory_json, stale_memory_decision) values (?, ?, ?, ?, ?)",
            (
                "memory-agent-runtime-smoke",
                PROJECT_ID,
                "main@previous",
                json.dumps({"next_autonomous_step": "run local runtime smoke"}),
                "fresh",
            ),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            (RUN_ID, PROJECT_ID, "in_progress", "analysis"),
        )
        conn.commit()


def _selected_file(context_payload: dict[str, Any]) -> dict[str, Any] | None:
    for item in context_payload["file_inventory"]["files"]:
        if item["path"] == AFFECTED_FILE:
            return item
    return None


def _emit_runtime_blocked(code: str, details: dict[str, Any]) -> int:
    print(
        json.dumps(
            {
                "status": "blocked",
                "blocker_code": code,
                "script_name": "scripts/agent_runtime_cycle_smoke.py",
                "script_execution_mode": "python_executed_from_cloned_repo",
                "details": details,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
