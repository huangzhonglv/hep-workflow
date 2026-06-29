from __future__ import annotations


def collect_files(root):
    return sorted(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
    )


def test_hep_paper_formalize_skill_tree_matches_agents(repo_root) -> None:
    claude_dir = repo_root / ".claude" / "skills" / "hep-paper-formalize"
    agents_dir = repo_root / ".agents" / "skills" / "hep-paper-formalize"

    claude_files = collect_files(claude_dir)
    agents_files = collect_files(agents_dir)

    assert claude_files == agents_files

    for relative_path in claude_files:
        assert (claude_dir / relative_path).read_bytes() == (
            agents_dir / relative_path
        ).read_bytes()
