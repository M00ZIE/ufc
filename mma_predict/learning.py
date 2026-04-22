"""
Aprendizagem online leve: registo de previsões, ajuste incremental de pesos do matchup,
deteção de drift, ruído MC adaptativo, limiar de value bet e relatórios periódicos.

Persistência: SQLite em ``data/mma_learning/`` (ou ``MMA_LEARNING_DATA_DIR``).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from mma_predict.calibration import brier_score_binary

# Hiperparâmetros conservadores (estabilidade > velocidade)
ALPHA_WEIGHT = 0.025
VALUE_MARGIN_STEP = 0.005
VALUE_MARGIN_MIN = 0.03
VALUE_MARGIN_MAX = 0.12
REPORT_EVERY_RESOLVED = 50
WEIGHT_MIN = 0.08
WEIGHT_MAX = 0.52
WINDOW_SHORT = 50
WINDOW_LONG = 100
MC_ERROR_EMA_ALPHA = 0.12

_lock = threading.Lock()

DEFAULT_WEIGHTS: tuple[float, float, float, float] = (0.35, 0.35, 0.20, 0.10)


def learning_data_dir() -> Path:
    raw = os.environ.get("MMA_LEARNING_DATA_DIR")
    if raw:
        d = Path(raw)
    else:
        d = Path(__file__).resolve().parent.parent / "data" / "mma_learning"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path() -> Path:
    return learning_data_dir() / "predictions_store.sqlite"


def make_fight_id(event_url: str, fight_index: int) -> str:
    """Identificador estável por evento + índice da luta (1-based)."""
    h = hashlib.sha256(f"{event_url.strip()}\n{int(fight_index)}".encode("utf-8")).hexdigest()
    return h[:32]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def _migrate_predictions_log(conn: sqlite3.Connection) -> None:
    cols = _column_names(conn, "predictions_log")
    alters = []
    if "red_slug" not in cols:
        alters.append("ALTER TABLE predictions_log ADD COLUMN red_slug TEXT")
    if "blue_slug" not in cols:
        alters.append("ALTER TABLE predictions_log ADD COLUMN blue_slug TEXT")
    if "phase4_snapshot" not in cols:
        alters.append("ALTER TABLE predictions_log ADD COLUMN phase4_snapshot TEXT")
    if "phase5_snapshot" not in cols:
        alters.append("ALTER TABLE predictions_log ADD COLUMN phase5_snapshot TEXT")
    for stmt in alters:
        conn.execute(stmt)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS predictions_log (
            fight_id TEXT PRIMARY KEY,
            event_url TEXT NOT NULL,
            fight_index INTEGER NOT NULL,
            model_prob REAL NOT NULL,
            monte_carlo_prob REAL NOT NULL,
            confidence REAL NOT NULL,
            volatility REAL NOT NULL,
            term_strike REAL NOT NULL,
            term_grap REAL NOT NULL,
            term_tdd REAL NOT NULL,
            term_card REAL NOT NULL,
            value_flag INTEGER NOT NULL DEFAULT 0,
            value_side TEXT,
            value_edge REAL,
            result INTEGER,
            created_ts REAL NOT NULL,
            result_ts REAL
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_result_ts
            ON predictions_log(result_ts) WHERE result IS NOT NULL;
        CREATE TABLE IF NOT EXISTS learning_meta (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bayesian_prior_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fight_id TEXT,
            prior_red REAL,
            posterior_red REAL,
            ml_red REAL,
            w_prior REAL,
            w_ml REAL,
            created_ts REAL
        );
        """
    )
    from mma_predict.adversarial_sim import init_adversarial_tables
    from mma_predict.bankroll import init_phase7_tables
    from mma_predict.elo import init_elo_tables
    from mma_predict.meta_ensemble import init_ensemble_tables
    from mma_predict.rl_policy import init_rl_tables
    from mma_predict.self_play import init_self_play_tables

    init_elo_tables(conn)
    init_ensemble_tables(conn)
    init_adversarial_tables(conn)
    init_rl_tables(conn)
    init_self_play_tables(conn)
    init_phase7_tables(conn)
    _migrate_predictions_log(conn)
    conn.commit()


def _ensure_db() -> sqlite3.Connection:
    conn = _conn()
    _init_schema(conn)
    return conn


def _meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT v FROM learning_meta WHERE k = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row["v"])


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO learning_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, value),
    )


