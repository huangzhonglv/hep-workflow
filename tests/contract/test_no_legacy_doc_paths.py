"""Legacy doc paths before the PR-1 refactor must not appear in project files."""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_NAME_CONVENTION_LEGACY_PATH = "/".join(
    ("docs", "canonical-name-" + "convention.md")
)
LEGACY_PATHS = [
    CANONICAL_NAME_CONVENTION_LEGACY_PATH,
]
ALLOWED_FILES_PER_PATH = {
    CANONICAL_NAME_CONVENTION_LEGACY_PATH: set(),
}

SCAN_SUFFIXES = (".md", ".py", ".toml", ".json")


def _tracked_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        REPO_ROOT / rel
        for rel in result.stdout.splitlines()
        if rel.endswith(SCAN_SUFFIXES)
    ]


def test_no_legacy_doc_paths():
    violations = []
    for legacy in LEGACY_PATHS:
        allowed = ALLOWED_FILES_PER_PATH.get(legacy, set())
        for path in _tracked_text_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if legacy in text:
                violations.append(f"{rel}: contains legacy path '{legacy}'")
    assert not violations, "\n".join(violations)
