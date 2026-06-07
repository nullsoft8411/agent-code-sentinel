from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .output_contract import (
    OUTPUT_CONTRACT_VERSION,
    finding_contract_fields,
    normalize_severity,
    sanitize_text,
    stable_finding_signature,
)


@dataclass(frozen=True)
class AnalysisInput:
    project_id: str
    run_id: str
    context: dict[str, Any]
    finding_candidates: list[dict[str, Any]]


@dataclass(frozen=True)
class ReasonedFinding:
    signature: str
    category: str
    severity: str
    file_path: str
    line_number: int | None
    title: str
    description: str
    source: str
    evidence: str
    rule_id: str
    status: str = "open"

    def as_finding_payload(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "category": self.category,
            "severity": self.severity,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "evidence": self.evidence,
            "rule_id": self.rule_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class FixPlan:
    summary: str
    steps: list[str]
    target_files: list[str]
    validation_commands: list[str]
    requires_approval: bool
    risk: str
    rollback: str

    def as_fix_plan_payload(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": self.steps,
            "target_files": self.target_files,
            "validation_commands": self.validation_commands,
            "requires_approval": self.requires_approval,
            "risk": self.risk,
            "rollback": self.rollback,
        }


@dataclass(frozen=True)
class AnalysisResult:
    status: str
    project_id: str
    run_id: str
    findings: list[ReasonedFinding]
    fix_plan: FixPlan
    secret_redaction_applied: bool

    def as_analysis_payload(self) -> dict[str, Any]:
        findings = [finding.as_finding_payload() for finding in self.findings]
        return {
            "status": self.status,
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
            "finding_contract_fields": finding_contract_fields(),
            "project_id": self.project_id,
            "run_id": self.run_id,
            "counts": {"findings": len(findings)},
            "findings": findings,
            "fix_plan": self.fix_plan.as_fix_plan_payload(),
            "secret_redaction_applied": self.secret_redaction_applied,
        }


def analyze_context(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        analysis_input = _parse_input(payload)
    except ValueError as exc:
        return 2, {"status": "blocked", "blocker_code": "INVALID_ANALYSIS_INPUT", "reason": str(exc)}

    findings, secret_redaction_applied = _collect_findings(analysis_input)
    fix_plan = _build_fix_plan(analysis_input.context, findings)
    result = AnalysisResult(
        status="passed",
        project_id=analysis_input.project_id,
        run_id=analysis_input.run_id,
        findings=findings,
        fix_plan=fix_plan,
        secret_redaction_applied=secret_redaction_applied,
    )
    return 0, result.as_analysis_payload()


def _parse_input(payload: dict[str, Any]) -> AnalysisInput:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    project_id = str(payload.get("project_id") or project.get("id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    if not run_id:
        raise ValueError("run_id is required")

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    candidate_values = payload.get("finding_candidates", payload.get("findings", []))
    finding_candidates = candidate_values if isinstance(candidate_values, list) else []
    return AnalysisInput(
        project_id=project_id,
        run_id=run_id,
        context=context,
        finding_candidates=[item for item in finding_candidates if isinstance(item, dict)],
    )


def _collect_findings(analysis_input: AnalysisInput) -> tuple[list[ReasonedFinding], bool]:
    redacted = False
    findings: list[ReasonedFinding] = []

    for candidate in analysis_input.finding_candidates:
        finding, was_redacted = _normalize_candidate(candidate, source_default="agent_reasoning")
        redacted = redacted or was_redacted
        findings.append(finding)

    for file_item in _list_of_dicts(analysis_input.context.get("files")):
        detected, was_redacted = _detect_file_findings(file_item)
        redacted = redacted or was_redacted
        findings.extend(detected)

    for command_item in _list_of_dicts(analysis_input.context.get("command_results")):
        detected, was_redacted = _detect_command_findings(command_item)
        redacted = redacted or was_redacted
        findings.extend(detected)

    return _dedupe_findings(findings), redacted


def _normalize_candidate(candidate: dict[str, Any], *, source_default: str) -> tuple[ReasonedFinding, bool]:
    category = _clean_identifier(candidate.get("category"), "code_quality")
    severity = normalize_severity(candidate.get("severity"))
    file_path = _clean_path(candidate.get("file_path") or candidate.get("file") or "project")
    line_number = _parse_line(candidate.get("line_number") or candidate.get("line"))
    title, title_redacted = sanitize_text(candidate.get("title") or "Agent reasoned finding")
    description, description_redacted = sanitize_text(candidate.get("description") or candidate.get("details") or title)
    evidence, evidence_redacted = sanitize_text(candidate.get("evidence") or description)
    rule_id = _clean_identifier(candidate.get("rule_id") or candidate.get("rule") or source_default, source_default)
    source = _clean_identifier(candidate.get("source"), source_default)
    signature = stable_finding_signature(
        category=category,
        rule_id=rule_id,
        file_path=file_path,
        line_number=line_number,
        title=title,
        description=description,
    )
    return (
        ReasonedFinding(
            signature=signature,
            category=category,
            severity=severity,
            file_path=file_path,
            line_number=line_number,
            title=title,
            description=description,
            source=source,
            evidence=evidence,
            rule_id=rule_id,
        ),
        title_redacted or description_redacted or evidence_redacted,
    )


def _detect_file_findings(file_item: dict[str, Any]) -> tuple[list[ReasonedFinding], bool]:
    file_path = _clean_path(file_item.get("path") or file_item.get("file_path") or "project")
    content = str(file_item.get("content") or "")
    findings: list[ReasonedFinding] = []
    redacted = False
    for index, line in enumerate(content.splitlines(), start=1):
        if re.search(r"(?i)\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]", line):
            evidence, was_redacted = sanitize_text(line.strip())
            redacted = redacted or was_redacted
            findings.append(
                _make_finding(
                    category="security",
                    severity="high",
                    file_path=file_path,
                    line_number=index,
                    title="Potential hardcoded secret",
                    description="Secret-like assignment detected in repository context.",
                    source="file_analysis",
                    evidence=evidence,
                    rule_id="secret_assignment",
                )
            )
    return findings, redacted


def _detect_command_findings(command_item: dict[str, Any]) -> tuple[list[ReasonedFinding], bool]:
    exit_code = command_item.get("exit_code")
    if exit_code in (0, "0", None):
        return [], False

    command, command_redacted = sanitize_text(command_item.get("command") or "unknown command")
    output = "\n".join(
        str(command_item.get(key) or "")
        for key in ("stdout", "stderr", "output")
        if command_item.get(key)
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        lines = [f"{command} exited {exit_code}"]

    findings: list[ReasonedFinding] = []
    redacted = command_redacted
    for line in lines[:20]:
        evidence, was_redacted = sanitize_text(line)
        redacted = redacted or was_redacted
        file_path, line_number, message = _parse_tool_output_line(evidence)
        findings.append(
            _make_finding(
                category="validation",
                severity="high",
                file_path=file_path,
                line_number=line_number,
                title=f"Validation failed: {command}",
                description=message,
                source="command_output",
                evidence=evidence,
                rule_id="command_exit_nonzero",
            )
        )
    return findings, redacted


def _make_finding(
    *,
    category: str,
    severity: str,
    file_path: str,
    line_number: int | None,
    title: str,
    description: str,
    source: str,
    evidence: str,
    rule_id: str,
) -> ReasonedFinding:
    signature = stable_finding_signature(
        category=category,
        rule_id=rule_id,
        file_path=file_path,
        line_number=line_number,
        title=title,
        description=description,
    )
    return ReasonedFinding(
        signature=signature,
        category=category,
        severity=normalize_severity(severity),
        file_path=file_path,
        line_number=line_number,
        title=title,
        description=description,
        source=source,
        evidence=evidence,
        rule_id=rule_id,
    )


def _build_fix_plan(context: dict[str, Any], findings: list[ReasonedFinding]) -> FixPlan:
    target_files = sorted({finding.file_path for finding in findings if finding.file_path != "project"})
    validation_commands = _validation_commands(context, findings)
    if findings:
        steps = [
            "Review each finding against current repository truth.",
            "Apply the smallest scoped fix for the affected file or command failure.",
            "Run the listed validation commands and update findings/tasks from evidence.",
        ]
        summary = f"Address {len(findings)} normalized finding(s) before autonomous completion."
        risk = "Changes may affect touched files or validation behavior; require approval before writes."
        rollback = "Revert the scoped file edits or restore the previous branch state before rerunning validation."
    else:
        steps = ["No normalized findings were produced from the supplied context."]
        summary = "No actionable findings detected from supplied context."
        risk = "No write risk from analysis-only execution."
        rollback = "No rollback required for analysis-only execution."
    return FixPlan(
        summary=summary,
        steps=steps,
        target_files=target_files,
        validation_commands=validation_commands,
        requires_approval=bool(findings),
        risk=risk,
        rollback=rollback,
    )


def _validation_commands(context: dict[str, Any], findings: list[ReasonedFinding]) -> list[str]:
    configured = context.get("validation_commands")
    commands = [str(command).strip() for command in configured] if isinstance(configured, list) else []
    if not commands:
        commands = [
            str(item.get("command")).strip()
            for item in _list_of_dicts(context.get("command_results"))
            if item.get("command")
        ]
    if not commands and findings:
        commands = ["run the narrowest repo-native validation for affected files"]
    sanitized: list[str] = []
    for command in commands:
        safe, _ = sanitize_text(command)
        if safe and safe not in sanitized:
            sanitized.append(safe)
    return sanitized


def _parse_tool_output_line(line: str) -> tuple[str, int | None, str]:
    match = re.match(r"^(?P<path>[^:\s][^:]*):(?P<line>\d+)(?::\d+)?:\s*(?P<message>.+)$", line)
    if not match:
        return "project", None, line
    return (
        _clean_path(match.group("path")),
        _parse_line(match.group("line")),
        match.group("message"),
    )


def _dedupe_findings(findings: list[ReasonedFinding]) -> list[ReasonedFinding]:
    by_signature: dict[str, ReasonedFinding] = {}
    for finding in findings:
        by_signature.setdefault(finding.signature, finding)
    return sorted(
        by_signature.values(),
        key=lambda finding: (
            finding.file_path,
            finding.line_number or 0,
            finding.category,
            finding.title,
        ),
    )


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _clean_identifier(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", raw) or fallback


def _clean_path(value: Any) -> str:
    raw = str(value or "project").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in PurePosixPath(raw).parts:
        return "project"
    return raw


def _parse_line(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def load_analysis_payload(*, input_json: str | None = None, input_file: str | None = None) -> dict[str, Any]:
    if input_json:
        return json.loads(input_json)
    if input_file:
        with open(input_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    raise ValueError("input_json or input_file is required")
