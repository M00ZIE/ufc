"""
Política RL leve (heurística + bandit online): ação de aposta simulada e fração de stake.

Sem rede neuronal — interpretável, estável, atualização incremental após resultados.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Literal, Optional

from mma_predict.learning import learning_data_dir
from mma_predict import meta_ensemble as me

Action = Literal["SKIP", "BET_LOW", "BET_MEDIUM", "BET_HIGH"]

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    path = learning_data_dir() / "predictions_store.sqlite"
    c = sqlite3.connect(str(path), timeout=30.0)
    c.row_factory = sqlite3.Row
    return c


def init_rl_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rl_policy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fight_id TEXT,
            state_snapshot TEXT NOT NULL,
            action TEXT NOT NULL,
            reward REAL,
            roi_outcome REAL,
            created_ts REAL NOT NULL
        )
        """
    )


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT v FROM learning_meta WHERE k = ?", (key,)).fetchone()
    return str(row["v"]) if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO learning_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, value),
    )


def load_policy_scores(conn: sqlite3.Connection, context_key: str) -> dict[str, float]:
    raw = _meta_get(conn, f"rl_scores:{context_key}")
    if not raw:
        return {"SKIP": 0.0, "BET_LOW": 0.0, "BET_MEDIUM": 0.0, "BET_HIGH": 0.0}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return {str(k): float(v) for k, v in d.items() if k in ("SKIP", "BET_LOW", "BET_MEDIUM", "BET_HIGH")}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"SKIP": 0.0, "BET_LOW": 0.0, "BET_MEDIUM": 0.0, "BET_HIGH": 0.0}


def save_policy_scores(conn: sqlite3.Connection, context_key: str, scores: dict[str, float]) -> None:
    _meta_set(conn, f"rl_scores:{context_key}", json.dumps(scores))


def policy_risk_factor(action: str) -> float:
    """>1 alarga MC quando a política escolhe exposição maior."""
    return {
        "SKIP": 0.92,
        "BET_LOW": 1.0,
        "BET_MEDIUM": 1.08,
        "BET_HIGH": 1.18,
    }.get(action, 1.0)


def select_action(
    *,
    final_prob: float,
    edge_max: float,
    confidence: float,
    agreement: float,
    regime: str,
    volatility: float,
    adversarial_risk: float,
    roi_context: str,
    has_odds_edge: bool,
    vulnerability_index: float = 0.0,
) -> tuple[Action, float]:
    """
    Escolhe ação e «valor esperado» heurístico (não EV de mercado completo).
    ``adversarial_risk`` = stress_test_score (0–1).
    """
    conn = _conn()
    try:
        init_rl_tables(conn)
        me.init_ensemble_tables(conn)
        roi_mean = me.mean_roi_context(conn, roi_context, limit=40)
    finally:
        conn.close()

    conf = max(0.0, min(1.0, float(confidence)))
    agr = max(0.0, min(1.0, float(agreement)))
    edge = max(0.0, float(edge_max))
    ar = max(0.0, min(1.0, float(adversarial_risk)))
    vuln = max(0.0, min(1.0, float(vulnerability_index)))
    voln = min(1.0, float(volatility) / 10.0)

    if vuln >= 0.85 or ar >= 0.88:
        return "SKIP", 0.0
    if regime == "chaotic_card" and ar >= 0.78 and vuln >= 0.62:
        return "SKIP", 0.0

    score_base = edge * (0.45 + 0.55 * conf) * (0.4 + 0.6 * agr) * (1.0 - 0.55 * ar) * (1.0 - 0.18 * voln)
    if roi_mean is not None and roi_mean > 0:
        score_base *= 1.0 + min(0.2, roi_mean * 2.0)
    if roi_mean is not None and roi_mean < 0:
        score_base *= 1.0 + max(-0.25, roi_mean * 3.0)

    if regime == "chaotic_card":
        score_base *= 0.55
    if not has_odds_edge:
        return "SKIP", 0.0

    if score_base < 0.008:
        return "SKIP", round(score_base, 5)
    if score_base < 0.022:
        return "BET_LOW", round(score_base + 0.01, 5)
    if score_base < 0.04:
        return "BET_MEDIUM", round(score_base + 0.02, 5)
    return "BET_HIGH", round(score_base + 0.03, 5)


