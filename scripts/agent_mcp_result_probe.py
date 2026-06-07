#!/usr/bin/env python3
"""Probe that proves Workspace Agent repo scripts can consume MCP DB results.

The Workspace Agent should call the PIKA MCP state tools first, clone this
repository, then execute this script from the cloned checkout with the MCP
results as JSON. The script never calls external services and does not require
secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


EXPECTED = {
    "project_id": "proj-agent-e2e",
    "target_project": "workspace-agent-script-e2e",
    "default_branch": "main",
    "latest_ref": "main@agent-e2e",
    "stale_memory_decision": "fresh",
}


def _load_payload(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise ValueError("No MCP result JSON was provided to the script.")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("MCP result JSON must be an object.")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-json", default="")
    args = parser.parse_args()

    try:
        payload = _load_payload(args.mcp_json or sys.stdin.read())
    except json.JSONDecodeError as error:
        return _emit_blocked("MCP_JSON_INVALID", str(error))
    except ValueError as error:
        return _emit_blocked("MCP_JSON_MISSING", str(error))

    project = payload.get("project_result", {}).get("project", {})
    memory = payload.get("memory_result", {}).get("memory", {})
    checks = {
        "project_id": project.get("id") == EXPECTED["project_id"],
        "target_project": project.get("target") == EXPECTED["target_project"],
        "default_branch": project.get("default_branch") == EXPECTED["default_branch"],
        "latest_ref": memory.get("latest_ref") == EXPECTED["latest_ref"],
        "stale_memory_decision": memory.get("stale_memory_decision") == EXPECTED["stale_memory_decision"],
    }
    passed = all(checks.values())
    print(json.dumps({
        "status": "script_mcp_db_e2e_passed" if passed else "script_mcp_db_e2e_failed",
        "script_name": "scripts/agent_mcp_result_probe.py",
        "script_execution_mode": "python_executed_from_cloned_repo",
        "mcp_result_consumed": True,
        "project_id": project.get("id"),
        "target_project": project.get("target"),
        "default_branch": project.get("default_branch"),
        "latest_ref": memory.get("latest_ref"),
        "stale_memory_decision": memory.get("stale_memory_decision"),
        "checks": checks,
        "write_actions": [],
    }, indent=2, sort_keys=True))
    return 0 if passed else 1


def _emit_blocked(code: str, reason: str) -> int:
    print(json.dumps({
        "status": "blocked",
        "blocker_code": code,
        "script_execution_mode": "python_executed_from_cloned_repo",
        "reason": reason,
    }, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

