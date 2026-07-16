"""Python implementation of the smoke-e2e BR_toy observable."""


def compute_BR_toy(*, M_Hpp: float, v_Delta: float) -> float:
    """Return BR_toy = 1e-4 * (v_Delta / M_Hpp)^2."""
    return 1.0e-4 * (v_Delta / M_Hpp) ** 2
