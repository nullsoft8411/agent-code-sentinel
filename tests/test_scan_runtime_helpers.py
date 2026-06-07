from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli

from code_sentinel_agent.db import initialize_database
from code_sentinel_agent.file_inventory import FileCheckInput, build_file_inventory, list_file_checks, record_file_check
from code_sentinel_agent.plugin_executions import (
    PluginExecutionInput,
    list_plugin_executions,
    record_plugin_execution,
)
from code_sentinel_agent.scan_jobs import ScanJobInput, create_scan_job, list_scan_jobs, update_scan_job_progress


def seed_scan_state(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into projects(id, target, default_branch) values (?, ?, ?)",
            ("proj-agent", "nullsoft8411/agent-code-sentinel", "main"),
        )
        conn.execute(
            "insert into runs(id, project_id, status, current_focus) values (?, ?, ?, ?)",
            ("run-agent", "proj-agent", "in_progress", "scan"),
        )
        conn.commit()


def test_scan_job_progress_and_listing(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_scan_state(db_path)

    created = create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="project_context",
            scan_type="context_inventory",
            target_ref="main@test",
        ),
    )
    updated = update_scan_job_progress(
        str(db_path),
        scan_job_id="scan-job-1",
        status="completed",
        files_total=3,
        files_scanned=2,
        files_skipped=1,
    )
    jobs = list_scan_jobs(str(db_path), project_id="proj-agent", run_id="run-agent")

    assert created["status"] == "running"
    assert updated["status"] == "completed"
    assert updated["files_total"] == 3
    assert updated["completed_at"] is not None
    assert [job["id"] for job in jobs] == ["scan-job-1"]


def test_plugin_execution_record_and_list(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_scan_state(db_path)
    create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="project_context",
            scan_type="context_inventory",
        ),
    )

    recorded = record_plugin_execution(
        str(db_path),
        PluginExecutionInput(
            id="plugin-1",
            project_id="proj-agent",
            run_id="run-agent",
            scan_job_id="scan-job-1",
            plugin_name="qg_preflight",
            status="passed",
            exit_code=0,
            stdout_summary="ok",
        ),
    )
    listed = list_plugin_executions(str(db_path), project_id="proj-agent", scan_job_id="scan-job-1")

    assert recorded["plugin_name"] == "qg_preflight"
    assert recorded["completed_at"] is not None
    assert len(listed) == 1
    assert listed[0]["status"] == "passed"


def test_file_inventory_and_file_check_persistence(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "src").mkdir()
    source = project / "src" / "app.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "ignored.py").write_text("print('skip')\n", encoding="utf-8")

    code, inventory = build_file_inventory(project)
    assert code == 0
    assert [item["path"] for item in inventory["files"]] == ["src/app.py"]
    assert inventory["files"][0]["language"] == "python"
    assert len(inventory["files"][0]["content_sha256"]) == 64

    db_path = tmp_path / "runtime.db"
    seed_scan_state(db_path)
    create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="file_inventory",
            scan_type="file_inventory",
        ),
    )
    file_info = inventory["files"][0]
    recorded = record_file_check(
        str(db_path),
        FileCheckInput(
            id="file-check-1",
            project_id="proj-agent",
            run_id="run-agent",
            scan_job_id="scan-job-1",
            file_path=file_info["path"],
            content_sha256=file_info["content_sha256"],
            language=file_info["language"],
            status="scanned",
            checks=[{"name": "inventory", "status": "passed"}],
        ),
    )
    listed = list_file_checks(str(db_path), project_id="proj-agent", run_id="run-agent")

    assert recorded["file_path"] == "src/app.py"
    assert recorded["language"] == "python"
    assert len(listed) == 1
    assert listed[0]["content_sha256"] == file_info["content_sha256"]


def test_scan_helper_cli_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    seed_scan_state(db_path)
    create_scan_job(
        str(db_path),
        ScanJobInput(
            id="scan-job-1",
            project_id="proj-agent",
            run_id="run-agent",
            scanner_name="project_context",
            scan_type="context_inventory",
        ),
    )

    progress = run_cli(
        "scan-job-progress",
        "--db",
        str(db_path),
        "--scan-job-id",
        "scan-job-1",
        "--status",
        "completed",
        "--files-total",
        "1",
        "--files-scanned",
        "1",
    )
    assert progress.returncode == 0, progress.stderr
    assert parse_json(progress)["scan_job"]["files_scanned"] == 1

    plugin = run_cli(
        "plugin-execution",
        "--db",
        str(db_path),
        "--payload-json",
        json.dumps(
            {
                "id": "plugin-1",
                "project_id": "proj-agent",
                "run_id": "run-agent",
                "scan_job_id": "scan-job-1",
                "plugin_name": "project_context",
                "status": "passed",
            }
        ),
    )
    assert plugin.returncode == 0, plugin.stderr

    listed_plugins = run_cli("plugin-executions", "--db", str(db_path), "--project-id", "proj-agent")
    assert parse_json(listed_plugins)["plugin_executions"][0]["id"] == "plugin-1"

    project = tmp_path / "repo"
    project.mkdir()
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    inventory = run_cli("file-inventory", "--project", str(project))
    assert parse_json(inventory)["files"][0]["path"] == "README.md"
