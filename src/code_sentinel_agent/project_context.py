from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .check_detection import detect_checks

CONFIG_FILES = [
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
]

SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}


def project_context(project: str | Path, *, max_files: int = 80) -> tuple[int, dict[str, Any]]:
    root = Path(project).resolve()
    if not root.exists():
        return 2, {
            "status": "blocked",
            "blocker_code": "PROJECT_PATH_MISSING",
            "reason": f"project path does not exist: {project}",
        }
    if not root.is_dir():
        return 2, {
            "status": "blocked",
            "blocker_code": "PROJECT_PATH_NOT_DIRECTORY",
            "reason": f"project path is not a directory: {project}",
        }

    agents_chain = _agents_chain(root)
    docs = _docs(root)
    config_files = _config_files(root)
    ci_files = _ci_files(root)
    git_truth = _git_truth(root)
    file_inventory = _file_inventory(root, max_files=max_files)
    checks = detect_checks(root)

    recommended_next_step = "select work from shared state before proposing edits"
    if not agents_chain:
        recommended_next_step = "inspect repository rules manually because no AGENTS.md chain was found"
    elif not checks["recommended_checks"]:
        recommended_next_step = "identify validation commands before any fix attempt"

    return 0, {
        "status": "passed",
        "target_project": str(root),
        "project_rules": {
            "agents_md_chain": agents_chain,
            "agents_md_count": len(agents_chain),
        },
        "docs": docs,
        "config_files": config_files,
        "ci_files": ci_files,
        "git_truth": git_truth,
        "check_detection": checks,
        "file_inventory": file_inventory,
        "context_summary": {
            "has_project_rules": bool(agents_chain),
            "has_docs": bool(docs),
            "has_validation_signals": bool(checks["recommended_checks"]),
            "has_git_truth": git_truth["status"] == "passed",
            "relevant_files_count": len(file_inventory["files"]),
        },
        "next_autonomous_step": recommended_next_step,
    }


def _agents_chain(root: Path) -> list[dict[str, Any]]:
    candidates = [root, *root.parents]
    result: list[dict[str, Any]] = []
    for directory in reversed(candidates):
        path = directory / "AGENTS.md"
        if path.exists() and path.is_file():
            result.append(_file_record(path, root))
    return result


def _docs(root: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(root.glob("README*")):
        if path.is_file():
            docs.append(_file_record(path, root))
    docs_dir = root / "docs"
    if docs_dir.exists():
        for path in sorted(docs_dir.glob("*.md"))[:30]:
            if path.is_file():
                docs.append(_file_record(path, root))
    return docs


def _config_files(root: Path) -> list[dict[str, Any]]:
    return [_file_record(root / name, root) for name in CONFIG_FILES if (root / name).is_file()]


def _ci_files(root: Path) -> list[dict[str, Any]]:
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return []
    return [_file_record(path, root) for path in sorted(workflows.iterdir()) if path.is_file()]


def _git_truth(root: Path) -> dict[str, Any]:
    top_level = _git(root, "rev-parse", "--show-toplevel")
    if top_level["exit_code"] != 0:
        return {
            "status": "not_available",
            "reason": "not a git repository or git unavailable",
        }
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--short")
    return {
        "status": "passed",
        "top_level": top_level["stdout"],
        "branch": branch["stdout"] or None,
        "head": head["stdout"] or None,
        "dirty": bool(status["stdout"]),
        "changed_files": [line for line in status["stdout"].splitlines() if line],
    }


def _file_inventory(root: Path, *, max_files: int) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            skipped += 1
            continue
        if path.suffix not in SOURCE_EXTENSIONS and path.name not in CONFIG_FILES:
            skipped += 1
            continue
        if len(files) >= max_files:
            skipped += 1
            continue
        files.append(_file_record(path, root))
    return {
        "status": "passed",
        "selection": "deterministic_relevant_file_inventory",
        "max_files": max_files,
        "files": files,
        "skipped_count": skipped,
    }


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = str(path)
    return {
        "path": relative_path,
        "bytes": path.stat().st_size,
    }


def _git(root: Path, *args: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": 127, "stdout": "", "stderr": str(exc)}
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
