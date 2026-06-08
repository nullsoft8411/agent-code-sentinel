from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .output_contract import OUTPUT_CONTRACT_VERSION, finding_contract_fields


@dataclass(frozen=True)
class AnalysisContractInput:
    project_id: str
    run_id: str
    target_project: str
    files_to_analyze: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)


def build_analysis_contract(contract: AnalysisContractInput) -> dict[str, Any]:
    return {
        "status": "passed",
        "mode": "agent_supplied_analysis_contract",
        "project_id": contract.project_id,
        "run_id": contract.run_id,
        "target_project": contract.target_project,
        "agent_role": "The Workspace Agent reads repository context and writes finding_candidates itself.",
        "script_role": (
            "Scripts only collect deterministic context, normalize/persist Agent-supplied findings, "
            "create tasks, and report state. Scripts do not call or wrap an external AI executor."
        ),
        "required_agent_steps": [
            "Read the requested repository files and project rules before creating findings.",
            "Create finding_candidates with concrete file_path, line_number when known, evidence, severity, rule_id, and source=agent_reasoning.",
            "Do not claim a finding unless it is grounded in file, command, or project-rule evidence.",
            "Pass the completed JSON payload to analyze-to-state or state_analyze_to_state for persistence.",
        ],
        "finding_contract_fields": finding_contract_fields(),
        "analysis_payload_template": {
            "project_id": contract.project_id,
            "run_id": contract.run_id,
            "context": {
                "target_project": contract.target_project,
                "files_to_analyze": contract.files_to_analyze,
                "validation_commands": contract.validation_commands,
            },
            "finding_candidates": [],
        },
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
    }
