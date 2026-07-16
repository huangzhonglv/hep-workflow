from __future__ import annotations

import json
from pathlib import Path


SKILL_TREES = (".claude", ".agents")


def test_result_meta_template_requires_provenance_finalization(
    repo_root: Path,
) -> None:
    for skill_tree in SKILL_TREES:
        template_path = (
            repo_root
            / skill_tree
            / "skills"
            / "package-scribe"
            / "templates"
            / "result-meta.json.tmpl"
        )
        template = json.loads(template_path.read_text(encoding="utf-8"))

        assert template["input_provenance"] == {
            "version": "sha256-bytes-v1",
            "verification_status": "{{input_provenance_status}}",
        }
        assert "legacy-unverified" not in template_path.read_text(encoding="utf-8")


def test_package_scribe_builds_and_reverifies_exact_dependency_set(
    repo_root: Path,
) -> None:
    required_fragments = (
        "calculation_dependency_specs",
        "build_dependency_graph",
        "verify_dependency_graph",
        "expected_specs=calculation_dependency_specs(",
        "allow_legacy=False",
        "check_package_result_placeholders.py",
        'input_provenance.verification_status = "verified"',
        "package-scribe must never emit it for a new or recomputed",
    )

    for skill_tree in SKILL_TREES:
        skill_path = (
            repo_root / skill_tree / "skills" / "package-scribe" / "SKILL.md"
        )
        skill = skill_path.read_text(encoding="utf-8")

        for fragment in required_fragments:
            assert fragment in skill, f"{skill_path} is missing {fragment!r}"

        assert skill.index("#### Step 4.7.4") < skill.index("#### Step 4.7 Self-Check")
