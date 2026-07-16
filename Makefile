PYTHON ?= python3

.PHONY: validate test contract e2e

validate:
	$(PYTHON) scripts/validate_examples.py
	$(PYTHON) scripts/validate_workspace_projects.py
	$(PYTHON) -m pytest -q

test:
	$(PYTHON) -m pytest -q

contract:
	$(PYTHON) -m pytest -q tests/contract

e2e:
	$(PYTHON) -m pytest -q tests/e2e --run-e2e
