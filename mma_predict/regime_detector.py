"""
Deteção de regime do card / confronto (volatilidade, finalizações, dispersão entre modelos).
"""

from __future__ import annotations

import math
from typing import Optional

from ufc_event_analysis import FighterProfile

from mma_predict.risk_volatility import finish_rate


def finish_blend(red: FighterProfile, blue: FighterProfile) -> float:
    """Média das taxas de vitória por finalização (0–1)."""
    return (finish_rate(red) + finish_rate(blue)) / 2.0


def disagreement_index(
    p_heuristic: float,
    p_bayesian: float,
    p_elo: float,
    p_ml: Optional[float],
    *,
    has_ml: bool,
) -> float:
    """0–1: maior quando os modelos divergem mais sobre P(red)."""
    parts = [float(p_heuristic), float(p_bayesian), float(p_elo)]
    if has_ml and p_ml is not None:
        parts.append(float(p_ml))
    m = sum(parts) / len(parts)
    var = sum((x - m) ** 2 for x in parts) / len(parts)
    spread = math.sqrt(max(0.0, var))
    return float(min(1.0, spread / 0.32))


def detect_fight_regime(
    volatility: float,
    finish_bl: float,
    disagreement_idx: float,
    division: str,
    *,
    card_avg_volatility: Optional[float] = None,
) -> str:
    """
    Regimes: ``stable_card``, ``chaotic_card``, ``grappling_heavy``, ``striking_heavy``.
    Usa sinais locais e, se fornecido, a volatilidade média do evento.
    """
    vol = float(volatility)
    div = (division or "").lower()
    hw = any(x in div for x in ("heavy", "pesado", "265", "206"))

    card_vol = float(card_avg_volatility) if card_avg_volatility is not None else vol
    vol_chaos = vol > max(3.4, card_vol * 1.12) and disagreement_idx > 0.34
    if hw and vol > 3.8:
        vol_chaos = True
    if vol_chaos:
        return "chaotic_card"

    if finish_bl > 0.52:
        return "grappling_heavy"
    if finish_bl < 0.32:
        return "striking_heavy"
    return "stable_card"


REGIME_WEIGHT_MULT: dict[str, dict[str, float]] = {
    "stable_card": {"heuristic": 1.0, "bayesian": 1.0, "elo": 1.0, "ml": 1.0},
    "chaotic_card": {"heuristic": 0.88, "bayesian": 1.12, "elo": 1.02, "ml": 0.92},
    "grappling_heavy": {"heuristic": 0.95, "bayesian": 1.06, "elo": 1.05, "ml": 0.98},
    "striking_heavy": {"heuristic": 1.05, "bayesian": 0.98, "elo": 0.97, "ml": 1.0},
}


def apply_regime_weight_multipliers(
    weights: dict[str, float],
    regime: str,
) -> dict[str, float]:
    mult = REGIME_WEIGHT_MULT.get(regime, REGIME_WEIGHT_MULT["stable_card"])
    out = {k: max(1e-6, float(weights.get(k, 0.0)) * float(mult.get(k, 1.0))) for k in ("heuristic", "bayesian", "elo", "ml")}
    s = sum(out.values())
    if s <= 0:
        return dict(weights)
    return {k: float(v) / s for k, v in out.items()}


def regime_mc_factor(regime: str, disagreement_idx: float) -> float:
    """Fator ≥1 para alargar MC em regimes mais instáveis ou com grande divergência."""
    base = {
        "stable_card": 1.0,
        "striking_heavy": 1.05,
        "grappling_heavy": 1.08,
        "chaotic_card": 1.22,
    }.get(regime, 1.06)
    return float(base * (1.0 + 0.35 * max(0.0, min(1.0, disagreement_idx))))
