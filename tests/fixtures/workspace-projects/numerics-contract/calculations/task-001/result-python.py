from __future__ import annotations


def compute_br_mu_to_egamma(*, M_Hpp: float, v_Delta: float = 1.0e-3, **kwargs) -> float:
    safe_mass = max(float(M_Hpp), 1.0)
    return float(1.0e-13 * (100.0 / safe_mass) ** 2 * (1.0 + 100.0 * float(v_Delta)))
