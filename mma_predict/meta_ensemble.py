"""
Meta-ensemble fase 4: pesos dinâmicos por contexto + regime, atualização por ROI (suave),
limiares de value bet adaptativos.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from typing import Any, Optional

from mma_predict.learning import learning_data_dir

_lock = threading.Lock()

ALPHA_ROI = 0.028
EMA_BETA = 0.22
WEIGHT_FLOOR = 0.05
WEIGHT_CEIL = 0.62

DEFAULT_WEIGHTS_ML: dict[str, float] = {
    "heuristic": 0.35,
    "bayesian": 0.20,
    "elo": 0.20,
    "ml": 0.25,
}
DEFAULT_WEIGHTS_NO_ML: dict[str, float] = {
    "heuristic": 0.45,
    "bayesian": 0.275,
    "elo": 0.275,
    "ml": 0.0,
}

VALUE_BASE_THRESHOLD = 0.012


def _conn() -> sqlite3.Connection:
    path = learning_data_dir() / "predictions_store.sqlite"
    c = sqlite3.connect(str(path), timeout=30.0)
    c.row_factory = sqlite3.Row
    return c


def init_ensemble_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ensemble_performance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fight_id TEXT,
            model_component TEXT NOT NULL,
            context_bucket TEXT NOT NULL,
            predicted_prob REAL NOT NULL,
            outcome INTEGER NOT NULL,
            roi_contribution REAL NOT NULL,
            regime TEXT,
            created_ts REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ensemble_ctx ON ensemble_performance_log(context_bucket, created_ts)"
    )


def context_bucket(
    division: str,
    red: Any,
    blue: Any,
    volatility: float,
) -> str:
    """
    Buckets interpretáveis (combinados por '|'). Sem dados de short-notice no perfil → omitido.
    """
    parts: list[str] = []
    div = (division or "").lower()
    if any(x in div for x in ("heavy", "pesado", "265")):
        parts.append("heavyweight_fights")
    try:
        exp_r = int(getattr(red, "wins", 0)) + int(getattr(red, "losses", 0))
        exp_b = int(getattr(blue, "wins", 0)) + int(getattr(blue, "losses", 0))
    except (TypeError, ValueError):
        exp_r, exp_b = 20, 20
    if min(exp_r, exp_b) < 8:
        parts.append("low_experience_fights")
    slpm_r = float(getattr(red, "sig_str_lpm", None) or 4.0)
    slpm_b = float(getattr(blue, "sig_str_lpm", None) or 4.0)
    td_r = float(getattr(red, "td_avg", None) or 0.0)
    td_b = float(getattr(blue, "td_avg", None) or 0.0)
    su_r = float(getattr(red, "sub_per_15", None) or 0.0)
    su_b = float(getattr(blue, "sub_per_15", None) or 0.0)
    strike_sum = slpm_r + slpm_b
    grap_sum = td_r + td_b + su_r + su_b
    if strike_sum > 9.5 and grap_sum < 3.2:
        parts.append("striker_vs_striker")
    elif grap_sum > 4.2:
        parts.append("grappler_vs_striker")
    if float(volatility) > 3.25:
        parts.append("high_volatility_fights")
    return "|".join(sorted(parts)) if parts else "general"


def composite_key(context_bucket: str, regime: str) -> str:
    return f"{context_bucket}@@{regime}"


def _default_weights(has_ml: bool) -> dict[str, float]:
    d = dict(DEFAULT_WEIGHTS_ML if has_ml else DEFAULT_WEIGHTS_NO_ML)
    if not has_ml:
        d["ml"] = 0.0
    return _normalize_weights(d, has_ml)


def _normalize_weights(w: dict[str, float], has_ml: bool) -> dict[str, float]:
    out = {k: max(WEIGHT_FLOOR, min(WEIGHT_CEIL, float(w.get(k, 0.0)))) for k in ("heuristic", "bayesian", "elo", "ml")}
    if not has_ml:
        out["ml"] = 0.0
    s = sum(out.values())
    if s <= 0:
        return _default_weights(has_ml)
    return {k: float(v) / s for k, v in out.items()}


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT v FROM learning_meta WHERE k = ?", (key,)).fetchone()
    return str(row["v"]) if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO learning_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, value),
    )


def load_ensemble_weights(conn: sqlite3.Connection, composite_id: str, has_ml: bool) -> dict[str, float]:
    raw = _meta_get(conn, f"ensemble_w:{composite_id}")
    if not raw:
        return _default_weights(has_ml)
    try:
        d = json.loads(raw)
        if not isinstance(d, dict):
            return _default_weights(has_ml)
        w = {k: float(d.get(k, 0.0)) for k in ("heuristic", "bayesian", "elo", "ml")}
        return _normalize_weights(w, has_ml)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _default_weights(has_ml)


def _persist_weights(conn: sqlite3.Connection, composite_id: str, weights: dict[str, float], has_ml: bool) -> None:
    wn = _normalize_weights(weights, has_ml)
    _meta_set(conn, f"ensemble_w:{composite_id}", json.dumps(wn))


def ensemble_prob_red(
    p_h: float,
    p_b: float,
    p_e: float,
    p_ml: Optional[float],
    w: dict[str, float],
    *,
    has_ml: bool,
) -> float:
    pm = float(p_ml) if has_ml and p_ml is not None else p_h
    p = (
        w["heuristic"] * float(p_h)
        + w["bayesian"] * float(p_b)
        + w["elo"] * float(p_e)
        + w["ml"] * pm
    )
    return float(max(1e-6, min(1.0 - 1e-6, p)))


def log_ensemble_performance(
    conn: sqlite3.Connection,
    *,
    fight_id: str,
    context_bucket: str,
    regime: str,
    y_red: int,
    preds: dict[str, Optional[float]],
    roi_by_component: dict[str, float],
) -> None:
    ts = time.time()
    for comp in ("heuristic", "bayesian", "elo", "ml"):
        pv = preds.get(comp)
        if comp == "ml" and pv is None:
            continue
        if pv is None:
            continue
        conn.execute(
            """
            INSERT INTO ensemble_performance_log (
                fight_id, model_component, context_bucket, predicted_prob,
                outcome, roi_contribution, regime, created_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fight_id,
                comp,
                context_bucket,
                float(pv),
                int(y_red),
                float(roi_by_component.get(comp, 0.0)),
                regime,
                ts,
            ),
        )


