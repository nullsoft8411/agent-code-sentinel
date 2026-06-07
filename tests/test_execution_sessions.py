from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import connect, initialize_database


def seed_run(db_path: Path) -> None:
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-agent", "proj-agent", "in_progress", "execution_session"),
        )
        conn.commit()


def test_execution_session_cli_e2e_stores_sanitized_agent_native_record(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_run(db_path)
    secret_value = "sk-agent-session-secret"
    payload = {
        "id": "session-1",
        "run_id": "run-agent",
        "project_id": "proj-agent",
        "task_id": None,
        "session_type": "analysis",
        "script_name": "analyze-context",
        "execution_method": "python_executed_from_cloned_repo",
        "command": "PYTHONPATH=src python3 -m code_sentinel_agent.cli analyze-context",
        "status": "passed",
        "output": {
            "status": "passed",
            "finding_count": 1,
            "note": "token='" + secret_value + "'",
        },
        "error_summary": None,
        "files_modified": ["src/service.py"],
        "attempt_log": [
            {
                "step": "script_completed",
                "status": "passed",
                "evidence": "token='" + secret_value + "'",
            }
        ],
        "completed_at": "2026-06-07T00:00:00+00:00",
    }

    result = run_cli("execution-session", "--db", str(db_path), "--payload-json", json.dumps(payload))
    output = parse_json(result)
    serialized = json.dumps(output, sort_keys=True)

    assert result.returncode == 0, result.stderr
    assert output["status"] == "passed"
    session = output["session"]
    assert session["id"] == "session-1"
    assert session["run_id"] == "run-agent"
    assert session["session_type"] == "analysis"
    assert session["execution_method"] == "python_executed_from_cloned_repo"
    assert session["command"] == "PYTHONPATH=src python3 -m code_sentinel_agent.cli analyze-context"
    assert session["status"] == "passed"
    assert session["files_modified"] == ["src/service.py"]
    assert session["secret_redaction_applied"] is True
    assert secret_value not in serialized
    assert "token=[REDACTED]" in serialized
    for forbidden in ["ClaudeExecutor", "claude_executor", "Claude CLI", "tmux", "model", "token"]:
        assert forbidden not in session.keys()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            select script_name, execution_method, status, output_json,
                   files_modified_json, attempt_log_json, completed_at
            from agent_execution_sessions
            where id = ?
            """,
            ("session-1",),
        ).fetchone()

    assert row[0] == "analyze-context"
    assert row[1] == "python_executed_from_cloned_repo"
    assert row[2] == "passed"
    assert secret_value not in row[3]
    assert row[4] == '["src/service.py"]'
    assert secret_value not in row[5]
    assert row[6] == "2026-06-07T00:00:00+00:00"
