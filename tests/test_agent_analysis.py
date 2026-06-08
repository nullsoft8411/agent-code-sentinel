from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database
from code_sentinel_agent.agent_analysis import analyze_context
from code_sentinel_agent.output_contract import normalize_severity, stable_finding_signature


def test_severity_mapping_and_signature_are_deterministic() -> None:
    assert normalize_severity("blocking") == "critical"
    assert normalize_severity("ERROR") == "high"
    assert normalize_severity("warn") == "medium"
    assert normalize_severity("advisory") == "low"
    assert normalize_severity("unexpected") == "medium"

    first = stable_finding_signature(
        category="security",
        rule_id="secret_assignment",
        file_path="src/app.py",
        line_number=3,
        title="Potential hardcoded secret",
        description="Secret-like assignment detected in repository context.",
    )
    second = stable_finding_signature(
        category="security",
        rule_id="secret_assignment",
        file_path="src/app.py",
        line_number=3,
        title="Potential hardcoded secret",
        description="Secret-like assignment detected in repository context.",
    )

    assert first == second
    assert first.startswith("finding:")


def test_analyze_context_normalizes_agent_reasoned_finding_without_secret_leak() -> None:
    payload = {
        "project_id": "proj-agent-e2e",
        "run_id": "run-agent-analysis",
        "finding_candidates": [
            {
                "category": "security",
                "severity": "error",
                "file_path": "src/settings.py",
                "line_number": 7,
                "title": "Token is hardcoded",
                "description": "token='super-secret-value' should not be stored in source.",
                "evidence": "token='super-secret-value'",
                "source": "agent_reasoning",
                "rule_id": "secret_assignment",
            }
        ],
    }

    exit_code, result = analyze_context(payload)
    serialized = json.dumps(result, sort_keys=True)

    assert exit_code == 0
    assert result["status"] == "passed"
    assert result["counts"] == {"findings": 1}
    assert result["secret_redaction_applied"] is True
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["signature"].startswith("finding:")
    assert "super-secret-value" not in serialized
    assert "REDACTED" in serialized


def test_analyze_context_cli_e2e_returns_findings_and_fix_plan_without_secrets() -> None:
    secret_value = "sk-test-should-never-leak"
    payload = {
        "project_id": "proj-agent-e2e",
        "run_id": "run-agent-analysis-e2e",
        "context": {
            "files": [
                {
                    "path": "src/service.py",
                    "content": "API_KEY = \"" + secret_value + "\"\nprint('ready')\n",
                }
            ],
            "command_results": [
                {
                    "command": "pytest tests/test_service.py -q",
                    "exit_code": 1,
                    "stderr": "src/service.py:1: AssertionError: leaked API_KEY = \"" + secret_value + "\"",
                }
            ],
            "validation_commands": ["pytest tests/test_service.py -q"],
        },
    }

    result = run_cli("analyze-context", "--input-json", json.dumps(payload))
    output = parse_json(result)
    serialized = json.dumps(output, sort_keys=True)

    assert result.returncode == 0, result.stderr
    assert output["status"] == "passed"
    assert output["output_contract_version"] == "agent-analysis.v1"
    assert output["counts"]["findings"] == 2
    assert output["fix_plan"]["requires_approval"] is True
    assert output["fix_plan"]["target_files"] == ["src/service.py"]
    assert output["fix_plan"]["validation_commands"] == ["pytest tests/test_service.py -q"]
    assert {finding["category"] for finding in output["findings"]} == {"security", "validation"}
    assert all(finding["signature"].startswith("finding:") for finding in output["findings"])
    assert secret_value not in serialized
    assert "API_KEY=[REDACTED]" in serialized


def test_analysis_contract_tells_agent_to_supply_reasoned_findings() -> None:
    result = run_cli(
        "analysis-contract",
        "--project-id",
        "proj-agent-e2e",
        "--run-id",
        "run-contract",
        "--target-project",
        "nullsoft8411/agent-code-sentinel",
        "--file",
        "src/settings.py",
        "--validation-command",
        "python3 -m pytest tests -q",
    )
    output = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert output["mode"] == "agent_supplied_analysis_contract"
    assert "Workspace Agent reads repository context" in output["agent_role"]
    assert "Scripts do not call or wrap an external AI executor" in output["script_role"]
    assert output["analysis_payload_template"]["finding_candidates"] == []
    assert output["analysis_payload_template"]["context"]["files_to_analyze"] == ["src/settings.py"]


def test_analyze_to_state_persists_agent_supplied_findings_tasks_and_takeover(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent-e2e", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-analysis-state", "proj-agent-e2e", "in_progress", "analysis"),
        )
        conn.commit()

    payload = {
        "project_id": "proj-agent-e2e",
        "run_id": "run-analysis-state",
        "latest_ref": "main@test",
        "context": {"validation_commands": ["python3 -m pytest tests -q"]},
        "finding_candidates": [
            {
                "category": "security",
                "severity": "high",
                "file_path": "src/settings.py",
                "line_number": 1,
                "title": "Token is hardcoded",
                "description": "TOKEN='sk-test-should-redact' is stored in source.",
                "evidence": "TOKEN='sk-test-should-redact'",
                "source": "agent_reasoning",
                "rule_id": "secret_assignment",
            }
        ],
    }

    result = run_cli(
        "analyze-to-state",
        "--db",
        str(db_path),
        "--input-json",
        json.dumps(payload),
    )
    output = parse_json(result)
    serialized = json.dumps(output, sort_keys=True)

    assert result.returncode == 0, result.stderr
    assert output["status"] == "passed"
    assert output["mode"] == "agent_supplied_analysis_persistence"
    assert "Workspace Agent performs reasoning" in output["agent_execution_boundary"]
    assert output["analysis"]["counts"]["findings"] == 1
    assert output["scan_job"]["status"] == "completed"
    assert output["task_creation"]["created_tasks"]
    assert output["selected_task_for_agent_takeover"]["status"] == "pending"
    assert output["selected_task_for_agent_takeover"]["affected_file"] == "src/settings.py"
    assert output["report"]["counts"]["findings"] == 1
    assert output["report"]["counts"]["tasks"] == 1
    assert "sk-test-should-redact" not in serialized
    assert "REDACTED" in serialized
