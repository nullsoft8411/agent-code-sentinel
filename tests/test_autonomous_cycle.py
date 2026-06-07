from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database


def seed_cycle_state(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-devopshub", "nullsoft8411/devopshub", "main"),
        )
        conn.execute(
            """
            insert into runs(id, project_id, status, current_focus, next_autonomous_step, completed_at)
            values (?, ?, ?, ?, ?, datetime('now'))
            """,
            ("run-prev", "proj-devopshub", "completed", "qa-gate", "rerun qa gates"),
        )
        conn.execute(
            """
            insert into memories(id, project_id, latest_ref, memory_json, stale_memory_decision)
            values (?, ?, ?, ?, ?)
            """,
            (
                "memory-prev",
                "proj-devopshub",
                "main@old",
                json.dumps(
                    {
                        "next_autonomous_step": "rerun qa gates",
                        "bounded_fix_queue": ["src/auth/token.ts"],
                        "qa_gate": {"validation": "blocking"},
                    }
                ),
                "needs_repository_refresh",
            ),
        )
        conn.commit()


def test_resume_cycle_loads_memory_creates_run_and_emits_next_step(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_cycle_state(db_path)

    result = run_cli(
        "resume-cycle",
        "--db",
        str(db_path),
        "--project-id",
        "proj-devopshub",
        "--run-id",
        "run-next",
        "--latest-ref",
        "main@new",
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["project_id"] == "proj-devopshub"
    assert payload["previous_run"]["id"] == "run-prev"
    assert payload["loaded_memory"]["next_autonomous_step"] == "rerun qa gates"
    assert payload["repository_delta"] == "repository truth must be checked before trusting memory"
    assert payload["stale_memory_decision"] == "needs_repository_refresh"
    assert payload["run"]["id"] == "run-next"
    assert payload["run"]["status"] == "in_progress"
    assert payload["run"]["current_focus"] == "autonomous_cycle"
    assert payload["next_autonomous_step"] == "preflight main@new before continuing: rerun qa gates"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select status, current_focus, next_autonomous_step from runs where id = ?",
            ("run-next",),
        ).fetchone()

    assert row == (
        "in_progress",
        "autonomous_cycle",
        "preflight main@new before continuing: rerun qa gates",
    )
