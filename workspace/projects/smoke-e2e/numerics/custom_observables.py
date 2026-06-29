"""Placeholder custom_observables for smoke-e2e fixture.

smoke-e2e doesn't need any derived observables; BR_toy is fully
provided by calculations/task-001/result-python.py. This file exists
only to satisfy validate_workspace_projects.py's requirement that
numerics/custom_observables.py contains at least one public function.
"""


def observable_noop(row, scan_config):
    """No-op observable; not bound by any scan-config in this fixture."""
    return None