def update_weights_from_roi(
    conn: sqlite3.Connection,
    composite_id: str,
    has_ml: bool,
    roi_by_component: dict[str, float],
) -> dict[str, float]:
    w0 = load_ensemble_weights(conn, composite_id, has_ml)
    w_step = {
        k: max(WEIGHT_FLOOR, min(WEIGHT_CEIL, w0[k] + ALPHA_ROI * float(roi_by_component.get(k, 0.0))))
        for k in ("heuristic", "bayesian", "elo", "ml")
    }
    w_step = _normalize_weights(w_step, has_ml)
    w_new = {
        k: (1.0 - EMA_BETA) * w0[k] + EMA_BETA * w_step[k]
        for k in ("heuristic", "bayesian", "elo", "ml")
    }
    w_new = _normalize_weights(w_new, has_ml)
    _persist_weights(conn, composite_id, w_new, has_ml)
    return w_new


def fetch_weights_for_prediction(composite_id: str, has_ml: bool) -> dict[str, float]:
    with _lock:
        conn = _conn()
        try:
            init_ensemble_tables(conn)
            return load_ensemble_weights(conn, composite_id, has_ml)
        finally:
            conn.close()


def read_metrics_for_context(context_bucket: str) -> tuple[float, float, float]:
    """(roi_adjustment, dynamic_value_threshold, roi_error_ema) para MC e value 4.0."""
    with _lock:
        conn = _conn()
        try:
            init_ensemble_tables(conn)
            ra = get_roi_adjustment_for_value(conn, context_bucket)
            dt = dynamic_value_threshold(conn, context_bucket)
            er = roi_error_ema_context(conn, context_bucket)
            return ra, dt, er
        finally:
            conn.close()


