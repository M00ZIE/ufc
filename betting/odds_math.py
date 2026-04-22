"""
Odds decimais a partir de probabilidades do modelo (mma_predict / legado).

- Margem da casa (vig): odds efetivas = (odd justa) / vig, onde odd justa = 1/p.
- Risco SKIP: mercado fechado para apostas.
- RISKY: vig maior (payout menor para o apostador).
"""

from __future__ import annotations

from typing import Any, Optional

# Probabilidade mínima para evitar divisão por zero em underdogs extremos
PROB_FLOOR = 0.04
MAX_DECIMAL_ODDS = 50.0

# Margem total tipo “overround” aplicada sobre a odd justa (1/p)
VIG_SAFE = 1.08  # ~8% sobre o retorno implícito
VIG_RISKY = 1.12  # confrontos incertos / modelo em alerta
VIG_MIN = 1.06
VIG_MAX = 1.10  # referência documental; SAFE usa 1.08 por padrão


def normalize_two_way(p_red: float, p_blue: float) -> tuple[float, float]:
    r = max(0.0, float(p_red))
    b = max(0.0, float(p_blue))
    s = r + b
    if s <= 0:
        return 0.5, 0.5
    return r / s, b / s


def vig_multiplier(risk_tier: Optional[str]) -> Optional[float]:
    """
    Retorna fator vig ou None se apostas bloqueadas (SKIP).
    """
    if risk_tier == "SKIP":
        return None
    if risk_tier == "RISKY":
        return VIG_RISKY
    return VIG_SAFE


def decimal_odds_fair_divide_vig(fair_prob: float, vig: float) -> float:
    """
    Odd justa ≈ 1/p; com margem: odd = (1/p) / vig (pior para o apostador).
    Garante decimal >= 1.01.
    """
    p = max(PROB_FLOOR, min(1.0 - PROB_FLOOR, float(fair_prob)))
    fair_decimal = 1.0 / p
    d = fair_decimal / vig
    return round(max(1.01, min(MAX_DECIMAL_ODDS, d)), 2)


def odds_pair_from_probs_vig(
    p_red: float,
    p_blue: float,
    risk_tier: Optional[str] = None,
) -> dict[str, Any]:
    """
    Retorna probabilidades normalizadas, odds com vig, e se o mercado está aberto.
    """
    pr, pb = normalize_two_way(p_red, p_blue)
    vig = vig_multiplier(risk_tier)
    if vig is None:
        return {
            "betting_blocked": True,
            "prob_red": round(pr, 4),
            "prob_blue": round(pb, 4),
            "decimal_odds_red": None,
            "decimal_odds_blue": None,
            "vig": None,
            "risk_tier": risk_tier,
        }
    or_red = decimal_odds_fair_divide_vig(pr, vig)
    or_blue = decimal_odds_fair_divide_vig(pb, vig)
    return {
        "betting_blocked": False,
        "prob_red": round(pr, 4),
        "prob_blue": round(pb, 4),
        "decimal_odds_red": or_red,
        "decimal_odds_blue": or_blue,
        "vig": vig,
        "risk_tier": risk_tier,
    }


# Compatibilidade: odds antigas sem vig (testes legados)
def decimal_odds_for_side(prob: float) -> float:
    return decimal_odds_fair_divide_vig(float(prob), 1.0)


def odds_pair_from_probs(p_red: float, p_blue: float) -> tuple[float, float, float, float]:
    """Legado: sem vig explícito (equivale a vig=1)."""
    pr, pb = normalize_two_way(p_red, p_blue)
    return pr, pb, decimal_odds_for_side(pr), decimal_odds_for_side(pb)


def extract_probs_from_fight_row(fight: dict) -> tuple[float, float, str | None]:
    """
    Usa modelo ponderado (advanced_prediction) se existir; senão prob_red_pct legado.
    Probabilidades em fração [0,1].
    """
    ap = fight.get("advanced_prediction") or {}
    wm = ap.get("weighted_model") if isinstance(ap, dict) else None
    if isinstance(wm, dict) and wm.get("prob_red_pct") is not None:
        pr = float(wm["prob_red_pct"]) / 100.0
        pb = float(wm.get("prob_blue_pct") or (100.0 - float(wm["prob_red_pct"]))) / 100.0
        risk = (ap.get("risk") or {}).get("tier") if isinstance(ap.get("risk"), dict) else None
        return pr, pb, risk
    pr = float(fight.get("prob_red_pct") or 50) / 100.0
    pb = float(fight.get("prob_blue_pct") or 50) / 100.0
    return pr, pb, None
