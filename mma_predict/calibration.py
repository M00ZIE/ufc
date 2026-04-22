"""
Ganchos futuros para calibração (Brier, armazenamento de previsões).

Chamar explicitamente quando existir backend de persistência.
"""

from __future__ import annotations

from typing import Any, Optional


def record_prediction_snapshot(
    *,
    event_url: str,
    fight_index: int,
    payload: dict[str, Any],
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """
    Gancho opcional: se ``meta`` incluir ``fight_id`` e termos do matchup,
    delega em ``mma_predict.learning.log_prediction`` (compatível com aprendizagem online).
    """
    del payload
    m = meta or {}
    fight_id = m.get("fight_id")
    if not fight_id:
        return
    try:
        from mma_predict.learning import log_prediction
    except ImportError:
        return
    try:
        ft = m.get("feature_terms_vector") or [0.0, 0.0, 0.0, 0.0]
        if len(ft) != 4:
            return
        log_prediction(
            fight_id=str(fight_id),
            event_url=str(event_url),
            fight_index=int(fight_index),
            model_prob=float(m["model_prob"]),
            monte_carlo_prob=float(m["monte_carlo_prob"]),
            confidence=float(m.get("confidence", 0.5)),
            volatility=float(m.get("volatility", 0.0)),
            term_strike=float(ft[0]),
            term_grap=float(ft[1]),
            term_tdd=float(ft[2]),
            term_card=float(ft[3]),
            value_flag=bool(m.get("value_flag")),
            value_side=m.get("value_side"),
            value_edge=float(m["value_edge"]) if m.get("value_edge") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return


def brier_score_binary(outcomes: list[tuple[float, int]]) -> Optional[float]:
    """
    outcomes: lista de (prob_prevista_para_evento_1, y) com y em {0,1}.
    Devolve média de (p-y)^2 ou None se vazio.
    """
    if not outcomes:
        return None
    s = 0.0
    for p, y in outcomes:
        s += (float(p) - float(y)) ** 2
    return s / len(outcomes)
