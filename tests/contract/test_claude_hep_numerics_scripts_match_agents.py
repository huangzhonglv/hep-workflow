from __future__ import annotations


def test_claude_hep_numerics_scripts_match_agents(repo_root) -> None:
    agents_dir = repo_root / ".agents" / "skills" / "hep-numerics" / "scripts"
    claude_dir = repo_root / ".claude" / "skills" / "hep-numerics" / "scripts"

    agents_files = sorted(path.name for path in agents_dir.glob("*.py"))
    claude_files = sorted(path.name for path in claude_dir.glob("*.py"))

    assert claude_files == agents_files

    for filename in agents_files:
        assert (claude_dir / filename).read_text(encoding="utf-8") == (
            agents_dir / filename
        ).read_text(encoding="utf-8")
