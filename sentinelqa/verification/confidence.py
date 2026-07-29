def confidence_score(
    *,
    reproduction_rate: float,
    deterministic_signal_strength: float,
    invariant_strength: float,
    cross_layer_confirmation: float,
    clean_environment_confirmation: float,
    cross_browser_confirmation: float,
    environment_instability: float,
) -> float:
    value = (
        0.25 * reproduction_rate
        + 0.20 * deterministic_signal_strength
        + 0.20 * invariant_strength
        + 0.15 * cross_layer_confirmation
        + 0.10 * clean_environment_confirmation
        + 0.10 * cross_browser_confirmation
        - 0.25 * environment_instability
    )
    return min(1.0, max(0.0, value))