def _load_weights(conn: sqlite3.Connection) -> tuple[float, float, float, float]:
    raw = _meta_get(conn, "matchup_weights_json")
    if not raw:
        return DEFAULT_WEIGHTS
    try:
        arr = json.loads(raw)
        if isinstance(arr, list) and len(arr) == 4:
            w = tuple(max(WEIGHT_MIN, min(WEIGHT_MAX, float(x))) for x in arr)
            s = sum(w)
            if s <= 0:
                return DEFAULT_WEIGHTS
            return tuple(float(x) / s for x in w)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return DEFAULT_WEIGHTS


def _save_weights(conn: sqlite3.Connection, w: tuple[float, float, float, float]) -> None:
    s = sum(w)
    if s <= 0:
        wn = list(DEFAULT_WEIGHTS)
    else:
        wn = [max(WEIGHT_MIN, min(WEIGHT_MAX, float(x) / s)) for x in w]
    s2 = sum(wn)
    wn = [float(x) / s2 for x in wn]
    _meta_set(conn, "matchup_weights_json", json.dumps(wn))


def _load_float_meta(conn: sqlite3.Connection, key: str, default: float) -> float:
    raw = _meta_get(conn, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_matchup_weights() -> tuple[float, float, float, float]:
    with _lock:
        conn = _ensure_db()
        try:
            return _load_weights(conn)
        finally:
            conn.close()


def get_monte_carlo_noise_scale() -> float:
    """Multiplicador ≥1 sobre o ruído base do MC (sobe quando o modelo erra mais)."""
    err = _load_float_meta_global("error_rate_ema", 0.0)
    return float(1.0 + min(1.5, max(0.0, err) * 2.0))


def _load_float_meta_global(key: str, default: float) -> float:
    with _lock:
        conn = _ensure_db()
        try:
            return _load_float_meta(conn, key, default)
        finally:
            conn.close()


def get_volatility_risk_multiplier() -> float:
    """Inflaciona volatilidade usada no tier de risco quando há degradação."""
    d = detect_drift()
    if d.get("stability") == "degrading":
        return float(1.0 + 0.22 * float(d.get("drift_score", 0.0)))
    return 1.0


def get_value_edge_margin() -> float:
    """Limiar mínimo de edge para marcar value bet (adaptativo)."""
    return _load_float_meta_global("value_edge_margin", 0.05)


def _adjust_weights(
    conn: sqlite3.Connection,
    *,
    model_prob_red: float,
    y_red: int,
    terms: tuple[float, float, float, float],
) -> None:
    """Pequeno passo na direção oposta ao erro (p_red - y), com renormalização."""
    w = list(_load_weights(conn))
    e = float(model_prob_red) - float(y_red)
    for k in range(4):
        w[k] -= ALPHA_WEIGHT * e * float(terms[k])
    _save_weights(conn, tuple(w))


def _update_error_ema(conn: sqlite3.Connection, abs_err: float) -> None:
    prev = _load_float_meta(conn, "error_rate_ema", abs_err)
    new = (1.0 - MC_ERROR_EMA_ALPHA) * prev + MC_ERROR_EMA_ALPHA * abs_err
    _meta_set(conn, "error_rate_ema", str(round(new, 6)))


def _update_value_margin_from_window(conn: sqlite3.Connection) -> None:
    raw = _meta_get(conn, "value_bet_window_json", "[]")
    try:
        window: list[dict[str, Any]] = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        window = []
    if len(window) < 5:
        return
    wins = sum(1 for x in window if x.get("won"))
    n = len(window)
    win_rate = wins / n if n else 0.5
    margin = _load_float_meta(conn, "value_edge_margin", 0.05)
    if win_rate < 0.45:
        margin = min(VALUE_MARGIN_MAX, margin + VALUE_MARGIN_STEP)
    elif win_rate > 0.58:
        margin = max(VALUE_MARGIN_MIN, margin - VALUE_MARGIN_STEP * 0.5)
    _meta_set(conn, "value_edge_margin", str(round(margin, 4)))


def _append_value_outcome(conn: sqlite3.Connection, won: bool, edge: float) -> None:
    raw = _meta_get(conn, "value_bet_window_json", "[]")
    try:
        window: list[dict[str, Any]] = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        window = []
    window.append({"won": bool(won), "edge": float(edge), "ts": time.time()})
    window = window[-40:]
    _meta_set(conn, "value_bet_window_json", json.dumps(window))
    _update_value_margin_from_window(conn)


def _resolved_rows_ordered(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT model_prob, result, result_ts
            FROM predictions_log
            WHERE result IS NOT NULL
            ORDER BY result_ts DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def _drift_metrics_unlocked(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Brier e acerto recentes vs janela anterior (sem adquirir ``_lock`` — usar só com lock já detido
    ou ligação dedicada).
    """
    recent = _resolved_rows_ordered(conn, WINDOW_SHORT)
    older = conn.execute(
        """
        SELECT model_prob, result FROM predictions_log
        WHERE result IS NOT NULL
        ORDER BY result_ts DESC, rowid DESC
        LIMIT ? OFFSET ?
        """,
        (WINDOW_SHORT, WINDOW_SHORT),
    ).fetchall()

    def _pack(rows: list[sqlite3.Row]) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        for r in rows:
            p = float(r["model_prob"])
            y = int(r["result"])
            out.append((p, y))
        return out

    pack_recent = _pack(recent)
    pack_older = _pack(list(older))

    brier_r = brier_score_binary(pack_recent)
    brier_o = brier_score_binary(pack_older) if len(pack_older) >= 15 else None

    acc_r = None
    if pack_recent:
        acc_r = sum(1 for p, y in pack_recent if (p >= 0.5 and y == 1) or (p < 0.5 and y == 0)) / len(
            pack_recent
        )

    drift_score = 0.0
    if brier_r is not None:
        drift_score = min(1.0, float(brier_r) / 0.28)

    stability = "stable"
    if brier_r is not None and brier_o is not None:
        if brier_r > brier_o + 0.02:
            stability = "degrading"
        elif brier_r < brier_o - 0.02:
            stability = "improving"

    adaptive_confidence = 1.0
    if brier_r is not None:
        adaptive_confidence = max(0.35, 1.0 - 2.2 * float(brier_r))
    if stability == "degrading":
        adaptive_confidence *= 0.88
    elif stability == "improving":
        adaptive_confidence = min(1.0, adaptive_confidence * 1.04)

    return {
        "drift_score": round(float(drift_score), 4),
        "stability": stability,
        "adaptive_confidence": round(float(adaptive_confidence), 4),
        "brier_recent_50": round(float(brier_r), 6) if brier_r is not None else None,
        "accuracy_recent_50": round(float(acc_r), 4) if acc_r is not None else None,
        "n_resolved_recent": len(pack_recent),
    }


def detect_drift() -> dict[str, Any]:
    """Brier e acerto recentes vs janela anterior; ``drift_score`` 0–1 (maior = pior calibração recente)."""
    with _lock:
        conn = _ensure_db()
        try:
            return _drift_metrics_unlocked(conn)
        finally:
            conn.close()


def get_learning_api_payload() -> dict[str, Any]:
    """Campos pedidos para ``/api/analyze`` (nível evento)."""
    d = detect_drift()
    out = {
        "drift_score": float(d["drift_score"]),
        "model_stability": str(d["stability"]),
        "adaptive_confidence": float(d["adaptive_confidence"]),
    }
    try:
        from mma_predict.bankroll import equity_gradient_signal_readonly

        out["equity_gradient_signal"] = round(float(equity_gradient_signal_readonly()), 4)
    except Exception:
        out["equity_gradient_signal"] = 0.0
    return out


def log_prediction(
    *,
    fight_id: str,
    event_url: str,
    fight_index: int,
    model_prob: float,
    monte_carlo_prob: float,
    confidence: float,
    volatility: float,
    term_strike: float,
    term_grap: float,
    term_tdd: float,
    term_card: float,
    value_flag: bool = False,
    value_side: str | None = None,
    value_edge: float | None = None,
    red_slug: str | None = None,
    blue_slug: str | None = None,
    phase4_snapshot: str | None = None,
    phase5_snapshot: str | None = None,
) -> None:
    """Grava ou atualiza previsão enquanto o resultado ainda não existe."""
    ts = time.time()
    vf = 1 if value_flag else 0
    vs = (value_side or "").strip().lower() if value_side else None
    if vs not in ("red", "blue", None):
        vs = None
    rs = (red_slug or "").strip() or None
    bs = (blue_slug or "").strip() or None
    with _lock:
        conn = _ensure_db()
        try:
            conn.execute(
                """
                INSERT INTO predictions_log (
                    fight_id, event_url, fight_index,
                    model_prob, monte_carlo_prob, confidence, volatility,
                    term_strike, term_grap, term_tdd, term_card,
                    value_flag, value_side, value_edge, red_slug, blue_slug,
                    phase4_snapshot, phase5_snapshot, created_ts, result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(fight_id) DO UPDATE SET
                    model_prob = excluded.model_prob,
                    monte_carlo_prob = excluded.monte_carlo_prob,
                    confidence = excluded.confidence,
                    volatility = excluded.volatility,
                    term_strike = excluded.term_strike,
                    term_grap = excluded.term_grap,
                    term_tdd = excluded.term_tdd,
                    term_card = excluded.term_card,
                    value_flag = excluded.value_flag,
                    value_side = excluded.value_side,
                    value_edge = excluded.value_edge,
                    red_slug = COALESCE(excluded.red_slug, predictions_log.red_slug),
                    blue_slug = COALESCE(excluded.blue_slug, predictions_log.blue_slug),
                    phase4_snapshot = COALESCE(excluded.phase4_snapshot, predictions_log.phase4_snapshot),
                    phase5_snapshot = COALESCE(excluded.phase5_snapshot, predictions_log.phase5_snapshot),
                    created_ts = excluded.created_ts
                WHERE predictions_log.result IS NULL
                """,
                (
                    fight_id,
                    event_url,
                    int(fight_index),
                    float(model_prob),
                    float(monte_carlo_prob),
                    float(confidence),
                    float(volatility),
                    float(term_strike),
                    float(term_grap),
                    float(term_tdd),
                    float(term_card),
                    vf,
                    vs,
                    float(value_edge) if value_edge is not None else None,
                    rs,
                    bs,
                    phase4_snapshot,
                    phase5_snapshot,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def log_bayesian_prior_snapshot(
    *,
    fight_id: str,
    prior_red: float,
    posterior_red: float,
    ml_red: Optional[float],
    w_prior: float,
    w_ml: float,
) -> None:
    """Registo opcional para auditoria / calibração Bayesiana."""
    with _lock:
        conn = _ensure_db()
        try:
            conn.execute(
                """
                INSERT INTO bayesian_prior_log (
                    fight_id, prior_red, posterior_red, ml_red, w_prior, w_ml, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fight_id,
                    float(prior_red),
                    float(posterior_red),
                    float(ml_red) if ml_red is not None else None,
                    float(w_prior),
                    float(w_ml),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_fight_outcome(event_url: str, fight_index: int, *, red_won: bool) -> None:
    """
    Chamado após resultado conhecido (ex.: liquidação de apostas).
    Atualiza registo, pesos, EMA de erro e, periodicamente, ``learning_report.json``.
    """
    fight_id = make_fight_id(event_url, fight_index)
    y = 1 if red_won else 0
    ts = time.time()
    n_resolved_int = 0
    with _lock:
        conn = _ensure_db()
        try:
            cur = conn.execute(
                """
                UPDATE predictions_log
                SET result = ?, result_ts = ?
                WHERE fight_id = ? AND result IS NULL
                """,
                (y, ts, fight_id),
            )
            if cur.rowcount == 0:
                conn.commit()
                return
            row = conn.execute(
                """
                SELECT model_prob, term_strike, term_grap, term_tdd, term_card,
                       value_flag, value_side, value_edge, volatility, confidence,
                       red_slug, blue_slug, phase4_snapshot, phase5_snapshot
                FROM predictions_log WHERE fight_id = ?
                """,
                (fight_id,),
            ).fetchone()
            if not row:
                conn.commit()
                return
            p = float(row["model_prob"])
            terms = (
                float(row["term_strike"]),
                float(row["term_grap"]),
                float(row["term_tdd"]),
                float(row["term_card"]),
            )
            _adjust_weights(conn, model_prob_red=p, y_red=y, terms=terms)
            _update_error_ema(conn, abs(p - float(y)))

            if int(row["value_flag"]) == 1 and row["value_side"] in ("red", "blue"):
                side = str(row["value_side"])
                won = (side == "red" and y == 1) or (side == "blue" and y == 0)
                edge = float(row["value_edge"] or 0.0)
                _append_value_outcome(conn, won=won, edge=edge)

            drift_info = _drift_metrics_unlocked(conn)
            stability = str(drift_info.get("stability", "stable"))
            vol = float(row["volatility"] or 0.0)
            conf = float(row["confidence"] or 0.5)
            rs = row["red_slug"] if "red_slug" in row.keys() else None
            bs = row["blue_slug"] if "blue_slug" in row.keys() else None
            if rs and bs:
                from mma_predict.elo import apply_fight_result

                apply_fight_result(
                    conn,
                    red_slug=str(rs),
                    blue_slug=str(bs),
                    red_won=bool(y),
                    volatility=vol,
                    confidence=conf,
                    model_stability=stability,
                )

            snap = row["phase4_snapshot"] if "phase4_snapshot" in row.keys() else None
            if snap:
                from mma_predict.meta_ensemble import init_ensemble_tables, process_fight_outcome_ensemble

                init_ensemble_tables(conn)
                process_fight_outcome_ensemble(
                    conn,
                    fight_id=fight_id,
                    y_red=y,
                    phase4_snapshot_json=str(snap),
                )

            snap5 = row["phase5_snapshot"] if "phase5_snapshot" in row.keys() else None
            if snap5:
                from mma_predict.adversarial_sim import init_adversarial_tables
                from mma_predict.rl_policy import init_rl_tables, process_rl_outcome

                try:
                    j5: Any = json.loads(str(snap5))
                except (json.JSONDecodeError, TypeError):
                    j5 = None
                if isinstance(j5, dict):
                    init_adversarial_tables(conn)
                    conn.execute(
                        """
                        INSERT INTO adversarial_sim_log (
                            fight_id, fight_context, stress_score, worst_case_roi,
                            vulnerability_index, created_ts
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fight_id,
                            str(j5.get("roi_context") or ""),
                            float(j5["stress_test_score"])
                            if j5.get("stress_test_score") is not None
                            else None,
                            float(j5["worst_case_roi"]) if j5.get("worst_case_roi") is not None else None,
                            float(j5["adversarial_risk"])
                            if j5.get("adversarial_risk") is not None
                            else None,
                            ts,
                        ),
                    )
                init_rl_tables(conn)
                process_rl_outcome(
                    conn,
                    fight_id=fight_id,
                    y_red=y,
                    phase5_snapshot_json=str(snap5),
                    model_prob_red=p,
                    volatility=vol,
                )

            n_resolved = conn.execute(
                "SELECT COUNT(*) AS c FROM predictions_log WHERE result IS NOT NULL"
            ).fetchone()["c"]
            n_resolved_int = int(n_resolved or 0)
            conn.commit()
        finally:
            conn.close()

    if n_resolved_int > 0 and n_resolved_int % REPORT_EVERY_RESOLVED == 0:
        try:
            write_learning_report()
        except Exception:
            pass


def write_learning_report() -> dict[str, Any]:
    """Brier, acurácia, ROI proxy de value bets e pesos atuais → ``learning_report.json``."""
    d = detect_drift()
    vw: list[Any] = []
    w = list(DEFAULT_WEIGHTS)
    margin = 0.05
    err_ema = 0.0
    n_all = 0
    with _lock:
        conn = _ensure_db()
        try:
            w = list(_load_weights(conn))
            margin = _load_float_meta(conn, "value_edge_margin", 0.05)
            err_ema = _load_float_meta(conn, "error_rate_ema", 0.0)
            raw_v = _meta_get(conn, "value_bet_window_json", "[]")
            try:
                vw = json.loads(raw_v) if raw_v else []
            except json.JSONDecodeError:
                vw = []
            row_n = conn.execute(
                "SELECT COUNT(*) AS c FROM predictions_log WHERE result IS NOT NULL"
            ).fetchone()
            n_all = int(row_n["c"] if row_n else 0)
        finally:
            conn.close()

    roi_proxy = None
    if vw:
        wins = sum(1 for x in vw if x.get("won"))
        roi_proxy = {"n": len(vw), "win_rate": round(wins / len(vw), 4) if vw else None}

    report = {
        "generated_ts": time.time(),
        "weights": [round(x, 5) for x in w],
        "value_edge_margin": round(margin, 4),
        "error_rate_ema": round(err_ema, 6),
        "resolved_predictions": int(n_all),
        "drift": d,
        "value_window": roi_proxy,
    }
    path = learning_data_dir() / "learning_report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def maybe_write_learning_report_after_resolved() -> None:
    """Pode ser chamado explicitamente; o fluxo normal já tenta escrever a cada N resultados."""
    write_learning_report()
