"""
Simulação adversarial leve: «oponente de mercado» que explora overconfidence e instabilidade.

Saídas interpretáveis para integração com política RL e MC (fase 5/6).
"""

from __future__ import annotations

import random
from typing import Any


def _perturb_toward_coin(p: float, strength: float, rng: random.Random) -> float:
    """Puxa ``p`` para 0,5 com intensidade 0–1 (exploração de overconfidence)."""
    p0 = max(0.03, min(0.97, float(p)))
    st = max(0.0, min(1.0, float(strength)))
    target = 0.5 + rng.uniform(-0.04, 0.04)
    return max(0.03, min(0.97, p0 + st * (target - p0)))


def run_adversarial_simulation(
    final_prob_red: float,
    *,
    disagreement: float,
    volatility: float,
    regime: str,
    model_agreement: float,
    simulations: int = 600,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    - ``adversarial_hit_rate``: frequência com que o favorito original deixa de ser favorito após perturbação.
    - ``worst_case_roi``: pior retorno simulado numa aposta de 1u ao favorito original (odds justas ~1/p).
    - ``stress_test_score``: 0–1 (maior = mais stress).
    - ``vulnerability_index``: 0–1 (exposição a instabilidade).
    """
    rng = random.Random(seed)
    p_red = max(0.03, min(0.97, float(final_prob_red)))
    fav_red = p_red >= 0.5
    p_fav = p_red if fav_red else 1.0 - p_red
    p_fav = max(0.52, min(0.98, p_fav))
    dec_odds = 1.0 / p_fav

    vuln = min(
        1.0,
        0.35 * max(0.0, float(disagreement))
        + 0.22 * min(1.0, float(volatility) / 10.0)
        + (0.18 if regime == "chaotic_card" else 0.0)
        + 0.25 * (1.0 - max(0.0, min(1.0, float(model_agreement)))),
    )
    strength = min(0.85, 0.32 + 0.55 * vuln)
    n = max(120, int(simulations))
    flips = 0
    rois: list[float] = []
    for _ in range(n):
        p_adv = _perturb_toward_coin(p_red, strength, rng)
        fav_adv = p_adv >= 0.5
        if fav_adv != fav_red:
            flips += 1
        red_wins = rng.random() < p_adv
        if fav_red:
            won = red_wins
        else:
            won = not red_wins
        pnl = (dec_odds - 1.0) if won else -1.0
        rois.append(pnl)

    adv_hit = flips / n if n else 0.0
    worst = min(rois) if rois else 0.0
    stress = float(max(0.0, min(1.0, 0.45 * adv_hit + 0.35 * vuln + 0.2 * min(1.0, max(0.0, -worst)))))
    return {
        "adversarial_hit_rate": round(adv_hit, 4),
        "worst_case_roi": round(worst, 4),
        "stress_test_score": round(stress, 4),
        "vulnerability_index": round(vuln, 4),
        "simulations": n,
    }


def init_adversarial_tables(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS adversarial_sim_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fight_id TEXT,
            fight_context TEXT,
            stress_score REAL,
            worst_case_roi REAL,
            vulnerability_index REAL,
            created_ts REAL NOT NULL
        )
        """
    )
