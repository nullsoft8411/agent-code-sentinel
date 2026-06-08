from __future__ import annotations

import json
import os
import subprocess
import sys

from runtime_cli_helpers import RUNTIME_ROOT, SRC_ROOT


def test_agent_runtime_cycle_smoke_script_runs_from_repo_checkout() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    result = subprocess.run(
        [sys.executable, "scripts/agent_runtime_cycle_smoke.py"],
        cwd=RUNTIME_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "agent_runtime_cycle_smoke_passed"
    assert payload["script_execution_mode"] == "python_executed_from_cloned_repo"
    assert payload["external_ai_executor_used"] is False
    assert payload["selected_context_file"]["path"] == "src/agent_runtime_smoke/app.py"
    assert payload["analyze_to_state"]["findings"] == 1
    assert payload["analyze_to_state"]["created_tasks"] == 1
    assert payload["cycle"]["selected_task_for_agent_takeover"]["affected_file"] == "src/agent_runtime_smoke/app.py"
    assert payload["cycle"]["lock_released"] is True
    assert payload["report"]["missing_review_outcomes"] == []
    assert all(payload["checks"].values())
    assert payload["write_actions"] == []