def stake_fraction_kelly_like(
    action: str,
    *,
    edge: float,
    confidence: float,
    volatility: float,
    adversarial_risk: float,
    regime: str,
    vulnerability_index: float,
) -> float:
    """Fração 0–0.08 do bankroll sugerida (simulação educativa)."""
    if action == "SKIP":
        return 0.0
    k = edge * (2.0 * confidence - 1.0)
    k = max(0.0, min(0.06, k * 0.5))
    mult = {"BET_LOW": 0.45, "BET_MEDIUM": 0.72, "BET_HIGH": 1.0}.get(action, 0.0)
    k *= mult
    k *= 1.0 - 0.4 * adversarial_risk - 0.25 * min(1.0, vulnerability_index)
    if regime == "chaotic_card":
        k *= 0.35
    elif regime == "stable_card":
        k *= 1.08
    k *= 1.0 - 0.12 * min(1.0, float(volatility) / 10.0)
    return float(max(0.0, min(0.08, k)))


def expected_roi_proxy(
    edge: float,
    confidence: float,
    agreement: float,
    adversarial_risk: float,
    stress_test_score: float,
) -> float:
    """Proxy de ROI esperado para API (não substitui EV de mercado)."""
    return float(
        max(-0.35, min(0.35, edge * (0.5 + confidence) * agreement - 0.22 * adversarial_risk - 0.12 * stress_test_score))
    )


def compute_reward(
    *,
    profit: float,
    volatility: float,
    calibration_error: float,
    action_consistency: float,
    equity_gradient_signal: float = 0.0,
) -> float:
    """reward = profit - drawdown_penalty - vol_penalty + calib + consistency (+ sinal de equity)."""
    drawdown_penalty = max(0.0, -profit) * 1.15
    vol_penalty = min(0.08, float(volatility) / 120.0)
    calib_bonus = max(0.0, 0.04 - abs(float(calibration_error))) * 0.5
    cons_bonus = 0.03 * max(0.0, min(1.0, float(action_consistency)))
    eq = 0.018 * max(-1.0, min(1.0, float(equity_gradient_signal)))
    return float(profit - drawdown_penalty - vol_penalty + calib_bonus + cons_bonus + eq)


def log_rl_transition(
    conn: sqlite3.Connection,
    *,
    fight_id: str,
    state: dict[str, Any],
    action: str,
    reward: Optional[float],
    roi_outcome: Optional[float],
) -> None:
    init_rl_tables(conn)
    conn.execute(
        """
        INSERT INTO rl_policy_log (fight_id, state_snapshot, action, reward, roi_outcome, created_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            fight_id,
            json.dumps(state, ensure_ascii=False),
            action,
            reward,
            roi_outcome,
            time.time(),
        ),
    )


def update_policy_from_reward(
    conn: sqlite3.Connection,
    context_key: str,
    action: str,
    reward: float,
    *,
    alpha: float = 0.08,
) -> None:
    scores = load_policy_scores(conn, context_key)
    if action not in scores:
        return
    scores[action] = (1.0 - alpha) * scores[action] + alpha * float(reward)
    save_policy_scores(conn, context_key, scores)


def process_rl_outcome(
    conn: sqlite3.Connection,
    *,
    fight_id: str,
    y_red: int,
    phase5_snapshot_json: Optional[str],
    model_prob_red: float,
    volatility: float,
) -> None:
    """Após resultado: recompensa da política e atualização incremental."""
    if not phase5_snapshot_json:
        return
    try:
        snap = json.loads(phase5_snapshot_json)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(snap, dict):
        return
    action = str(snap.get("action") or "SKIP")
    ctx = str(snap.get("roi_context") or "general")
    stake = float(snap.get("stake_fraction") or 0.0)
    edge = float(snap.get("edge_max") or 0.0)
    dec = float(snap.get("decimal_odds_taken") or 0.0)
    side = str(snap.get("bet_side") or "red")
    won = (side == "red" and y_red == 1) or (side == "blue" and y_red == 0)
    if stake <= 0 or action == "SKIP" or dec <= 1.0:
        profit = 0.0
        roi_o = 0.0
    else:
        profit = stake * (dec - 1.0) if won else -stake
        roi_o = profit / max(1e-6, stake)
    calib_err = float(model_prob_red) - float(y_red)
    try:
        from mma_predict.bankroll import equity_gradient_signal_readonly

        eg = float(equity_gradient_signal_readonly())
    except Exception:
        eg = 0.0
    reward = compute_reward(
        profit=profit,
        volatility=volatility,
        calibration_error=calib_err,
        action_consistency=1.0 if (action != "SKIP" and edge > 0) else 0.3,
        equity_gradient_signal=eg,
    )
    log_rl_transition(
        conn,
        fight_id=fight_id,
        state=snap,
        action=action,
        reward=reward,
        roi_outcome=roi_o,
    )
    update_policy_from_reward(conn, ctx, action, reward)
