from __future__ import annotations

import subprocess
import sys

from scripts.validate_examples import SCHEMA_TO_EXAMPLE


def test_validate_examples_map_is_complete_and_one_to_one(repo_root) -> None:
    schemas_dir = repo_root / "schemas"
    examples_dir = schemas_dir / "examples"

    schema_names = {path.name for path in schemas_dir.glob("*.schema.json")}
    example_names = {path.name for path in examples_dir.glob("*.example.json")}

    assert set(SCHEMA_TO_EXAMPLE) == schema_names
    assert set(SCHEMA_TO_EXAMPLE.values()) == example_names
    assert len(SCHEMA_TO_EXAMPLE.values()) == len(set(SCHEMA_TO_EXAMPLE.values()))


def test_contributing_points_to_pair_map_instead_of_enumerating_schemas(
    repo_root,
) -> None:
    text = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    paragraph = text.split(
        "`scripts/validate_examples.py` validates",
        1,
    )[1].split("\n\n", 1)[0]

    assert "`SCHEMA_TO_EXAMPLE`" in paragraph
    assert "`schemas/*.schema.json`" in paragraph
    assert "`schemas/examples/*.example.json`" in paragraph
    for schema_name in SCHEMA_TO_EXAMPLE:
        assert f"`{schema_name}`" not in paragraph


def test_validate_examples_includes_scan_config_and_scan_meta(repo_root) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_examples.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "scan-config.schema.json <- scan-config.example.json" in result.stdout
    assert "scan-meta.schema.json <- scan-meta.example.json" in result.stdout
