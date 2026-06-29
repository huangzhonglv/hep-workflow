from __future__ import annotations


def test_hep_paper_formalize_forbidden_outputs_are_listed(repo_root) -> None:
    skill_paths = [
        repo_root / ".claude" / "skills" / "hep-paper-formalize" / "SKILL.md",
        repo_root / ".agents" / "skills" / "hep-paper-formalize" / "SKILL.md",
    ]

    for skill_path in skill_paths:
        text = skill_path.read_text(encoding="utf-8")
        assert "Forbidden" in text
        assert "result-python.py" in text
        assert "result.wl" in text
        assert "numerics/scan-configs/" in text
        assert "reproduction/runs/" in text
        assert "docs/contracts/honest-reproduction-principle.md" in text
