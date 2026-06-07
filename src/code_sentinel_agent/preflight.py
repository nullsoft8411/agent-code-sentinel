from __future__ import annotations

from pathlib import Path

from .check_detection import detect_checks


def preflight(project: str) -> tuple[int, dict]:
    path = Path(project)
    if not path.exists():
        return 2, {
            "status": "blocked",
            "blocker_code": "PROJECT_PATH_MISSING",
            "reason": f"project path does not exist: {project}",
            "next_action": "provide an existing local path or use a read-only remote analysis mode",
        }
    if not path.is_dir():
        return 2, {
            "status": "blocked",
            "blocker_code": "PROJECT_PATH_NOT_DIRECTORY",
            "reason": f"project path is not a directory: {project}",
            "next_action": "provide a repository directory",
        }

    detection = detect_checks(path)
    return 0, {
        "status": "passed",
        "target_project": str(path.resolve()),
        "access_mode": "local_path",
        "project_rules": {
            "agents_md": "present" if (path / "AGENTS.md").exists() else "missing",
        },
        "check_detection": detection,
    }

