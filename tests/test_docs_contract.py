from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_code_sentinel_migration_analysis_is_linked_and_grounded() -> None:
    readme = (ROOT / "README.md").read_text()
    analysis = (ROOT / "docs/local-code-sentinel-migration-analysis.md").read_text()

    assert "docs/local-code-sentinel-migration-analysis.md" in readme
    assert "/home/pika/projekte/code-sentinel" in analysis
    assert "/home/pika/projekte/agent-code-sentinel" in analysis
    assert "Agent-native Runtime" in analysis
    assert "MCP Rolle" in analysis
    assert "Phase M0" in analysis
    assert "Definition of Done" in analysis
