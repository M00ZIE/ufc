"""
Fase 7 — gestão de bankroll, orçamentos de risco e MC de portefólio (card correlacionado).

Estado persistido em SQLite (mesma BD que ``learning``) + meta chaves ``phase7_*``.
Interpretável; limites conservadores por defeito.
"""

from __future__ import annotations

import hashlib
import math
import random
import sqlite3
import threading
import time
from typing import Any, Literal, Optional

from mma_predict.learning import learning_data_dir

RiskBudgetStatus = Literal["ok", "limited", "blocked"]

_lock = threading.Lock()

# Limites normalizados (bankroll = 1.0)
DEFAULT_EVENT_EXPOSURE_CAP = 0.18
DEFAULT_REGIME_EXPOSURE_CAP = 0.10
DEFAULT_CONTEXT_EXPOSURE_CAP = 0.08
DISAGREEMENT_BLOCK = 0.58
DEFAULT_CARD_CORRELATION = 0.22

META_BANKROLL = "phase7_bankroll_norm"
META_PEAK = "phase7_equity_peak_norm"
META_MAX_DD = "phase7_max_drawdown_seen"
META_NEG_ROI_STREAK = "phase7_neg_roi_streak"


def _conn() -> sqlite3.Connection:
    path = learning_data_dir() / "predictions_store.sqlite"
    c = sqlite3.connect(str(path), timeout=30.0)
    c.row_factory = sqlite3.Row
    return c


def init_phase7_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bankroll_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts REAL NOT NULL,
            bankroll_value REAL NOT NULL,
            exposure REAL NOT NULL,
            drawdown REAL NOT NULL,
            event_id TEXT
        );
        CREATE TABLE IF NOT EXISTS risk_budget_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts REAL NOT NULL,
            budget_type TEXT NOT NULL,
            usage REAL NOT NULL,
            limit_val REAL NOT NULL,
            event_id TEXT
        );
        CREATE TABLE IF NOT EXISTS equity_curve_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts REAL NOT NULL,
            bankroll_value REAL NOT NULL,
            roi REAL,
            drawdown REAL NOT NULL,
            rolling_sharpe_proxy REAL,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bankroll_log_ts ON bankroll_log(created_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_equity_curve_ts ON equity_curve_log(created_ts DESC);
        """
    )


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


def event_id_from_url(event_url: str) -> str:
    h = hashlib.sha256(event_url.strip().encode("utf-8")).hexdigest()
    return h[:24]


def _ensure_meta_defaults(conn: sqlite3.Connection) -> None:
    init_phase7_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_meta (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """
    )
    if _meta_get(conn, META_BANKROLL) is None:
        _meta_set(conn, META_BANKROLL, "1.0")
    if _meta_get(conn, META_PEAK) is None:
        _meta_set(conn, META_PEAK, "1.0")
    if _meta_get(conn, META_MAX_DD) is None:
        _meta_set(conn, META_MAX_DD, "0.0")
    if _meta_get(conn, META_NEG_ROI_STREAK) is None:
        _meta_set(conn, META_NEG_ROI_STREAK, "0")


def load_portfolio_norms(conn: sqlite3.Connection) -> dict[str, float]:
    _ensure_meta_defaults(conn)
    try:
        b = float(_meta_get(conn, META_BANKROLL) or "1.0")
    except ValueError:
        b = 1.0
    try:
        peak = float(_meta_get(conn, META_PEAK) or str(max(b, 1.0)))
    except ValueError:
        peak = max(b, 1.0)
    try:
        max_dd = float(_meta_get(conn, META_MAX_DD) or "0.0")
    except ValueError:
        max_dd = 0.0
    try:
        streak = int(float(_meta_get(conn, META_NEG_ROI_STREAK) or "0"))
    except ValueError:
        streak = 0
    peak = max(peak, b, 1e-9)
    dd = max(0.0, (peak - b) / peak)
    return {
        "bankroll_norm": max(0.05, min(50.0, b)),
        "equity_peak_norm": peak,
        "current_drawdown": dd,
        "max_drawdown_seen": max_dd,
        "neg_roi_streak": float(streak),
    }


def current_drawdown_readonly() -> float:
    """Drawdown atual 0–1 (aproximação via meta)."""
    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            d = load_portfolio_norms(conn)
            return float(d["current_drawdown"])
        finally:
            conn.close()


