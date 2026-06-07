from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli
from code_sentinel_agent.db import initialize_database


def seed_approval(
    db_path: Path,
    *,
    run_id: str = "run-1",
    target_project: str = "nullsoft8411/devopshub",
    branch: str = "code-sentinel/test",
    allowed_paths: list[str] | None = None,
    allowed_actions: list[str] | None = None,
    expires_at: str | None = None,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into approvals(
              id, run_id, target_project, branch, allowed_paths_json,
              allowed_actions_json, approved_by, approval_evidence, expires_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "approval-1",
                run_id,
                target_project,
                branch,
                json.dumps(allowed_paths or ["docs/proof.md"]),
                json.dumps(allowed_actions or ["file_write"]),
                "operator",
                "explicit chat approval",
                expires_at,
            ),
        )
        conn.commit()


def test_write_guard_blocks_without_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    initialize_database(db_path)

    result = run_cli(
        "approval-check",
        "--db",
        str(db_path),
        "--run-id",
        "run-1",
        "--target-project",
        "nullsoft8411/devopshub",
        "--branch",
        "code-sentinel/test",
        "--path",
        "docs/proof.md",
        "--action",
        "file_write",
    )

    assert result.returncode == 2
    payload = parse_json(result)
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "WRITE_APPROVAL_MISSING"
    assert payload["write_allowed"] is False


def test_write_guard_blocks_wrong_project_path_and_action(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_approval(db_path)

    wrong_project = run_cli(
        "approval-check",
        "--db",
        str(db_path),
        "--run-id",
        "run-1",
        "--target-project",
        "nullsoft8411/other",
        "--branch",
        "code-sentinel/test",
        "--path",
        "docs/proof.md",
        "--action",
        "file_write",
    )
    wrong_path = run_cli(
        "approval-check",
        "--db",
        str(db_path),
        "--run-id",
        "run-1",
        "--target-project",
        "nullsoft8411/devopshub",
        "--branch",
        "code-sentinel/test",
        "--path",
        "src/app.ts",
        "--action",
        "file_write",
    )
    wrong_action = run_cli(
        "approval-check",
        "--db",
        str(db_path),
        "--run-id",
        "run-1",
        "--target-project",
        "nullsoft8411/devopshub",
        "--branch",
        "code-sentinel/test",
        "--path",
        "docs/proof.md",
        "--action",
        "pull_request",
    )

    assert parse_json(wrong_project)["blocker_code"] == "WRITE_APPROVAL_MISSING"
    assert parse_json(wrong_path)["blocker_code"] == "WRITE_PATH_NOT_APPROVED"
    assert parse_json(wrong_action)["blocker_code"] == "WRITE_ACTION_NOT_APPROVED"
    assert wrong_project.returncode == wrong_path.returncode == wrong_action.returncode == 2


def test_write_guard_allows_exact_approved_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_approval(db_path)

    result = run_cli(
        "approval-check",
        "--db",
        str(db_path),
        "--run-id",
        "run-1",
        "--target-project",
        "nullsoft8411/devopshub",
        "--branch",
        "code-sentinel/test",
        "--path",
        "docs/proof.md",
        "--action",
        "file_write",
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result)
    assert payload["status"] == "passed"
    assert payload["write_allowed"] is True
    assert payload["approval"]["allowed_paths"] == ["docs/proof.md"]
    assert payload["approval"]["allowed_actions"] == ["file_write"]
