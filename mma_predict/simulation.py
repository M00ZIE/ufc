"""
Monte Carlo sobre probabilidades base e simulação de parlays (independência entre pernas).
"""

from __future__ import annotations

import math
import random
from typing import Any


def monte_carlo_prob(
    base_prob: float,
    *,
    simulations: int = 5000,
    noise: float = 0.07,
    noise_scale: float = 1.0,
    uncertainty_multiplier: float = 1.0,
    policy_risk_factor: float = 1.0,
    adversarial_factor: float = 1.0,
    seed: int | None = None,
) -> float:
    """
    Perturba a probabilidade base com ruído uniforme ±noise·noise_scale·uncertainty_multiplier.

    ``noise_scale``: erro histórico / aprendizagem online.
    ``uncertainty_multiplier``: dispersão entre modelos, drift, mismatch Elo, volatilidade (fase 3).
    ``policy_risk_factor`` / ``adversarial_factor``: fases 5–6 (política + stress adversarial).
    """
    rng = random.Random(seed)
    p0 = max(0.02, min(0.98, float(base_prob)))
    um = max(0.65, min(2.2, float(uncertainty_multiplier)))
    pr = max(0.72, min(1.95, float(policy_risk_factor)))
    adv = max(0.72, min(1.9, float(adversarial_factor)))
    eff_noise = float(noise) * max(0.5, min(2.5, float(noise_scale))) * um * pr * adv
    wins = 0
    n = max(100, int(simulations))
    for _ in range(n):
        p = p0 + rng.uniform(-eff_noise, eff_noise)
        p = max(0.02, min(0.98, p))
        if rng.random() < p:
            wins += 1
    return wins / n


def simulate_parlay(
    probabilities: list[float],
    *,
    simulations: int = 10000,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    Assume independência entre pernas. Em cada simulação, todas as pernas têm de acertar.
    """
    rng = random.Random(seed)
    probs = [max(0.01, min(0.99, float(p))) for p in probabilities if p is not None]
    if not probs:
        return {
            "hit_rate": 0.0,
            "simulations": 0,
            "combined_probability_naive": None,
            "n_legs": 0,
        }
    naive = math.prod(probs)
    n = max(200, int(simulations))
    hits = 0
    for _ in range(n):
        ok = True
        for p in probs:
            if rng.random() >= p:
                ok = False
                break
        if ok:
            hits += 1
    return {
        "hit_rate": hits / n,
        "simulations": n,
        "combined_probability_naive": naive,
        "n_legs": len(probs),
    }


def monte_carlo_portfolio_card(
    probs_red: list[float],
    *,
    stake_fraction_per_fight: float,
    decimal_odds: float = 1.88,
    bet_red: list[bool] | None = None,
    correlation: float = 0.22,
    simulations: int = 3500,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    Fase 7: evolução de bankroll num card com pernas correlacionadas (delega em ``bankroll``).
    """
    from mma_predict.bankroll import monte_carlo_portfolio_correlated

    return monte_carlo_portfolio_correlated(
        probs_red,
        stake_fraction_per_fight=stake_fraction_per_fight,
        decimal_odds=decimal_odds,
        bet_red=bet_red,
        correlation=correlation,
        simulations=simulations,
        seed=seed,
    )
