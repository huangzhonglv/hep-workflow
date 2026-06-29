#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 scripts/validate_examples.py
python3 scripts/validate_workspace_projects.py
python3 -m pytest tests/unit tests/contract -x
python3 -m pytest tests/integration -x
