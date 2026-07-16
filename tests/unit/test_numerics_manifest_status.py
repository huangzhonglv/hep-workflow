from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_HELPER = (
    REPO_ROOT / ".agents" / "skills" / "hep-numerics" / "scripts" / "_manifest.py"
)


def load_manifest_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hep_numerics_manifest_helper", MANIFEST_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def figure_paths(tmp_path: Path, *, empty: str | None = None) -> list[Path]:
    paths = [
        tmp_path / "exclusion-x-y.pdf",
        tmp_path / "exclusion-x-y.png",
    ]
    for path in paths:
        path.write_bytes(b"" if path.suffix == empty else b"figure")
    return paths


def scan_config(constraints: list[str]) -> dict[str, object]:
    return {
        "constraints_used": constraints,
        "figures": [
            {
                "kind": "exclusion_2d",
                "x": "x",
                "y": "y",
                "constraints": constraints,
            }
        ],
    }


def test_done_requires_only_selected_constraints_to_be_usable(tmp_path: Path) -> None:
    helper = load_manifest_helper()
    constraints = {
        "c-001": {"implementation_status": "direct"},
        "c-002": {"implementation_status": "interpolated"},
    }

    status = helper.determine_numerics_status(
        constraints,
        scan_config(["c-001"]),
        figure_paths=figure_paths(tmp_path),
    )

    assert status == "done"


def test_selected_missing_or_non_executable_constraint_stays_partial(tmp_path: Path) -> None:
    helper = load_manifest_helper()
    constraints = {
        "c-001": {"implementation_status": "direct"},
        "c-002": {"implementation_status": "manual"},
    }
    paths = figure_paths(tmp_path)

    assert (
        helper.determine_numerics_status(
            constraints,
            scan_config(["c-002"]),
            figure_paths=paths,
        )
        == "partial"
    )
    assert (
        helper.determine_numerics_status(
            constraints,
            scan_config(["c-999"]),
            figure_paths=paths,
        )
        == "partial"
    )


def test_empty_constraint_selection_stays_partial(tmp_path: Path) -> None:
    helper = load_manifest_helper()

    assert (
        helper.determine_numerics_status(
            {"c-001": {"implementation_status": "direct"}},
            scan_config([]),
            figure_paths=figure_paths(tmp_path),
        )
        == "partial"
    )


def test_missing_or_empty_figure_stays_partial(tmp_path: Path) -> None:
    helper = load_manifest_helper()
    config = scan_config(["c-001"])
    constraints = {"c-001": {"implementation_status": "direct"}}
    assert (
        helper.determine_numerics_status(
            constraints,
            config,
            figure_paths=[],
        )
        == "partial"
    )
    assert (
        helper.determine_numerics_status(
            constraints,
            config,
            figure_paths=figure_paths(tmp_path, empty=".png"),
        )
        == "partial"
    )
