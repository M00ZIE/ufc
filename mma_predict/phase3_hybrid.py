"""
Ensemble fase 3: heurística + ML opcional + Bayesiano + Elo; acordo entre modelos e incerteza.
"""

from __future__ import annotations

import math
from typing import Any, Optional


# Pesos pedidos; sem ML o peso de ML reparte-se de forma interpretável.
W_HEURISTIC_FULL = 0.35
W_ML_FULL = 0.25
W_BAYES_FULL = 0.20
W_ELO_FULL = 0.20


def _renorm_without_ml() -> tuple[float, float, float]:
    s = W_HEURISTIC_FULL + W_BAYES_FULL + W_ELO_FULL
    return W_HEURISTIC_FULL / s, W_BAYES_FULL / s, W_ELO_FULL / s


def ensemble_final_prob_red(
    p_heuristic: float,
    p_ml: Optional[float],
    p_bayesian: float,
    p_elo: float,
    *,
    has_ml: bool,
) -> float:
    if has_ml and p_ml is not None:
        p = (
            W_HEURISTIC_FULL * p_heuristic
            + W_ML_FULL * float(p_ml)
            + W_BAYES_FULL * p_bayesian
            + W_ELO_FULL * p_elo
        )
    else:
        wh, wb, we = _renorm_without_ml()
        p = wh * p_heuristic + wb * p_bayesian + we * p_elo
    return float(max(1e-6, min(1.0 - 1e-6, p)))


def model_agreement_score(
    p_heuristic: float,
    p_ml: Optional[float],
    p_bayesian: float,
    p_elo: float,
    *,
    has_ml: bool,
) -> float:
    """
    1 − dispersão normalizada das probabilidades de vitória do vermelho.
    """
    parts = [float(p_heuristic), float(p_bayesian), float(p_elo)]
    if has_ml and p_ml is not None:
        parts.append(float(p_ml))
    m = sum(parts) / len(parts)
    var = sum((x - m) ** 2 for x in parts) / len(parts)
    spread = math.sqrt(max(0.0, var))
    # spread 0 → agreement 1; ~0.25 (modelos muito díspares) → ~0
    return float(max(0.0, min(1.0, 1.0 - spread / 0.28)))


def uncertainty_index(
    p_heuristic: float,
    p_ml: Optional[float],
    p_bayesian: float,
    p_elo: float,
    *,
    has_ml: bool,
    drift_score: float,
    elo_diff: float,
) -> float:
    """
    Incerteza 0–1: dispersão entre modelos + drift + desalinhamento Elo (mismatch).
    """
    parts = [float(p_heuristic), float(p_bayesian), float(p_elo)]
    if has_ml and p_ml is not None:
        parts.append(float(p_ml))
    m = sum(parts) / len(parts)
    var = sum((x - m) ** 2 for x in parts) / len(parts)
    spread = math.sqrt(max(0.0, var))
    u_models = min(1.0, spread / 0.32)
    u_drift = max(0.0, min(1.0, float(drift_score)))
    u_elo = min(1.0, abs(float(elo_diff)) / 520.0)
    u = 0.52 * u_models + 0.28 * u_drift + 0.20 * u_elo
    return float(max(0.0, min(1.0, u)))


def mc_uncertainty_multiplier(
    uncertainty: float,
    elo_diff: float,
    volatility: float,
) -> float:
    """Combina incerteza global, mismatch Elo e volatilidade da luta para alargar o MC."""
    u = max(0.0, min(1.0, float(uncertainty)))
    ed = min(1.0, abs(float(elo_diff)) / 450.0)
    vol = max(0.0, min(1.0, float(volatility) / 10.0))
    return float(1.0 + 0.55 * u + 0.38 * ed + 0.15 * vol)


def value_score_v3(
    edge_abs: float,
    confidence: float,
    agreement: float,
) -> float:
    return float(max(0.0, edge_abs) * max(0.0, confidence) * max(0.0, min(1.0, agreement)))


def value_bet_v3_decision(
    *,
    final_prob_red: float,
    implied_red: float,
    implied_blue: float,
    agreement: float,
    confidence: float,
    value_score_threshold: float,
) -> tuple[bool, Optional[dict[str, Any]]]:
    """
    VALUE BET 3.0: edge sobre ``final_prob``; exige confiança > 0.6 e value_score acima do limiar.
    """
    conf = max(float(final_prob_red), 1.0 - float(final_prob_red))
    if conf <= 0.6:
        return False, None
    er = float(final_prob_red) - float(implied_red)
    eb = (1.0 - float(final_prob_red)) - float(implied_blue)
    if er >= eb and er > 0:
        side = "red"
        edge = er
        implied = implied_red
        p_side = float(final_prob_red)
    elif eb > 0:
        side = "blue"
        edge = eb
        implied = implied_blue
        p_side = 1.0 - float(final_prob_red)
    else:
        return False, None
    vs = value_score_v3(edge, conf, agreement)
    if vs <= value_score_threshold:
        return False, None
    return True, {
        "side": side,
        "model_pct": round(100.0 * p_side, 2),
        "implied_pct": round(100.0 * implied, 2),
        "edge_prob": round(edge, 4),
        "value_score": round(vs, 5),
        "confidence": round(conf, 4),
        "model_agreement": round(agreement, 4),
        "note": "Value 3.0: ensemble final_prob × confiança × acordo entre modelos.",
    }
