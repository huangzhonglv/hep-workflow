from __future__ import annotations

from scripts.sync_skill_mirrors import compare_shared_helpers


def test_strict_json_helper_is_identical_in_root_and_skill_installs(repo_root) -> None:
    assert not compare_shared_helpers(repo_root)