def mean_roi_context(conn: sqlite3.Connection, context_bucket: str, limit: int = 80) -> Optional[float]:
    row = conn.execute(
        """
        SELECT AVG(roi_contribution) AS m FROM (
            SELECT roi_contribution FROM ensemble_performance_log
            WHERE context_bucket = ?
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (context_bucket, limit),
    ).fetchone()
    if not row or row["m"] is None:
        return None
    return float(row["m"])


def roi_error_ema_context(conn: sqlite3.Connection, context_bucket: str) -> float:
    """Erro médio absoluto de ROI agregado (proxy 0–1) para MC."""
    m = mean_roi_context(conn, context_bucket, limit=60)
    if m is None:
        return 0.0
    return float(min(1.0, abs(m) * 2.5))


def get_roi_adjustment_for_value(conn: sqlite3.Connection, context_bucket: str) -> float:
    """>1 se histórico favorável, <1 se ROI médio negativo (endurece edge efetivo)."""
    m = mean_roi_context(conn, context_bucket, limit=40)
    if m is None:
        return 1.0
    if m < -0.02:
        return max(0.72, 1.0 + 3.5 * m)
    if m > 0.02:
        return min(1.12, 1.0 + 1.8 * m)
    return 1.0


def dynamic_value_threshold(conn: sqlite3.Connection, context_bucket: str) -> float:
    m = mean_roi_context(conn, context_bucket, limit=35)
    t = VALUE_BASE_THRESHOLD
    if m is None:
        return t
    if m < -0.015:
        t *= 1.0 - 4.0 * m
    elif m > 0.02:
        t *= 0.92
    return float(max(0.006, min(0.028, t)))


def value_bet_v4_decision(
    *,
    final_prob_red: float,
    implied_red: float,
    implied_blue: float,
    agreement: float,
    roi_adjustment: float,
    dynamic_threshold: float,
    regime: str,
) -> tuple[bool, Optional[dict[str, Any]]]:
    if regime == "chaotic_card":
        return False, None
    conf = max(float(final_prob_red), 1.0 - float(final_prob_red))
    if conf <= 0.6:
        return False, None
    er = float(final_prob_red) - float(implied_red)
    eb = (1.0 - float(final_prob_red)) - float(implied_blue)
    if er >= eb and er > 0:
        side, edge, implied, p_side = "red", er, implied_red, float(final_prob_red)
    elif eb > 0:
        side, edge, implied, p_side = "blue", eb, implied_blue, 1.0 - float(final_prob_red)
    else:
        return False, None
    vs = max(0.0, edge) * max(0.0, conf) * max(0.0, min(1.0, agreement)) * max(0.35, min(1.35, roi_adjustment))
    if vs <= dynamic_threshold:
        return False, None
    return True, {
        "side": side,
        "model_pct": round(100.0 * p_side, 2),
        "implied_pct": round(100.0 * implied, 2),
        "edge_prob": round(edge, 4),
        "value_score": round(vs, 5),
        "confidence": round(conf, 4),
        "model_agreement": round(agreement, 4),
        "roi_adjustment": round(roi_adjustment, 4),
        "dynamic_threshold": round(dynamic_threshold, 5),
        "note": "Value 4.0: ROI-aware, bloqueado em chaotic_card.",
    }


def process_fight_outcome_ensemble(
    conn: sqlite3.Connection,
    *,
    fight_id: str,
    y_red: int,
    phase4_snapshot_json: Optional[str],
) -> None:
    """Chamado após resultado: regista ROI por componente e atualiza pesos do meta-ensemble."""
    if not phase4_snapshot_json:
        return
    try:
        snap = json.loads(phase4_snapshot_json)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(snap, dict):
        return
    ctx = str(snap.get("context_bucket") or "general")
    regime = str(snap.get("regime") or "stable_card")
    has_ml = bool(snap.get("has_ml"))
    cid = composite_key(ctx, regime)
    preds: dict[str, Optional[float]] = {
        "heuristic": snap.get("p_heuristic"),
        "bayesian": snap.get("p_bayesian"),
        "elo": snap.get("p_elo"),
        "ml": snap.get("p_ml"),
    }
    roi_full: dict[str, float] = {}
    for k, pv in preds.items():
        if pv is None:
            continue
        roi_full[k] = float(y_red) - float(pv)
    log_ensemble_performance(
        conn,
        fight_id=fight_id,
        context_bucket=ctx,
        regime=regime,
        y_red=y_red,
        preds=preds,
        roi_by_component=roi_full,
    )
    roi_update = {k: float(roi_full.get(k, 0.0)) for k in ("heuristic", "bayesian", "elo", "ml")}
    if not has_ml:
        roi_update["ml"] = 0.0
    update_weights_from_roi(conn, cid, has_ml, roi_update)
