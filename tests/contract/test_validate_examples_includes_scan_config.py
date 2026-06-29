from __future__ import annotations

import subprocess
import sys


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
