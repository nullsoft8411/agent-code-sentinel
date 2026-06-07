from __future__ import annotations

import hashlib
import re
from typing import Any

OUTPUT_CONTRACT_VERSION = "agent-analysis.v1"

_SEVERITY_ALIASES = {
    "blocker": "critical",
    "blocking": "critical",
    "critical": "critical",
    "error": "high",
    "high": "high",
    "security": "high",
    "warn": "medium",
    "warning": "medium",
    "medium": "medium",
    "advisory": "low",
    "info": "low",
    "low": "low",
}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{6,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]\s*([\"'])[^\"']+\2"
    ),
    re.compile(r"(?i)\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]\s*[^\s,;}]+"),
]


def normalize_severity(value: Any) -> str:
    if value is None:
        return "medium"
    text = str(value).strip().lower().replace(" ", "_")
    return _SEVERITY_ALIASES.get(text, "medium")


def sanitize_text(value: Any) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    redacted = False
    for pattern in _SECRET_PATTERNS:
        updated = pattern.sub(_redact_secret_match, text)
        if updated != text:
            redacted = True
            text = updated
    return text, redacted


def stable_finding_signature(
    *,
    category: str,
    rule_id: str,
    file_path: str,
    line_number: int | None,
    title: str,
    description: str,
) -> str:
    raw = "|".join(
        [
            category.strip().lower(),
            rule_id.strip().lower(),
            file_path.strip(),
            "" if line_number is None else str(line_number),
            _collapse_ws(title).lower(),
            _collapse_ws(description).lower(),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"finding:{digest}"


def finding_contract_fields() -> list[str]:
    return [
        "signature",
        "category",
        "severity",
        "file_path",
        "line_number",
        "title",
        "description",
        "source",
        "evidence",
        "rule_id",
        "status",
    ]


def _redact_secret_match(match: re.Match[str]) -> str:
    text = match.group(0)
    if text.startswith("sk-"):
        return "sk-[REDACTED]"
    if "=" in text:
        name = text.split("=", 1)[0].strip()
        return f"{name}=[REDACTED]"
    if ":" in text:
        name = text.split(":", 1)[0].strip()
        return f"{name}: [REDACTED]"
    return "[REDACTED]"


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())
