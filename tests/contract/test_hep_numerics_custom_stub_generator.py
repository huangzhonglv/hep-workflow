from __future__ import annotations

from pathlib import Path


def test_hep_numerics_entry_points_share_one_stub_generator(repo_root: Path) -> None:
    scripts_dir = repo_root / ".claude" / "skills" / "hep-numerics" / "scripts"
    helper = (scripts_dir / "_custom_observables.py").read_text(encoding="utf-8")
    init_analysis = (scripts_dir / "init_analysis.py").read_text(encoding="utf-8")
    run_scan = (scripts_dir / "run_scan.py").read_text(encoding="utf-8")

    definition = "def append_custom_observable_stub("
    delegation = "CUSTOM_OBSERVABLES.append_custom_observable_stub("

    assert helper.count(definition) == 1
    assert definition not in init_analysis
    assert definition not in run_scan
    assert delegation in init_analysis
    assert delegation not in run_scan
    assert "CUSTOM_OBSERVABLES = RUN_SCAN.CUSTOM_OBSERVABLES" in init_analysis
    assert 'helper_path = Path(__file__).resolve().parent / "_custom_observables.py"' in run_scan
