from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .output_contract import sanitize_text

ALLOWED_COMMAND_PREFIXES = (
    "pytest",
    "python3 -m pytest",
    "python -m pytest",
    "ruff check",
    "mypy",
    "npm test",
    "npm run lint",
    "npm run build",
    "go test",
    "go vet",
)


@dataclass(frozen=True)
class ValidationResult:
    project_id: str
    run_id: str
    gate: str
    command: str
    exit_code: int
    cwd: str | None
    stdout_summary: str
    stderr_summary: str
    status: str
    evidence: str
    findings: list[dict[str, Any]]
    secret_redaction_applied: bool


def normalize_validation_payload(payload: dict[str, Any]) -> tuple[int, dict[str, Any] | ValidationResult]:
    try:
        project_id = _required(payload, "project_id")
        run_id = _required(payload, "run_id")
        gate = _required(payload, "gate")
        command = _required(payload, "command")
        exit_code = int(payload.get("exit_code"))
    except (TypeError, ValueError) as exc:
        return 2, {"status": "blocked", "blocker_code": "INVALID_VALIDATION_PAYLOAD", "reason": str(exc)}

    if not _is_allowed_command(command):
        safe_command, _ = sanitize_text(command)
        return 2, {
            "status": "blocked",
            "blocker_code": "VALIDATION_COMMAND_NOT_ALLOWED",
            "command": safe_command,
            "allowed_prefixes": list(ALLOWED_COMMAND_PREFIXES),
        }

    stdout_summary, stdout_redacted = sanitize_text(_summary(payload.get("stdout")))
    stderr_summary, stderr_redacted = sanitize_text(_summary(payload.get("stderr")))
    command_safe, command_redacted = sanitize_text(command)
    cwd = str(payload.get("cwd")).strip() if payload.get("cwd") else None
    status = "passed" if exit_code == 0 else "blocking"
    evidence = f"{command_safe} exited {exit_code}"
    if stderr_summary:
        evidence = f"{evidence}: {stderr_summary}"
    elif stdout_summary:
        evidence = f"{evidence}: {stdout_summary}"

    findings = [] if exit_code == 0 else _findings_from_output(
        gate=gate,
        command=command_safe,
        stdout=stdout_summary,
        stderr=stderr_summary,
    )
    return 0, ValidationResult(
        project_id=project_id,
        run_id=run_id,
        gate=gate,
        command=command_safe,
        exit_code=exit_code,
        cwd=cwd,
        stdout_summary=stdout_summary,
        stderr_summary=stderr_summary,
        status=status,
        evidence=evidence,
        findings=findings,
        secret_redaction_applied=stdout_redacted or stderr_redacted or command_redacted,
    )


def _findings_from_output(*, gate: str, command: str, stdout: str, stderr: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in "\n".join([stderr, stdout]).splitlines() if line.strip()]
    if not lines:
        lines = [f"{command} exited non-zero"]
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()
    for line in lines[:20]:
        file_path, line_number, message = _parse_output_line(line)
        key = (file_path, line_number, message)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "rule_id": f"qa_gate_{gate}",
                "file_path": file_path,
                "line_number": line_number,
                "severity": "high",
                "title": f"{gate} gate failed",
                "message": message,
                "evidence": line,
            }
        )
    return findings


def _parse_output_line(line: str) -> tuple[str, int | None, str]:
    match = re.match(r"^(?P<path>[^:\s][^:]*):(?P<line>\d+)(?::\d+)?:\s*(?P<message>.+)$", line)
    if not match:
        return "project", None, line
    return match.group("path"), int(match.group("line")), match.group("message")


def _is_allowed_command(command: str) -> bool:
    normalized = " ".join(command.strip().split())
    return any(normalized == prefix or normalized.startswith(f"{prefix} ") for prefix in ALLOWED_COMMAND_PREFIXES)


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _summary(value: Any, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    return text[:limit]