def equity_gradient_signal_readonly() -> float:
    """
    Declive normalizado dos últimos pontos da curva de equity (-1 a +1).
    Positivo = recuperação recente; negativo = pressão descendente.
    """
    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            rows = conn.execute(
                """
                SELECT bankroll_value, created_ts
                FROM equity_curve_log
                ORDER BY created_ts DESC, id DESC
                LIMIT 14
                """
            ).fetchall()
        finally:
            conn.close()
    if len(rows) < 3:
        return 0.0
    vals = [float(r["bankroll_value"]) for r in reversed(rows)]
    n = len(vals)
    mean_v = sum(vals) / n
    var = sum((v - mean_v) ** 2 for v in vals) / max(1, n - 1)
    if var < 1e-12:
        return 0.0
    # declive simples primeiro vs último, normalizado pela volatilidade amostral
    slope = (vals[-1] - vals[0]) / max(1e-6, mean_v)
    noise = math.sqrt(var) / max(1e-6, mean_v)
    return float(max(-1.0, min(1.0, 0.5 * slope / max(0.08, noise))))


def log_risk_budget_event(
    conn: sqlite3.Connection,
    *,
    budget_type: str,
    usage: float,
    limit_val: float,
    event_id: str | None,
) -> None:
    init_phase7_tables(conn)
    conn.execute(
        """
        INSERT INTO risk_budget_log (created_ts, budget_type, usage, limit_val, event_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (time.time(), budget_type[:32], float(usage), float(limit_val), event_id),
    )


def fractional_kelly_cap(
    *,
    volatility: float,
    stress_test_score: float,
    neg_roi_streak: float,
    regime: str,
) -> float:
    """Teto dinâmico estilo Kelly fraccionado (0.22–0.5)."""
    cap = 0.42
    cap *= 1.0 - 0.38 * max(0.0, min(1.0, float(stress_test_score)))
    cap *= 1.0 - 0.12 * min(1.0, float(volatility) / 12.0)
    cap *= max(0.28, 1.0 - 0.055 * max(0.0, float(neg_roi_streak)))
    if regime == "chaotic_card":
        cap *= 0.74
    elif regime == "stable_card":
        cap *= 1.05
    return float(max(0.22, min(0.5, cap)))


def portfolio_stake_after_drawdown(
    raw_stake: float,
    *,
    current_drawdown: float,
    equity_gradient: float,
) -> float:
    """Reduz exposição sob drawdown; ligeiro boost se gradiente de equity for positivo."""
    dd = max(0.0, min(0.85, float(current_drawdown)))
    mult = 1.0 / (1.0 + 2.8 * dd)
    mult *= 1.0 + 0.08 * max(0.0, float(equity_gradient))
    mult *= max(0.35, 1.0 + 0.12 * min(0.0, float(equity_gradient)))
    return float(max(0.0, min(0.12, raw_stake * mult)))


def evaluate_risk_budgets(
    *,
    disagreement: float,
    regime: str,
    context_bucket: str,
    prior_event_stake: float,
    proposed_stake: float,
    event_cap: float = DEFAULT_EVENT_EXPOSURE_CAP,
    regime_cap: float = DEFAULT_REGIME_EXPOSURE_CAP,
    context_cap: float = DEFAULT_CONTEXT_EXPOSURE_CAP,
    conn: sqlite3.Connection | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """
    Orçamentos: evento, regime, contexto (uso lido em meta) + bloqueio por desacordo.
    ``proposed_stake`` = fração 0–1 do bankroll.
    """
    own_conn = conn is None
    if own_conn:
        c = _conn()
        try:
            init_phase7_tables(c)
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            return evaluate_risk_budgets(
                disagreement=disagreement,
                regime=regime,
                context_bucket=context_bucket,
                prior_event_stake=prior_event_stake,
                proposed_stake=proposed_stake,
                event_cap=event_cap,
                regime_cap=regime_cap,
                context_cap=context_cap,
                conn=c,
                event_id=event_id,
            )
        finally:
            c.close()

    assert conn is not None
    init_phase7_tables(conn)
    _ensure_meta_defaults(conn)

    reasons: list[str] = []
    status: RiskBudgetStatus = "ok"
    stake_out = float(max(0.0, proposed_stake))
    per_bet_cap = min(float(regime_cap), float(context_cap))
    _ = context_bucket  # reservado para orçamentos por bucket no futuro

    if float(disagreement) >= DISAGREEMENT_BLOCK:
        status = "blocked"
        reasons.append("model_disagreement_budget")
        stake_out = 0.0
        if event_id:
            log_risk_budget_event(
                conn,
                budget_type="disagreement",
                usage=float(disagreement),
                limit_val=DISAGREEMENT_BLOCK,
                event_id=event_id,
            )
        return {
            "status": status,
            "stake_after_budget": stake_out,
            "reasons": reasons,
            "event_usage": float(prior_event_stake),
            "regime_usage": 0.0,
            "context_usage": 0.0,
            "per_bet_cap": float(per_bet_cap),
        }

    ev_rem = max(0.0, float(event_cap) - float(prior_event_stake))
    # Tetos por aposta (regime + contexto); evento é cumulativo no card.
    hard_cap = min(ev_rem, per_bet_cap)
    if stake_out > hard_cap + 1e-9:
        if hard_cap < 0.002:
            status = "blocked"
            reasons.append("exposure_cap_exhausted")
            stake_out = 0.0
        else:
            status = "limited"
            reasons.append("exposure_scaled")
            stake_out = min(stake_out, hard_cap)
        if event_id and status != "ok":
            log_risk_budget_event(
                conn,
                budget_type="event_regime_context",
                usage=float(prior_event_stake + proposed_stake),
                limit_val=float(event_cap),
                event_id=event_id,
            )

    return {
        "status": status,
        "stake_after_budget": float(max(0.0, stake_out)),
        "reasons": reasons,
        "event_usage": float(prior_event_stake),
        "regime_usage": 0.0,
        "context_usage": 0.0,
        "per_bet_cap": float(per_bet_cap),
    }


def monte_carlo_portfolio_correlated(
    probs_red: list[float],
    *,
    stake_fraction_per_fight: float,
    decimal_odds: float = 1.88,
    bet_red: list[bool] | None = None,
    correlation: float = DEFAULT_CARD_CORRELATION,
    simulations: int = 3500,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    Simula PnL do card com Bernoullis correlacionadas (mistura comum / independente).

    Cada luta: aposta no lado escolhido (vermelho por defeito se ``bet_red`` None).
    """
    rng = random.Random(seed)
    ps = [max(0.03, min(0.97, float(p))) for p in probs_red]
    if not ps:
        return {
            "expected_bankroll_growth": 0.0,
            "worst_case_drawdown": 0.0,
            "volatility_of_equity_curve": 0.0,
            "simulations": 0,
            "n_legs": 0,
        }
    br = bet_red if bet_red is not None else [True] * len(ps)
    s = max(0.0, min(0.12, float(stake_fraction_per_fight)))
    d = max(1.01, float(decimal_odds))
    rho = max(0.0, min(0.85, float(correlation)))
    n = max(400, int(simulations))
    finals: list[float] = []
    worst_mins: list[float] = []

    for _ in range(n):
        equity = 1.0
        min_eq = equity
        if rng.random() < rho:
            u = rng.random()
            wins = []
            for i, p in enumerate(ps):
                p_side = p if br[i] else 1.0 - p
                wins.append(1 if u < p_side else 0)
        else:
            wins = []
            for i, p in enumerate(ps):
                p_side = p if br[i] else 1.0 - p
                wins.append(1 if rng.random() < p_side else 0)
        for w in wins:
            pnl = s * (d - 1.0) if w else -s
            equity += pnl
            min_eq = min(min_eq, equity)
        finals.append(equity)
        worst_mins.append(min_eq - 1.0)

    mean_f = sum(finals) / len(finals)
    growth = mean_f - 1.0
    vol = _std_dev(finals)
    wdd = min(worst_mins) if worst_mins else 0.0
    return {
        "expected_bankroll_growth": round(float(growth), 5),
        "worst_case_drawdown": round(float(wdd), 5),
        "volatility_of_equity_curve": round(float(vol), 5),
        "simulations": n,
        "n_legs": len(ps),
    }


def _std_dev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(max(0.0, v))


def build_phase7_payload(
    *,
    base_stake_rl: float,
    action_rl: str,
    edge: float,
    confidence: float,
    volatility: float,
    stress_test_score: float,
    vulnerability_index: float,
    regime: str,
    context_bucket: str,
    disagreement: float,
    final_prob_red: float,
    decimal_odds: float,
    peer_final_probs: list[float],
    prior_event_stake: float,
    event_url: str | None,
    fight_index: int | None,
    event_total_fights: int | None,
) -> dict[str, Any]:
    """Agrega stake, orçamentos, MC de card e métricas de risco para a API."""
    _ = (action_rl, fight_index, event_total_fights)
    probs = [float(x) for x in peer_final_probs] + [float(final_prob_red)]
    bet_red_side = [p >= 0.5 for p in probs]
    eid = event_id_from_url(event_url) if event_url else None

    norms: dict[str, float] = {}
    budget: dict[str, Any] = {}
    rec = 0.0
    eg = 0.0
    kcap = 0.38
    raw = 0.0

    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            norms = load_portfolio_norms(conn)
            eg = _equity_grad_unlocked(conn)
            kcap = fractional_kelly_cap(
                volatility=volatility,
                stress_test_score=stress_test_score,
                neg_roi_streak=norms["neg_roi_streak"],
                regime=regime,
            )
            raw = float(max(0.0, base_stake_rl * kcap))
            raw = portfolio_stake_after_drawdown(
                raw,
                current_drawdown=norms["current_drawdown"],
                equity_gradient=eg,
            )
            budget = evaluate_risk_budgets(
                disagreement=disagreement,
                regime=regime,
                context_bucket=context_bucket,
                prior_event_stake=prior_event_stake,
                proposed_stake=raw,
                conn=conn,
                event_id=eid,
            )
            rec = float(budget["stake_after_budget"])
            conn.commit()
        finally:
            conn.close()

    port_mc = monte_carlo_portfolio_correlated(
        probs,
        stake_fraction_per_fight=rec if rec > 1e-8 else max(1e-5, min(0.02, raw * 0.4)),
        decimal_odds=decimal_odds,
        bet_red=bet_red_side,
        correlation=DEFAULT_CARD_CORRELATION
        * (1.0 + 0.35 * max(0.0, min(1.0, float(vulnerability_index)))),
        simulations=3200,
        seed=None,
    )

    dd_risk = float(
        max(
            norms["current_drawdown"],
            -float(port_mc["worst_case_drawdown"]),
            0.35 * float(stress_test_score),
        )
    )
    dd_risk = max(0.0, min(1.0, dd_risk))

    exp_impact = float(rec * (max(0.0, edge) * (decimal_odds - 1.0) - (1.0 - max(0.0, min(1.0, confidence))) * 0.15))

    exposure_after = float(prior_event_stake + rec)

    return {
        "recommended_stake": round(rec, 5),
        "bankroll_exposure": round(exposure_after, 5),
        "risk_budget_status": str(budget["status"]),
        "expected_bankroll_impact": round(exp_impact, 5),
        "drawdown_risk": round(dd_risk, 4),
        "equity_gradient_signal": round(eg, 4),
        "fractional_kelly_cap": round(kcap, 4),
        "portfolio_mc": port_mc,
        "risk_budget_detail": {
            "reasons": list(budget["reasons"]),
            "event_usage": budget["event_usage"],
            "policy_override": "portfolio_risk_budget"
            if budget["status"] == "blocked"
            else None,
        },
        "stake_rl_pre_portfolio": round(float(base_stake_rl), 5),
    }


def _equity_grad_unlocked(conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        """
        SELECT bankroll_value FROM equity_curve_log
        ORDER BY created_ts DESC, id DESC LIMIT 14
        """
    ).fetchall()
    if len(rows) < 3:
        return 0.0
    vals = [float(r["bankroll_value"]) for r in reversed(rows)]
    n = len(vals)
    mean_v = sum(vals) / n
    var = sum((v - mean_v) ** 2 for v in vals) / max(1, n - 1)
    if var < 1e-12:
        return 0.0
    slope = (vals[-1] - vals[0]) / max(1e-6, mean_v)
    noise = math.sqrt(var) / max(1e-6, mean_v)
    return float(max(-1.0, min(1.0, 0.5 * slope / max(0.08, noise))))


def append_bankroll_snapshot(
    *,
    bankroll_value: float,
    exposure: float,
    drawdown: float,
    event_id: str | None = None,
) -> None:
    """Registo auditável (opcional, chamado no fim do card ou após liquidação)."""
    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                INSERT INTO bankroll_log (created_ts, bankroll_value, exposure, drawdown, event_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (time.time(), float(bankroll_value), float(exposure), float(drawdown), event_id),
            )
            conn.commit()
        finally:
            conn.close()


def append_equity_point(
    *,
    bankroll_value: float,
    roi: Optional[float],
    drawdown: float,
    rolling_sharpe_proxy: Optional[float] = None,
    notes: str | None = None,
) -> None:
    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                INSERT INTO equity_curve_log (
                    created_ts, bankroll_value, roi, drawdown, rolling_sharpe_proxy, notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    float(bankroll_value),
                    float(roi) if roi is not None else None,
                    float(drawdown),
                    float(rolling_sharpe_proxy) if rolling_sharpe_proxy is not None else None,
                    notes,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def maybe_log_event_completion(
    *,
    event_url: str,
    cumulative_stake_fraction: float,
    bankroll_norm: float | None = None,
) -> None:
    """Útil no fim de ``analyze_event_json``: um ponto na curva de equity."""
    eid = event_id_from_url(event_url)
    ts = time.time()
    with _lock:
        conn = _conn()
        try:
            init_phase7_tables(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            norms = load_portfolio_norms(conn)
            b = float(bankroll_norm) if bankroll_norm is not None else float(norms["bankroll_norm"])
            dd = float(norms["current_drawdown"])
            conn.execute(
                """
                INSERT INTO equity_curve_log (
                    created_ts, bankroll_value, roi, drawdown, rolling_sharpe_proxy, notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, b, None, dd, None, f"event_close:{eid}"),
            )
            conn.execute(
                """
                INSERT INTO bankroll_log (created_ts, bankroll_value, exposure, drawdown, event_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, b, float(cumulative_stake_fraction), dd, eid),
            )
            conn.commit()
        finally:
            conn.close()
