"""
Cálculo de probabilidade e odds por perna de parlay (educativo).

Tipos: final_result (vencedor), method (KO/Decisão/Sub condicional ao canto),
round_winner (cantos + round 1–5).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from betting.odds_math import (
    decimal_odds_fair_divide_vig,
    extract_probs_from_fight_row,
    odds_pair_from_probs_vig,
    vig_multiplier,
)

# Distribuição heurística de rounds (1–5) condicional à vitória
_ROUND_WEIGHTS = (0.28, 0.24, 0.20, 0.16, 0.12)

METHOD_KEYS = ("ko_tko", "decisao", "finalizacao")

OPTION_TO_KEY: dict[str, str] = {
    "KO": "ko_tko",
    "ko": "ko_tko",
    "ko_tko": "ko_tko",
    "Decisão": "decisao",
    "decisao": "decisao",
    "DEC": "decisao",
    "Sub": "finalizacao",
    "SUB": "finalizacao",
    "finalizacao": "finalizacao",
}


def normalize_method_option(option: Any) -> str:
    if option is None:
        raise ValueError("option obrigatório para method")
    s = str(option).strip()
    if s in OPTION_TO_KEY:
        return OPTION_TO_KEY[s]
    low = s.lower()
    if low in ("ko", "tko", "nocaute"):
        return "ko_tko"
    if low in ("dec", "decisão", "decisao", "points"):
        return "decisao"
    if low in ("sub", "finalização", "finalizacao"):
        return "finalizacao"
    raise ValueError(f"Método inválido: {option}")


def _pct_map(d: dict[str, Any], key: str) -> float:
    v = d.get(key)
    if v is None:
        return 0.0
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _method_conditional_prob(fight: dict[str, Any], side: str, method_key: str) -> float:
    """P(method | side vence), usando if_favorite_wins_pct ou heurística."""
    fav = (fight.get("favorite_corner") or "").strip().lower()
    iff = fight.get("if_favorite_wins_pct") or {}
    methods = fight.get("methods_pct") or {}
    if side == fav and isinstance(iff, dict) and iff:
        total = sum(_pct_map(iff, k) for k in METHOD_KEYS)
        if total <= 0:
            return 1.0 / 3.0
        return _pct_map(iff, method_key) / total
    # Underdog ou dados incompletos: usar methods_pct global normalizado
    total_m = sum(_pct_map(methods, k) for k in METHOD_KEYS)
    if total_m > 0:
        return _pct_map(methods, method_key) / total_m
    return 1.0 / 3.0


def _round_conditional_prob(round_num: int) -> float:
    i = int(round_num) - 1
    if 0 <= i < len(_ROUND_WEIGHTS):
        return float(_ROUND_WEIGHTS[i])
    return 1.0 / 5.0


def compute_leg(
    fight: dict[str, Any],
    *,
    bet_type: str,
    side: str,
    option: Any = None,
) -> dict[str, Any]:
    """
    Retorna odds decimais da perna, prob implícita, risco, bloqueio SKIP.
    """
    side = (side or "").strip().lower()
    if side not in ("red", "blue"):
        raise ValueError("side deve ser red ou blue")
    bt = (bet_type or "").strip().lower()
    if bt not in ("final_result", "method", "round_winner"):
        raise ValueError("bet_type inválido")

    if fight.get("error"):
        raise ValueError("Luta com erro de análise")

    pr, pb, risk = extract_probs_from_fight_row(fight)
    vig = vig_multiplier(risk)
    ap = fight.get("advanced_prediction") if isinstance(fight.get("advanced_prediction"), dict) else {}
    wm = ap.get("weighted_model") if isinstance(ap, dict) else None
    value_bet = ap.get("value_bet") if isinstance(ap, dict) else None

    out_base: dict[str, Any] = {
        "bet_type": bt,
        "side": side,
        "option": option,
        "risk_tier": risk,
        "weighted_model": wm,
        "value_bet": value_bet,
    }

    if vig is None:
        return {
            **out_base,
            "betting_blocked": True,
            "decimal_odds": None,
            "prob_leg": None,
            "vig": None,
        }

    p_win = pr if side == "red" else pb

    if bt == "final_result":
        pkg = odds_pair_from_probs_vig(pr, pb, risk)
        if pkg.get("betting_blocked"):
            return {**out_base, "betting_blocked": True, "decimal_odds": None, "prob_leg": None, "vig": None}
        dec = float(pkg["decimal_odds_red"] if side == "red" else pkg["decimal_odds_blue"])
        p_leg = float(pkg["prob_red"] if side == "red" else pkg["prob_blue"])
        return {
            **out_base,
            "betting_blocked": False,
            "decimal_odds": dec,
            "prob_leg": round(p_leg, 4),
            "vig": pkg.get("vig"),
            "prob_red": pkg.get("prob_red"),
            "prob_blue": pkg.get("prob_blue"),
        }

    if bt == "method":
        mk = normalize_method_option(option)
        p_cond = _method_conditional_prob(fight, side, mk)
        p_leg = max(1e-6, min(1.0, p_win * p_cond))
        dec = decimal_odds_fair_divide_vig(p_leg, vig)
        return {
            **out_base,
            "betting_blocked": False,
            "decimal_odds": dec,
            "prob_leg": round(p_leg, 4),
            "vig": vig,
            "method_key": mk,
        }

    if bt == "round_winner":
        try:
            rn = int(option)
        except (TypeError, ValueError) as e:
            raise ValueError("round_winner requer option inteiro 1–5") from e
        if rn < 1 or rn > 5:
            raise ValueError("Round deve ser 1–5")
        p_cond = _round_conditional_prob(rn)
        p_leg = max(1e-6, min(1.0, p_win * p_cond))
        dec = decimal_odds_fair_divide_vig(p_leg, vig)
        return {
            **out_base,
            "betting_blocked": False,
            "decimal_odds": dec,
            "prob_leg": round(p_leg, 4),
            "vig": vig,
            "round": rn,
        }

    raise ValueError("bet_type desconhecido")


def combined_decimal(legs: list[dict[str, Any]]) -> float:
    """Produto das odds decimais (parlay)."""
    d = 1.0
    for leg in legs:
        od = leg.get("decimal_odds")
        if od is None:
            return 0.0
        d *= float(od)
    return round(d, 4)


def leg_wins_against_result(
    leg: dict[str, Any],
    *,
    winner_side: str,
    official_method: Optional[str] = None,
    round_num: Optional[int] = None,
) -> bool:
    """Verifica se a perna ganha dado o resultado oficial."""
    ws = (winner_side or "").strip().lower()
    side = (leg.get("side") or "").strip().lower()
    bt = (leg.get("bet_type") or "").strip().lower()

    if side != ws:
        return False

    if bt == "final_result":
        return True

    if bt == "method":
        mk = leg.get("method_key")
        if not mk and leg.get("option") is not None:
            try:
                mk = normalize_method_option(leg.get("option"))
            except ValueError:
                mk = None
        if not mk:
            return False
        om = (official_method or "").strip().lower()
        if om in ("ko", "tko", "ko_tko", "nocaute"):
            omk = "ko_tko"
        elif om in ("dec", "decisão", "decisao", "points"):
            omk = "decisao"
        elif om in ("sub", "finalização", "finalizacao"):
            omk = "finalizacao"
        else:
            omk = om
        return bool(mk) and mk == omk

    if bt == "round_winner":
        try:
            want = int(leg.get("option"))
        except (TypeError, ValueError):
            return False
        if round_num is None:
            return False
        return int(round_num) == want

    return False


def parse_legs_json(s: str) -> list[dict[str, Any]]:
    data = json.loads(s)
    if not isinstance(data, list):
        raise ValueError("legs_json inválido")
    return data
