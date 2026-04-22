"""
Volatilidade heurística e classificação SAFE / RISKY / SKIP com Monte Carlo.
"""

from __future__ import annotations

from typing import Any, Literal

from ufc_event_analysis import FighterProfile

RiskTier = Literal["SAFE", "RISKY", "SKIP"]


def finish_rate(fp: FighterProfile) -> float:
    """Taxa de vitórias por finalização (KO/TKO + sub) / vitórias totais."""
    if fp.wins <= 0:
        return 0.0
    fin = fp.ko_wins + fp.sub_wins
    return min(1.0, fin / max(1, fp.wins))


def compute_volatility(
    red: FighterProfile,
    blue: FighterProfile,
    *,
    abs_striking_diff_lpm: float,
) -> float:
    """
    Maior volatilidade quando ambos finalizam muito, há gap de striking e «queixo» exposto.
    """
    fr = (finish_rate(red) + finish_rate(blue)) / 2.0
    ko_r = red.history.last5_ko_losses if red.history else 0
    ko_b = blue.history.last5_ko_losses if blue.history else 0
    chin_penalty = min(6, ko_r + ko_b) / 6.0
    strike_term = abs(float(abs_striking_diff_lpm)) * 0.05
    v = fr + strike_term + chin_penalty * 0.45
    return float(min(10.0, max(0.0, v)))


def favorite_win_probs(p_red: float) -> tuple[float, float]:
    """Probabilidade do favorito (lado com p>0.5; empate em 0.5 assume vermelho)."""
    if p_red > 0.5:
        return p_red, 1.0 - p_red
    if p_red < 0.5:
        return 1.0 - p_red, p_red
    return 0.5, 0.5


def classify_risk_advanced(
    p_fav_model: float,
    p_fav_monte_carlo: float,
    volatility: float,
    *,
    volatility_scale: float = 1.0,
) -> dict[str, Any]:
    """
    SAFE: modelo e MC altos e volatilidade moderada.
    RISKY: confiança média.
    SKIP: resto.

    ``volatility_scale`` (>1 quando o modelo está a degradar) inflaciona só o impacto no tier,
    sem alterar o valor de ``volatility`` devolvido no dict.
    """
    reasons: list[str] = []
    vs = max(0.5, min(2.5, float(volatility_scale)))
    risk_score = min(5.0, float(volatility) * vs)

    if p_fav_model > 0.70 and p_fav_monte_carlo > 0.68 and risk_score < 3.0:
        return {
            "tier": "SAFE",
            "reasons": reasons,
            "max_confidence_pct": round(100.0 * max(p_fav_model, p_fav_monte_carlo), 2),
            "risk_score": round(risk_score, 3),
            "volatility": round(volatility, 4),
        }
    if p_fav_model > 0.55 and p_fav_monte_carlo > 0.52:
        if risk_score >= 3.0:
            reasons.append("Volatilidade elevada — favorito em confronto explosivo")
        if p_fav_model <= 0.70:
            reasons.append("Confiança do modelo abaixo do patamar SAFE")
        return {
            "tier": "RISKY",
            "reasons": reasons or ["Confiança moderada ou volatilidade"],
            "max_confidence_pct": round(100.0 * max(p_fav_model, p_fav_monte_carlo), 2),
            "risk_score": round(risk_score, 3),
            "volatility": round(volatility, 4),
        }
    if p_fav_model <= 0.55:
        reasons.append("Probabilidade do favorito ≤ 55%")
    if p_fav_monte_carlo <= 0.52:
        reasons.append("Monte Carlo não sustenta o favorito")
    if risk_score >= 3.0 and not reasons:
        reasons.append("Risco / volatilidade elevados")
    return {
        "tier": "SKIP",
        "reasons": reasons or ["Critérios SAFE/RISKY não atingidos"],
        "max_confidence_pct": round(100.0 * max(p_fav_model, p_fav_monte_carlo), 2),
        "risk_score": round(risk_score, 3),
        "volatility": round(volatility, 4),
    }
