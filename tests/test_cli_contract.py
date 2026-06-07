from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database


def create_mixed_fixture(root: Path) -> Path:
    fixture = root / "mixed-repo"
    (fixture / ".github" / "workflows").mkdir(parents=True)
    (fixture / "package.json").write_text('{"scripts":{"test":"node --test"}}\n', encoding="utf-8")
    (fixture / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (fixture / "go.mod").write_text("module example.com/fixture\n", encoding="utf-8")
    (fixture / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    (fixture / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    return fixture


def seed_runtime_db(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-devopshub", "nullsoft8411/devopshub", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus, next_autonomous_step) values (?, ?, ?, ?, ?)",
            ("run-1", "proj-devopshub", "in_progress", "validation", "fix validation blocker"),
        )
        conn.execute(
            """
            insert into findings(id, run_id, project_id, signature, category, severity, file_path, title)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "finding-1",
                "run-1",
                "proj-devopshub",
                "src/auth/token.ts:validation:process",
                "validation",
                "blocking",
                "src/auth/token.ts",
                "process is not defined",
            ),
        )
        conn.execute(
            """
            insert into tasks(id, finding_id, run_id, project_id, status, priority, title, affected_file)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-1",
                "finding-1",
                "run-1",
                "proj-devopshub",
                "pending",
                10,
                "Fix validation blocker",
                "src/auth/token.ts",
            ),
        )
        conn.execute(
            """
            insert into qa_gate_results(id, run_id, gate, status, evidence, next_action)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("gate-1", "run-1", "validation", "blocking", "npm test exited 1", "fix blocker"),
        )
        conn.execute(
            """
            insert into memories(id, project_id, latest_ref, memory_json, stale_memory_decision)
            values (?, ?, ?, ?, ?)
            """,
            (
                "memory-1",
                "proj-devopshub",
                "main@old",
                '{"next_autonomous_step":"verify checks"}',
                "needs_repository_refresh",
            ),
        )
        conn.commit()


def test_detect_checks_returns_deterministic_json(tmp_path: Path) -> None:
    fixture = create_mixed_fixture(tmp_path)

    result = run_cli("detect-checks", "--path", str(fixture))

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["path"] == str(fixture.resolve())
    assert payload["signals"] == [
        "node_root",
        "python",
        "go",
        "github_actions",
        "agents_md",
    ]
    assert payload["recommended_checks"] == [
        "npm test",
        "npm run lint",
        "npm run build",
        "pytest",
        "ruff check .",
        "mypy .",
        "go test ./...",
        "go vet ./...",
    ]


def test_preflight_blocks_missing_project_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = run_cli("preflight", "--project", str(missing))

    assert result.returncode == 2
    payload = parse_json(result)
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "PROJECT_PATH_MISSING"
    assert payload["next_action"] == "provide an existing local path or use a read-only remote analysis mode"


def test_preflight_uses_check_detection_for_local_path(tmp_path: Path) -> None:
    fixture = create_mixed_fixture(tmp_path)

    result = run_cli("preflight", "--project", str(fixture))

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["access_mode"] == "local_path"
    assert payload["project_rules"]["agents_md"] == "present"
    assert "npm test" in payload["check_detection"]["recommended_checks"]


def test_qa_gates_memory_delta_and_report_read_sqlite_state(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_runtime_db(db_path)

    qa = run_cli("qa-gates", "--db", str(db_path), "--run-id", "run-1")
    memory = run_cli("memory-delta", "--db", str(db_path), "--project-id", "proj-devopshub")
    report = run_cli("report", "--db", str(db_path), "--run-id", "run-1")

    assert qa.returncode == 2
    qa_payload = parse_json(qa)
    assert qa_payload["status"] == "blocking"
    assert qa_payload["blocking_gates"] == ["validation"]
    assert qa_payload["gates"][0]["evidence"] == "npm test exited 1"

    assert memory.returncode == 0, memory.stderr
    memory_payload = parse_json(memory)
    assert memory_payload["status"] == "passed"
    assert memory_payload["stale_memory_decision"] == "needs_repository_refresh"
    assert memory_payload["latest_ref"] == "main@old"

    assert report.returncode == 0, report.stderr
    report_payload = parse_json(report)
    assert report_payload["status"] == "passed"
    assert report_payload["run"]["id"] == "run-1"
    assert report_payload["project"]["target"] == "nullsoft8411/devopshub"
    assert report_payload["counts"] == {
        "findings": 1,
        "tasks": 1,
        "qa_gates": 1,
        "validation_attempts": 0,
    }
