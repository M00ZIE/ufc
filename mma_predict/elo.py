"""
Elo-style rating por lutador (memória de longo prazo), integrado com a BD de aprendizagem.

K-factor dinâmico: volatilidade da luta, confiança da previsão heurística, estado de drift.
"""

from __future__ import annotations

import math
import sqlite3
import time
from typing import Any, Optional

DEFAULT_RATING = 1500.0
DEFAULT_SIGMA = 350.0
BASE_K = 24.0


def init_elo_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fighter_elo (
            fighter_id TEXT PRIMARY KEY,
            rating REAL NOT NULL DEFAULT 1500.0,
            sigma REAL NOT NULL DEFAULT 350.0,
            fights INTEGER NOT NULL DEFAULT 0,
            updated_ts REAL
        )
        """
    )


def _expected_score(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (r_b - r_a) / 400.0))


def elo_prob_red(r_red: float, r_blue: float) -> float:
    """P(vermelho) a partir da diferença de ratings (vermelho = A)."""
    return _expected_score(r_red, r_blue)


def elo_rating_diff(r_red: float, r_blue: float) -> float:
    return float(r_red - r_blue)


def dynamic_k(
    *,
    volatility: float,
    confidence: float,
    model_stability: str,
) -> float:
    """
    K maior em lutas explosivas e quando o modelo está menos confiante ou em degradação.
    """
    vol = max(0.0, float(volatility))
    conf = max(0.35, min(1.0, float(confidence)))
    mult_vol = 1.0 + min(1.2, vol / 8.0)
    mult_conf = 1.0 + (1.0 - conf) * 0.85
    mult_drift = 1.28 if model_stability == "degrading" else 1.0
    if model_stability == "improving":
        mult_drift = 0.94
    k = BASE_K * mult_vol * mult_conf * mult_drift
    return float(max(8.0, min(72.0, k)))


def get_row(conn: sqlite3.Connection, fighter_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT fighter_id, rating, sigma, fights, updated_ts FROM fighter_elo WHERE fighter_id = ?",
        (fighter_id,),
    ).fetchone()


def get_rating(conn: sqlite3.Connection, fighter_id: str) -> tuple[float, float, int]:
    row = get_row(conn, fighter_id)
    if not row:
        return DEFAULT_RATING, DEFAULT_SIGMA, 0
    fights = int(row["fights"] or 0)
    return float(row["rating"]), float(row["sigma"]), fights


def ensure_fighter(conn: sqlite3.Connection, fighter_id: str) -> tuple[float, float, int]:
    fid = (fighter_id or "").strip()
    if not fid:
        return DEFAULT_RATING, DEFAULT_SIGMA, 0
    row = get_row(conn, fid)
    if row:
        nf = int(row["fights"] or 0)
        return float(row["rating"]), float(row["sigma"]), nf
    ts = time.time()
    conn.execute(
        """
        INSERT INTO fighter_elo (fighter_id, rating, sigma, fights, updated_ts)
        VALUES (?, ?, ?, 0, ?)
        """,
        (fid, DEFAULT_RATING, DEFAULT_SIGMA, ts),
    )
    return DEFAULT_RATING, DEFAULT_SIGMA, 0


def apply_fight_result(
    conn: sqlite3.Connection,
    *,
    red_slug: str,
    blue_slug: str,
    red_won: bool,
    volatility: float,
    confidence: float,
    model_stability: str,
) -> dict[str, Any]:
    """
    Atualiza ratings após resultado (1 vitória vermelho, 0 azul).
    Deve ser chamado dentro da mesma transação/lock que o resto da aprendizagem, se aplicável.
    """
    rs = (red_slug or "").strip()
    bs = (blue_slug or "").strip()
    if not rs or not bs:
        return {"ok": False, "reason": "missing_slugs"}

    r_red, sig_r, n_r = ensure_fighter(conn, rs)
    r_blue, sig_b, n_b = ensure_fighter(conn, bs)
    exp_red = _expected_score(r_red, r_blue)
    y = 1.0 if red_won else 0.0
    k = dynamic_k(volatility=volatility, confidence=confidence, model_stability=model_stability)

    new_red = r_red + k * (y - exp_red)
    new_blue = r_blue + k * ((1.0 - y) - (1.0 - exp_red))

    # sigma desce ligeiramente com mais dados (opcional, interpretável)
    def _shrink_sigma(s: float, n: int) -> float:
        return float(max(200.0, s * 0.998 + 0.4 / max(1, n + 1)))

    ts = time.time()
    conn.execute(
        """
        UPDATE fighter_elo SET rating = ?, sigma = ?, fights = fights + 1, updated_ts = ?
        WHERE fighter_id = ?
        """,
        (new_red, _shrink_sigma(sig_r, n_r), ts, rs),
    )
    conn.execute(
        """
        UPDATE fighter_elo SET rating = ?, sigma = ?, fights = fights + 1, updated_ts = ?
        WHERE fighter_id = ?
        """,
        (new_blue, _shrink_sigma(sig_b, n_b), ts, bs),
    )
    return {
        "ok": True,
        "k_used": round(k, 3),
        "expected_red": round(exp_red, 5),
        "rating_red_before": round(r_red, 2),
        "rating_blue_before": round(r_blue, 2),
        "rating_red_after": round(new_red, 2),
        "rating_blue_after": round(new_blue, 2),
    }


def read_matchup_snapshot(red_slug: str, blue_slug: str) -> dict[str, Any]:
    """Lê ratings do SQLite de aprendizagem (só leitura; cria ficheiro/tabelas se ainda não existirem)."""
    from mma_predict.learning import learning_data_dir

    path = learning_data_dir() / "predictions_store.sqlite"
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        init_elo_tables(conn)
        return matchup_snapshot(conn, red_slug, blue_slug)
    finally:
        conn.close()


def matchup_snapshot(conn: sqlite3.Connection, red_slug: str, blue_slug: str) -> dict[str, Any]:
    rs = (red_slug or "").strip()
    bs = (blue_slug or "").strip()
    if not rs or not bs:
        return {
            "rating_red": DEFAULT_RATING,
            "rating_blue": DEFAULT_RATING,
            "elo_diff": 0.0,
            "elo_prob_red": 0.5,
        }
    r_red, _, _ = ensure_fighter(conn, rs)
    r_blue, _, _ = ensure_fighter(conn, bs)
    diff = elo_rating_diff(r_red, r_blue)
    p = elo_prob_red(r_red, r_blue)
    return {
        "rating_red": round(r_red, 2),
        "rating_blue": round(r_blue, 2),
        "elo_diff": round(diff, 2),
        "elo_prob_red": round(p, 5),
    }
