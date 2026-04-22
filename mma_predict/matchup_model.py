"""
Modelo de confronto: combinação linear de diferenças (vermelho − azul) + logística.

Substitui o score linear antigo (cinco componentes normalizados) como fonte principal
de P(vermelho), mantendo os componentes legados só para visualização comparativa.
"""

from __future__ import annotations

import math
from typing import Any

from ufc_event_analysis import FighterProfile

# Pesos pedidos (striking / grappling / def. quedas / cardio)
W_STRIKE = 0.35
W_GRAP = 0.35
W_TDD = 0.20
W_CARDIO = 0.10

# Escala da logística sobre o score linear (ajuste fino em calibração futura)
LOGIT_K = 1.25

DEFAULT_MATCHUP_WEIGHTS: tuple[float, float, float, float] = (W_STRIKE, W_GRAP, W_TDD, W_CARDIO)


def _f(x: float | None, default: float) -> float:
    return float(x) if x is not None else default


def raw_matchup_differentials(red: FighterProfile, blue: FighterProfile) -> dict[str, float]:
    """Diferenças brutas orientadas a vermelho − azul (mesmas unidades do site UFC)."""
    striking_diff = _f(red.sig_str_lpm, 4.0) - _f(blue.sig_str_lpm, 4.0)
    td_r, td_b = _f(red.td_avg, 0.0), _f(blue.td_avg, 0.0)
    su_r, su_b = _f(red.sub_per_15, 0.0), _f(blue.sub_per_15, 0.0)
    grappling_diff = (td_r - td_b) + 0.55 * (su_r - su_b)
    td_def_diff = _f(red.td_def_pct, 50.0) - _f(blue.td_def_pct, 50.0)
    cardio_diff = _f(red.avg_fight_minutes, 12.0) - _f(blue.avg_fight_minutes, 12.0)
    return {
        "striking_diff": striking_diff,
        "grappling_diff": grappling_diff,
        "td_def_diff": td_def_diff,
        "cardio_diff": cardio_diff,
    }


def matchup_feature_terms_vector(d: dict[str, float]) -> tuple[float, float, float, float]:
    """Termos normalizados (vermelho − azul) antes de aplicar pesos — usados no aprendizado online."""
    s = d["striking_diff"] / 2.8
    g = d["grappling_diff"] / 2.2
    tdd_term = d["td_def_diff"] / 100.0
    card = d["cardio_diff"] / 6.0
    return (s, g, tdd_term, card)


def matchup_linear_score(
    d: dict[str, float],
    weights: tuple[float, float, float, float] | None = None,
) -> float:
    """
    Score contínuo; termos escalados antes da logística.
    ``weights`` opcional (ex.: pesos adaptativos); omissão = constantes do módulo.
    """
    w = weights if weights is not None else DEFAULT_MATCHUP_WEIGHTS
    t = matchup_feature_terms_vector(d)
    return w[0] * t[0] + w[1] * t[1] + w[2] * t[2] + w[3] * t[3]


def score_to_prob_red(score: float) -> tuple[float, float]:
    p_red = 1.0 / (1.0 + math.exp(-LOGIT_K * score))
    return p_red, 1.0 - p_red


def run_matchup_probabilities(
    red: FighterProfile,
    blue: FighterProfile,
    *,
    weights: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    raw = raw_matchup_differentials(red, blue)
    w = weights if weights is not None else DEFAULT_MATCHUP_WEIGHTS
    score = matchup_linear_score(raw, weights=w)
    p_red, p_blue = score_to_prob_red(score)
    tvec = matchup_feature_terms_vector(raw)
    return {
        "matchup_linear_score": round(score, 5),
        "prob_red": p_red,
        "prob_blue": p_blue,
        "raw_differentials": {k: round(v, 4) for k, v in raw.items()},
        "feature_terms_vector": [float(x) for x in tvec],
        "weights_used": [float(x) for x in w],
    }
