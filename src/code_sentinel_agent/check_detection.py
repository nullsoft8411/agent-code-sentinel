from __future__ import annotations

from pathlib import Path


def detect_checks(root: str | Path) -> dict:
    path = Path(root).resolve()
    signals: list[str] = []
    recommended_checks: list[str] = []

    def exists(*parts: str) -> bool:
        return (path / Path(*parts)).exists()

    if exists("package.json"):
        signals.append("node_root")
        recommended_checks.extend(["npm test", "npm run lint", "npm run build"])
    if exists("pnpm-lock.yaml"):
        signals.append("pnpm")
        recommended_checks.extend(["pnpm test", "pnpm lint", "pnpm build"])
    if exists("yarn.lock"):
        signals.append("yarn")
        recommended_checks.extend(["yarn test", "yarn lint", "yarn build"])
    if exists("pyproject.toml") or exists("requirements.txt"):
        signals.append("python")
        recommended_checks.extend(["pytest", "ruff check .", "mypy ."])
    if exists("go.mod"):
        signals.append("go")
        recommended_checks.extend(["go test ./...", "go vet ./..."])
    if exists("Cargo.toml"):
        signals.append("rust")
        recommended_checks.extend(["cargo test", "cargo clippy --all-targets --all-features"])
    if exists(".github", "workflows"):
        signals.append("github_actions")
    if exists("docker-compose.yml") or exists("docker", "docker-compose.yml"):
        signals.append("docker")
    if exists("AGENTS.md"):
        signals.append("agents_md")

    return {
        "status": "passed",
        "path": str(path),
        "signals": signals,
        "recommended_checks": dedupe(recommended_checks),
    }


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

