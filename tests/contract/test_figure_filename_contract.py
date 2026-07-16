from __future__ import annotations

from pathlib import Path


def test_scan1d_reference_and_renderer_share_one_canonical_basename(
    repo_root: Path,
) -> None:
    for mirror_root in (".claude", ".agents"):
        skill_root = repo_root / mirror_root / "skills" / "hep-numerics"
        reference = (skill_root / "references" / "figure-styles.md").read_text(
            encoding="utf-8"
        )
        renderer = (skill_root / "scripts" / "make_figures.py").read_text(
            encoding="utf-8"
        )

        assert "scan1d-{x}-{observable}.pdf" in reference
        assert "scan1d-{x}-{observable}.png" in reference
        assert "scan-{x}-{observable}.pdf" not in reference
        assert "scan-{x}-{observable}.png" not in reference
        assert "sole canonical prefix" in reference
        assert renderer.count("figure_output_key(figure_spec)") == 2
        assert 'f"scan1d-' not in renderer

    contributing = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "only canonical one-dimensional figure prefix is `scan1d-`" in contributing
    assert "must not emit or accept a second `scan-` alias" in contributing
