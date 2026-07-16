from __future__ import annotations


def test_honest_reproduction_contract_names_each_scan_unit_authority(repo_root) -> None:
    text = (repo_root / "docs" / "contracts" / "honest-reproduction-principle.md").read_text(
        encoding="utf-8"
    )

    for token in (
        "model-spec.json parameters[].unit",
        "result-meta.json return_value.unit",
        "scan-config.json source.canonical_unit",
        "comparator never converts scan output",
    ):
        assert token in text
