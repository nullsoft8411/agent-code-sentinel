from __future__ import annotations

import subprocess
from pathlib import Path

from runtime_cli_helpers import parse_json, run_cli


def test_project_context_collects_rules_docs_configs_git_and_files(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (tmp_path / "AGENTS.md").write_text("# Parent rules\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# Repo rules\n", encoding="utf-8")
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project / "package.json").write_text('{"scripts":{"test":"node --test"}}\n', encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "app.ts").write_text("export const ok = true;\n", encoding="utf-8")
    (project / ".github" / "workflows").mkdir(parents=True)
    (project / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    result = run_cli("project-context", "--project", str(project), "--max-files", "10")
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["project_rules"]["agents_md_count"] >= 2
    assert payload["docs"][0]["path"] == "README.md"
    assert payload["config_files"][0]["path"] == "package.json"
    assert payload["ci_files"][0]["path"] == ".github/workflows/ci.yml"
    assert payload["git_truth"]["status"] == "passed"
    assert payload["git_truth"]["dirty"] is False
    assert "node_root" in payload["check_detection"]["signals"]
    assert payload["context_summary"]["has_project_rules"] is True
    assert any(item["path"] == "src/app.ts" for item in payload["file_inventory"]["files"])


def test_project_context_blocks_missing_project(tmp_path: Path) -> None:
    result = run_cli("project-context", "--project", str(tmp_path / "missing"))
    payload = parse_json(result)

    assert result.returncode == 2
    assert payload["status"] == "blocked"
    assert payload["blocker_code"] == "PROJECT_PATH_MISSING"


def test_preflight_includes_project_context(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Repo rules\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    result = run_cli("preflight", "--project", str(project))
    payload = parse_json(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "passed"
    assert payload["project_context"]["status"] == "passed"
    assert payload["project_context"]["context_summary"]["has_project_rules"] is True
    assert "python" in payload["project_context"]["check_detection"]["signals"]
