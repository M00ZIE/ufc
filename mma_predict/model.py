"""
Modelo linear ponderado nas diferenças (vermelho − azul) → probabilidade via logística.
"""

from __future__ import annotations

import math
from typing import Any

from mma_predict.feature_engineering import build_match_components, components_to_public_dict
from ufc_event_analysis import FighterProfile

# Pesos alinhados ao roteiro do usuário (striking, grappling, forma, físico/rank, consistência)
WEIGHTS: dict[str, float] = {
    "striking": 0.3,
    "grappling": 0.3,
    "recent_form": 0.2,
    "physical": 0.1,
    "consistency": 0.1,
}

# Curva logística: edge em [-1,1] típico → probabilidades úteis
LOGIT_SCALE = 3.85


def weighted_edge(components: dict[str, float]) -> float:
    return sum(WEIGHTS[k] * components[k] for k in WEIGHTS)


def edge_to_prob_red(edge: float) -> tuple[float, float]:
    p_red = 1.0 / (1.0 + math.exp(-LOGIT_SCALE * edge))
    return p_red, 1.0 - p_red


def run_weighted_model(
    red: FighterProfile,
    blue: FighterProfile,
    red_rank_card: int | None,
    blue_rank_card: int | None,
) -> dict[str, Any]:
    comp = build_match_components(red, blue, red_rank_card, blue_rank_card)
    edge = weighted_edge(comp)
    p_red, p_blue = edge_to_prob_red(edge)
    return {
        "edge": round(edge, 4),
        "prob_red": p_red,
        "prob_blue": p_blue,
        "components": components_to_public_dict(comp),
        "weights": dict(WEIGHTS),
    }
