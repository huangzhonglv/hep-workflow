from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _schema(repo_root: Path) -> dict[str, Any]:
    return json.loads(
        (repo_root / "schemas" / "calc-tasks.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _minimal_task(external_particles: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": "Synthetic model",
        "model_version": "v1",
        "tasks": [
            {
                "task_id": "task-001",
                "title": "Synthetic task",
                "type": "loop",
                "loop_order": 1,
                "process": "mu -> mu",
                "lagrangian_terms": ["L_int = g X_mu bar{mu} gamma^mu mu"],
                "external_particles": external_particles,
                "loop_particles": [{"propagator": "mu", "mass": "m_mu"}],
                "target_quantity": "delta_a_mu",
                "on_shell": True,
                "priority": "high",
                "notes": "Synthetic schema fixture.",
            }
        ],
    }


def test_external_particles_accept_structured_legs(repo_root: Path) -> None:
    payload = _minimal_task(
        {
            "incoming": [{"particle": "mu", "momentum": "p1"}],
            "outgoing": [{"particle": "mu", "momentum": "p2"}],
            "virtual_boson": {"particle": "gamma", "momentum": "q"},
        }
    )

    Draft202012Validator(_schema(repo_root)).validate(payload)


def test_external_particles_reject_embedded_momentum_strings(
    repo_root: Path,
) -> None:
    payload = _minimal_task(
        {
            "incoming": ["mu(p1)"],
            "outgoing": ["mu(p2)"],
            "virtual_boson": "gamma(q)",
        }
    )

    validator = Draft202012Validator(_schema(repo_root))
    errors = list(validator.iter_errors(payload))

    assert errors


def test_external_particles_reject_noncanonical_particle_names(
    repo_root: Path,
) -> None:
    payload = _minimal_task(
        {
            "incoming": [{"particle": "mu-", "momentum": "p1"}],
            "outgoing": [{"particle": "mu_plus", "momentum": "p2"}],
        }
    )

    validator = Draft202012Validator(_schema(repo_root))
    errors = list(validator.iter_errors(payload))

    assert errors
