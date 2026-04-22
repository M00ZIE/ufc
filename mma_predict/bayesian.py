"""
Camada Bayesiana simples: combina prior heurístico com probabilidade secundária (ex. ML)
mediante pesos de confiança (média ponderada normalizada).
"""

from __future__ import annotations

from typing import Optional


def bayesian_posterior_red(
    prior_red: float,
    likelihood_red: Optional[float],
    *,
    w_prior: float,
    w_likelihood: float,
) -> tuple[float, float, float]:
    """
    Devolve (posterior_red, w1_eff, w2_eff).

    Se ``likelihood_red`` for None ou ``w_likelihood`` <= 0, o posterior coincide com o prior.
    """
    p0 = max(1e-6, min(1.0 - 1e-6, float(prior_red)))
    w1 = max(1e-6, float(w_prior))
    w2 = max(0.0, float(w_likelihood))
    if likelihood_red is None or w2 <= 0:
        return p0, w1, 0.0
    p1 = max(1e-6, min(1.0 - 1e-6, float(likelihood_red)))
    den = w1 + w2
    post = (p0 * w1 + p1 * w2) / den
    post = max(1e-6, min(1.0 - 1e-6, post))
    return post, w1, w2
