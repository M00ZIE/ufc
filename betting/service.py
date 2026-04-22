"""Regras de negócio: registro, login, apostas, liquidação, admin."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import bcrypt

from betting.db import init_schema, row_to_dict
from betting.odds_math import extract_probs_from_fight_row, odds_pair_from_probs_vig
from betting.parlay_math import combined_decimal, compute_leg, leg_wins_against_result
from mma_predict.simulation import simulate_parlay
from sports import get_analyzer
from sports.ufc_urls import allowed_ufc_event_url

INITIAL_CREDITS = 1000
# Limite educativo: stake máximo = fração do saldo (créditos)
STAKE_MAX_FRACTION = 0.20
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_ADMIN_EMAIL = "astrosoprime@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Senha1337#"


def ensure_default_admin(conn: sqlite3.Connection) -> None:
    """Garante conta admin inicial (email/senha fixos na primeira execução)."""
    email = DEFAULT_ADMIN_EMAIL.strip().lower()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        conn.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
        conn.commit()
        return
    ph = _hash_password(DEFAULT_ADMIN_PASSWORD)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, balance, is_admin, blocked) VALUES (?, ?, ?, 1, 0)",
        (email, ph, INITIAL_CREDITS),
    )
    uid = cur.lastrowid
    conn.execute(
        """INSERT INTO transactions (user_id, amount, balance_after, type, note)
           VALUES (?, ?, ?, ?, ?)""",
        (uid, INITIAL_CREDITS, INITIAL_CREDITS, "initial_credit", "Saldo inicial (demo) — admin"),
    )
    conn.commit()


def _admin_emails() -> set[str]:
    raw = os.environ.get("BETTING_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


@dataclass
class ServiceError(Exception):
    code: str
    message: str
    http_status: int = 400


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def register_user(conn: sqlite3.Connection, email: str, password: str) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ServiceError("invalid_email", "Email inválido.", 400)
    if len(password) < 8:
        raise ServiceError("weak_password", "Senha deve ter pelo menos 8 caracteres.", 400)

    ph = _hash_password(password)
    is_admin = 1 if email in _admin_emails() else 0
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, balance, is_admin) VALUES (?, ?, ?, ?)",
            (email, ph, INITIAL_CREDITS, is_admin),
        )
        uid = cur.lastrowid
        conn.execute(
            """INSERT INTO transactions (user_id, amount, balance_after, type, note)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, INITIAL_CREDITS, INITIAL_CREDITS, "initial_credit", "Saldo inicial (demo)"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ServiceError("email_taken", "Este email já está cadastrado.", 409)
    return {
        "id": uid,
        "email": email,
        "balance": INITIAL_CREDITS,
        "is_admin": bool(is_admin),
    }


def login_user(conn: sqlite3.Connection, email: str, password: str) -> dict[str, Any]:
    email = (email or "").strip().lower()
    row = conn.execute(
        "SELECT id, email, balance, password_hash, blocked, is_admin FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        raise ServiceError("auth_failed", "Email ou senha incorretos.", 401)
    if row["blocked"]:
        raise ServiceError("blocked", "Conta suspensa. Contate o suporte.", 403)
    return {
        "id": row["id"],
        "email": row["email"],
        "balance": row["balance"],
        "is_admin": bool(row["is_admin"]),
    }


def get_user(conn: sqlite3.Connection, user_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT id, email, balance, is_admin, blocked, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    d["is_admin"] = bool(d.get("is_admin"))
    d["blocked"] = bool(d.get("blocked"))
    return d


def max_stake_allowed(balance: int) -> int:
    """Máximo de créditos por aposta (20% do saldo, arredondado para baixo)."""
    b = max(0, int(balance))
    return max(0, int(b * STAKE_MAX_FRACTION))


def _load_ufc_fights(event_url: str, cache_dir: Path) -> list[dict[str, Any]]:
    if not allowed_ufc_event_url(event_url):
        raise ServiceError("bad_event_url", "URL do evento não permitida.", 400)
    analyzer = get_analyzer("ufc")
    data = analyzer.analyze(
        event_url,
        cache_dir=cache_dir,
        cache_hours=24.0,
        refresh=False,
    )
    if not data.get("ok"):
        err = (data.get("errors") or ["Análise indisponível"])[0]
        raise ServiceError("analyze_failed", err, 502)
    fights = data.get("fights") or []
    return fights


def odds_for_event(event_url: str, cache_dir: Path) -> dict[str, Any]:
    """Lista lutas com probabilidades e odds (vig + risco)."""
    fights = _load_ufc_fights(event_url, cache_dir)
    out = []
    favorite_leg_probs: list[float] = []
    for i, f in enumerate(fights, start=1):
        if f.get("error"):
            out.append({"index": i, "error": f.get("error"), "division": f.get("division")})
            continue
        pr, pb, risk = extract_probs_from_fight_row(f)
        favorite_leg_probs.append(max(float(pr), float(pb)))
        pkg = odds_pair_from_probs_vig(pr, pb, risk)
        r = f.get("red") or {}
        b = f.get("blue") or {}
        row: dict[str, Any] = {
            "index": i,
            "division": f.get("division"),
            "red_name": r.get("name"),
            "blue_name": b.get("name"),
            "prob_red": pkg["prob_red"],
            "prob_blue": pkg["prob_blue"],
            "decimal_odds_red": pkg.get("decimal_odds_red"),
            "decimal_odds_blue": pkg.get("decimal_odds_blue"),
            "betting_blocked": pkg.get("betting_blocked", False),
            "vig": pkg.get("vig"),
            "risk_tier": pkg.get("risk_tier"),
        }
        out.append(row)

    parlay_analysis: dict[str, Any] = {
        "combined_odds": None,
        "real_hit_rate": None,
        "edge": None,
        "naive_probability": None,
        "n_legs": len(favorite_leg_probs),
        "simulations": None,
    }
    if len(favorite_leg_probs) >= 2:
        sim = simulate_parlay(favorite_leg_probs, simulations=10000)
        naive = sim.get("combined_probability_naive")
        hit = float(sim.get("hit_rate") or 0.0)
        parlay_analysis["real_hit_rate"] = round(hit, 4)
        parlay_analysis["simulations"] = sim.get("simulations")
        if naive is not None:
            parlay_analysis["naive_probability"] = round(float(naive), 6)
            if float(naive) > 1e-12:
                parlay_analysis["combined_odds"] = round(1.0 / float(naive), 2)
            parlay_analysis["edge"] = round(hit - float(naive), 4)
    return {"ok": True, "event_url": event_url, "fights": out, "parlay_analysis": parlay_analysis}


def place_bet(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    event_url: str,
    fight_index: int,
    side: str,
    stake: int,
    cache_dir: Path,
) -> dict[str, Any]:
    side = (side or "").strip().lower()
    if side not in ("red", "blue"):
        raise ServiceError("bad_side", "Lado deve ser «red» ou «blue».", 400)
    if fight_index < 1:
        raise ServiceError("bad_index", "Índice da luta inválido.", 400)
    if stake < 1:
        raise ServiceError("bad_stake", "Aposta mínima: 1 crédito.", 400)

    urow = conn.execute(
        "SELECT balance, blocked FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not urow:
        raise ServiceError("no_user", "Usuário não encontrado.", 404)
    if urow["blocked"]:
        raise ServiceError("blocked", "Conta suspensa.", 403)

    balance = int(urow["balance"])
    if stake > balance:
        raise ServiceError("insufficient_balance", "Saldo insuficiente.", 400)
    cap = max_stake_allowed(balance)
    if stake > cap:
        raise ServiceError(
            "stake_over_limit",
            f"Stake máximo: {cap} créditos (20% do saldo).",
            400,
        )

    fights = _load_ufc_fights(event_url, cache_dir)
    if fight_index > len(fights):
        raise ServiceError("fight_not_found", "Luta não existe neste evento.", 400)
    f = fights[fight_index - 1]
    if f.get("error"):
        raise ServiceError("fight_error", "Esta luta não pôde ser analisada.", 400)

    pr, pb, risk = extract_probs_from_fight_row(f)
    pkg = odds_pair_from_probs_vig(pr, pb, risk)
    if pkg.get("betting_blocked"):
        raise ServiceError(
            "betting_closed",
            "Mercado fechado para esta luta (SKIP — modelo sem confiança suficiente).",
            400,
        )
    or_red = float(pkg["decimal_odds_red"])
    or_blue = float(pkg["decimal_odds_blue"])
    odds_taken = or_red if side == "red" else or_blue
    prn = float(pkg["prob_red"])
    pbn = float(pkg["prob_blue"])

    r = f.get("red") or {}
    b = f.get("blue") or {}

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ? AND blocked = 0",
            (stake, user_id, stake),
        )
        if conn.total_changes != 1:
            conn.rollback()
            raise ServiceError("insufficient_balance", "Saldo insuficiente.", 400)
        new_bal = balance - stake
        cur = conn.execute(
            """INSERT INTO bets (
            user_id, event_url, fight_index, division, red_name, blue_name,
            side, stake, odds_red, odds_blue, odds_taken, prob_red, prob_blue,
            risk_tier, status
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (
                user_id,
                event_url,
                fight_index,
                f.get("division"),
                r.get("name"),
                b.get("name"),
                side,
                stake,
                or_red,
                or_blue,
                odds_taken,
                prn,
                pbn,
                risk,
            ),
        )
        bet_id = cur.lastrowid
        conn.execute(
            """INSERT INTO transactions (user_id, amount, balance_after, type, bet_id, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, -stake, new_bal, "bet_stake", bet_id, f"Aposta luta #{fight_index} ({side})"),
        )
        conn.commit()
    except ServiceError:
        raise
    except Exception:
        conn.rollback()
        raise

    return {
        "ok": True,
        "bet_id": bet_id,
        "balance": new_bal,
        "odds_taken": odds_taken,
        "payout_if_win": int(round(stake * odds_taken)),
        "side": side,
        "fight_index": fight_index,
        "max_bet_next": max_stake_allowed(new_bal),
        "vig_applied": pkg.get("vig"),
    }


def list_user_bets(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    lim = max(1, min(200, limit))
    rows = conn.execute(
        """SELECT * FROM bets WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
        (user_id, lim),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def _learning_record_outcome(event_url: str, fight_index: int, winner_side: str) -> None:
    try:
        from mma_predict.learning import record_fight_outcome

        record_fight_outcome(event_url, fight_index, red_won=(winner_side == "red"))
    except Exception:
        pass


def settle_fight_bets(
    conn: sqlite3.Connection,
    *,
    event_url: str,
    fight_index: int,
    winner_side: str,
) -> dict[str, Any]:
    winner_side = (winner_side or "").strip().lower()
    if winner_side not in ("red", "blue"):
        raise ServiceError("bad_winner", "Vencedor deve ser «red» ou «blue».", 400)

    open_bets = conn.execute(
        """SELECT * FROM bets WHERE event_url = ? AND fight_index = ? AND status = 'open'""",
        (event_url, fight_index),
    ).fetchall()
    if not open_bets:
        _learning_record_outcome(event_url, fight_index, winner_side)
        return {"ok": True, "settled": 0, "details": []}

    details: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for bet in open_bets:
            bid = bet["id"]
            uid = bet["user_id"]
            stake = int(bet["stake"])
            odds_taken = float(bet["odds_taken"])
            side = bet["side"]
            won = side == winner_side
            payout = int(round(stake * odds_taken)) if won else 0

            if won:
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (payout, uid))
                urow = conn.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()
                bal = int(urow["balance"])
                conn.execute(
                    """INSERT INTO transactions (user_id, amount, balance_after, type, bet_id, note)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (uid, payout, bal, "bet_payout", bid, "Vitória na aposta"),
                )
                conn.execute(
                    """UPDATE bets SET status = 'won', payout = ?, settled_at = datetime('now'),
                       winner_side = ? WHERE id = ?""",
                    (payout, winner_side, bid),
                )
                details.append({"bet_id": bid, "user_id": uid, "result": "won", "payout": payout})
            else:
                conn.execute(
                    """UPDATE bets SET status = 'lost', payout = 0, settled_at = datetime('now'),
                       winner_side = ? WHERE id = ?""",
                    (winner_side, bid),
                )
                urow = conn.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()
                details.append(
                    {
                        "bet_id": bid,
                        "user_id": uid,
                        "result": "lost",
                        "payout": 0,
                        "balance": int(urow["balance"]),
                    }
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    _learning_record_outcome(event_url, fight_index, winner_side)
    return {"ok": True, "settled": len(open_bets), "winner_side": winner_side, "details": details}


def list_all_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT u.id, u.email, u.balance, u.is_admin, u.blocked, u.created_at,
        (SELECT COUNT(*) FROM bets b WHERE b.user_id = u.id) AS total_bets,
        (SELECT COUNT(*) FROM parlay_bets p WHERE p.user_id = u.id) AS total_parlays
        FROM users u ORDER BY u.id ASC"""
    ).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["is_admin"] = bool(d.get("is_admin"))
        d["blocked"] = bool(d.get("blocked"))
        d["total_bets"] = int(d.get("total_bets") or 0)
        d["total_parlays"] = int(d.get("total_parlays") or 0)
        out.append(d)
    return out


def admin_set_balance(conn: sqlite3.Connection, user_id: int, new_balance: int, admin_note: str) -> dict[str, Any]:
    if new_balance < 0:
        raise ServiceError("bad_balance", "Saldo não pode ser negativo.", 400)
    row = conn.execute("SELECT id, balance FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise ServiceError("no_user", "Usuário não encontrado.", 404)
    old = int(row["balance"])
    delta = new_balance - old
    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))
    conn.execute(
        """INSERT INTO transactions (user_id, amount, balance_after, type, note)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, delta, new_balance, "admin_adjust", admin_note or "Ajuste admin"),
    )
    conn.commit()
    return {"ok": True, "user_id": user_id, "balance": new_balance}


def admin_set_blocked(conn: sqlite3.Connection, user_id: int, blocked: bool) -> dict[str, Any]:
    conn.execute("UPDATE users SET blocked = ? WHERE id = ?", (1 if blocked else 0, user_id))
    if conn.total_changes != 1:
        conn.rollback()
        raise ServiceError("no_user", "Usuário não encontrado.", 404)
    conn.commit()
    return {"ok": True, "user_id": user_id, "blocked": blocked}


def list_all_bets(
    conn: sqlite3.Connection,
    *,
    event_url: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    lim = max(1, min(500, limit))
    q = "SELECT * FROM bets WHERE 1=1"
    args: list[Any] = []
    if event_url:
        q += " AND event_url = ?"
        args.append(event_url.strip())
    if user_id is not None:
        q += " AND user_id = ?"
        args.append(user_id)
    st = (status or "").strip().lower()
    if st in ("open", "won", "lost"):
        q += " AND status = ?"
        args.append(st)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q += " AND (COALESCE(red_name,'') LIKE ? OR COALESCE(blue_name,'') LIKE ? OR event_url LIKE ?)"
        args.extend([term, term, term])
    q += " ORDER BY id DESC LIMIT ?"
    args.append(lim)
    rows = conn.execute(q, args).fetchall()
    return [row_to_dict(r) for r in rows]


def enrich_bet_for_admin(b: dict[str, Any]) -> dict[str, Any]:
    """Edge / value vs modelo armazenado na aposta (créditos fictícios)."""
    side = (b.get("side") or "").lower()
    try:
        odds_t = float(b.get("odds_taken") or 0)
        pr = float(b.get("prob_red") or 0)
        pb = float(b.get("prob_blue") or 0)
    except (TypeError, ValueError):
        return {**b, "value_edge": None, "implied_prob_bet": None}
    p_model = pr if side == "red" else pb
    implied = 1.0 / odds_t if odds_t > 0 else None
    edge = (p_model * odds_t - 1.0) if odds_t > 0 else None
    out = dict(b)
    out["value_edge"] = round(edge, 4) if edge is not None else None
    out["implied_prob_bet"] = round(implied, 4) if implied is not None else None
    out["model_prob_side"] = round(p_model, 4)
    return out


def admin_dashboard_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    n_users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    n_open = int(conn.execute("SELECT COUNT(*) FROM bets WHERE status = 'open'").fetchone()[0])
    n_liq = int(
        conn.execute("SELECT COUNT(*) FROM bets WHERE status IN ('won', 'lost')").fetchone()[0]
    )
    total_credits = int(conn.execute("SELECT COALESCE(SUM(balance), 0) FROM users").fetchone()[0])
    return {
        "total_users": n_users,
        "bets_open": n_open,
        "bets_settled": n_liq,
        "credits_total": total_credits,
    }


def admin_ranking(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    lim = max(1, min(500, limit))
    rows = conn.execute(
        """
        SELECT u.id AS user_id, u.email,
          (SELECT COUNT(*) FROM bets b WHERE b.user_id = u.id AND b.status = 'won') AS wins,
          (SELECT COUNT(*) FROM bets b WHERE b.user_id = u.id AND b.status = 'lost') AS losses,
          (SELECT COUNT(*) FROM bets b WHERE b.user_id = u.id AND b.status IN ('won', 'lost')) AS settled,
          (SELECT COALESCE(SUM(CASE WHEN b.status = 'won' THEN b.payout - b.stake
              WHEN b.status = 'lost' THEN -b.stake ELSE 0 END), 0)
            FROM bets b WHERE b.user_id = u.id AND b.status IN ('won', 'lost')) AS net_singles,
          (SELECT COUNT(*) FROM parlay_bets p WHERE p.user_id = u.id AND p.status = 'won') AS parlay_wins,
          (SELECT COUNT(*) FROM parlay_bets p WHERE p.user_id = u.id AND p.status IN ('won', 'lost')) AS parlays_settled,
          (SELECT COALESCE(SUM(CASE WHEN p.status = 'won' THEN p.payout - p.stake
              WHEN p.status = 'lost' THEN -p.stake ELSE 0 END), 0)
            FROM parlay_bets p WHERE p.user_id = u.id AND p.status IN ('won', 'lost')) AS net_parlays
        FROM users u
        ORDER BY u.id ASC
        LIMIT ?
        """,
        (lim * 10,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        wins = int(r["wins"] or 0)
        losses = int(r["losses"] or 0)
        settled = int(r["settled"] or 0)
        pw = int(r["parlay_wins"] or 0)
        ps = int(r["parlays_settled"] or 0)
        tot_settled = settled + ps
        if tot_settled == 0:
            continue
        tot_wins = wins + pw
        pct = (100.0 * tot_wins / tot_settled) if tot_settled > 0 else 0.0
        net = int(r["net_singles"] or 0) + int(r["net_parlays"] or 0)
        out.append(
            {
                "user_id": int(r["user_id"]),
                "email": r["email"],
                "wins": wins,
                "losses": losses,
                "settled": settled,
                "parlay_wins": pw,
                "parlays_settled": ps,
                "win_rate_pct": round(pct, 2),
                "net_credits": net,
            }
        )
    out.sort(key=lambda x: (-x["win_rate_pct"], -x["net_credits"]))
    return out[:lim]


def _normalize_official_method(m: Any) -> Optional[str]:
    if m is None:
        return None
    s = str(m).strip().lower()
    if s in ("ko", "tko", "ko_tko", "nocaute"):
        return "ko_tko"
    if s in ("dec", "decisao", "decisão", "points"):
        return "decisao"
    if s in ("sub", "finalizacao", "finalização"):
        return "finalizacao"
    return s


def _outcome_complete_for_leg(leg: dict[str, Any], outcome: dict[str, Any]) -> bool:
    bt = (leg.get("bet_type") or "").strip().lower()
    if bt == "final_result":
        return True
    if bt == "method":
        return _normalize_official_method(outcome.get("method")) is not None
    if bt == "round_winner":
        return outcome.get("round") is not None
    return True


def preview_parlay(
    event_url: str,
    legs_in: list[dict[str, Any]],
    cache_dir: Path,
) -> dict[str, Any]:
    if not legs_in or len(legs_in) < 2:
        raise ServiceError("bad_parlay", "Parlay requer pelo menos 2 pernas.", 400)
    fights = _load_ufc_fights(event_url, cache_dir)
    computed: list[dict[str, Any]] = []
    for raw in legs_in:
        fi = int(raw.get("fight_index", 0))
        if fi < 1 or fi > len(fights):
            raise ServiceError("fight_not_found", f"Luta #{fi} inexistente no evento.", 400)
        f = fights[fi - 1]
        if f.get("error"):
            raise ServiceError("fight_error", f"Luta #{fi} sem análise.", 400)
        bt = (raw.get("bet_type") or "final_result").strip().lower()
        side = (raw.get("side") or "").strip().lower()
        opt = raw.get("option")
        try:
            leg = compute_leg(f, bet_type=bt, side=side, option=opt)
        except ValueError as e:
            raise ServiceError("bad_leg", str(e), 400) from e
        if leg.get("betting_blocked"):
            raise ServiceError(
                "betting_closed",
                f"Mercado fechado na luta #{fi} (SKIP).",
                400,
            )
        leg["fight_index"] = fi
        leg["division"] = f.get("division")
        rname = (f.get("red") or {}).get("name")
        bname = (f.get("blue") or {}).get("name")
        leg["red_name"] = rname
        leg["blue_name"] = bname
        computed.append(leg)
    comb = combined_decimal(computed)
    if comb <= 0:
        raise ServiceError("bad_parlay", "Odds combinadas inválidas.", 400)
    return {
        "ok": True,
        "event_url": event_url,
        "legs": computed,
        "combined_odds": comb,
    }


def place_parlay(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    event_url: str,
    legs_in: list[dict[str, Any]],
    stake: int,
    cache_dir: Path,
) -> dict[str, Any]:
    if stake < 1:
        raise ServiceError("bad_stake", "Aposta mínima: 1 crédito.", 400)
    pkg = preview_parlay(event_url, legs_in, cache_dir)
    legs = pkg["legs"]
    comb = float(pkg["combined_odds"])
    fis = [int(lg["fight_index"]) for lg in legs]
    if len(fis) != len(set(fis)):
        raise ServiceError("duplicate_fight", "Não repita a mesma luta no parlay.", 400)

    urow = conn.execute(
        "SELECT balance, blocked FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not urow:
        raise ServiceError("no_user", "Usuário não encontrado.", 404)
    if urow["blocked"]:
        raise ServiceError("blocked", "Conta suspensa.", 403)
    balance = int(urow["balance"])
    cap = max_stake_allowed(balance)
    if stake > cap:
        raise ServiceError(
            "stake_over_limit",
            f"Stake máximo: {cap} créditos (20% do saldo).",
            400,
        )
    if stake > balance:
        raise ServiceError("insufficient_balance", "Saldo insuficiente.", 400)

    legs_store = []
    for lg in legs:
        slim = {k: v for k, v in lg.items() if k != "weighted_model"}
        legs_store.append(slim)

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ? AND blocked = 0",
            (stake, user_id, stake),
        )
        if conn.total_changes != 1:
            conn.rollback()
            raise ServiceError("insufficient_balance", "Saldo insuficiente.", 400)
        new_bal = balance - stake
        cur = conn.execute(
            """INSERT INTO parlay_bets (
            user_id, event_url, stake, combined_odds, legs_json, status
          ) VALUES (?, ?, ?, ?, ?, 'open')""",
            (user_id, event_url, stake, comb, json.dumps(legs_store, ensure_ascii=False)),
        )
        parlay_id = cur.lastrowid
        conn.execute(
            """INSERT INTO transactions (user_id, amount, balance_after, type, parlay_bet_id, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                -stake,
                new_bal,
                "parlay_stake",
                parlay_id,
                f"Parlay #{parlay_id} ({len(legs)} pernas)",
            ),
        )
        conn.commit()
    except ServiceError:
        raise
    except Exception:
        conn.rollback()
        raise

    return {
        "ok": True,
        "parlay_id": parlay_id,
        "balance": new_bal,
        "combined_odds": comb,
        "payout_if_win": int(round(stake * comb)),
        "legs": legs_store,
        "max_bet_next": max_stake_allowed(new_bal),
    }


def list_user_parlays(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    lim = max(1, min(200, limit))
    rows = conn.execute(
        """SELECT * FROM parlay_bets WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
        (user_id, lim),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def settle_parlays_for_event(
    conn: sqlite3.Connection,
    *,
    event_url: str,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    idx_map: dict[int, dict[str, Any]] = {}
    for o in outcomes:
        try:
            fi = int(o.get("fight_index", 0))
        except (TypeError, ValueError):
            continue
        if fi >= 1:
            idx_map[fi] = o

    rows = conn.execute(
        "SELECT * FROM parlay_bets WHERE event_url = ? AND status = 'open'",
        (event_url,),
    ).fetchall()
    if not rows:
        return {"ok": True, "parlays_settled": 0, "details": []}

    details: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for pb in rows:
            pid = int(pb["id"])
            uid = int(pb["user_id"])
            stake = int(pb["stake"])
            comb = float(pb["combined_odds"])
            legs: list[dict[str, Any]] = json.loads(pb["legs_json"] or "[]")

            ready = True
            for leg in legs:
                fi = int(leg.get("fight_index", 0))
                if fi not in idx_map:
                    ready = False
                    break
                if not _outcome_complete_for_leg(leg, idx_map[fi]):
                    ready = False
                    break
            if not ready:
                continue

            wins = True
            for leg in legs:
                fi = int(leg["fight_index"])
                oc = idx_map[fi]
                ws = (oc.get("winner_side") or "").strip().lower()
                mk = _normalize_official_method(oc.get("method"))
                rn = oc.get("round")
                if rn is not None:
                    try:
                        rn = int(rn)
                    except (TypeError, ValueError):
                        rn = None
                if not leg_wins_against_result(
                    leg,
                    winner_side=ws,
                    official_method=mk,
                    round_num=rn,
                ):
                    wins = False
                    break

            payout = int(round(stake * comb)) if wins else 0
            if wins:
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (payout, uid))
                urow = conn.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()
                bal = int(urow["balance"])
                conn.execute(
                    """INSERT INTO transactions (user_id, amount, balance_after, type, parlay_bet_id, note)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (uid, payout, bal, "parlay_payout", pid, f"Parlay #{pid} ganho"),
                )
                conn.execute(
                    """UPDATE parlay_bets SET status = 'won', payout = ?, settled_at = datetime('now')
                       WHERE id = ?""",
                    (payout, pid),
                )
                details.append({"parlay_id": pid, "user_id": uid, "result": "won", "payout": payout})
            else:
                conn.execute(
                    """UPDATE parlay_bets SET status = 'lost', payout = 0, settled_at = datetime('now')
                       WHERE id = ?""",
                    (pid,),
                )
                details.append({"parlay_id": pid, "user_id": uid, "result": "lost", "payout": 0})
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"ok": True, "parlays_settled": len(details), "details": details}


def settle_event_outcomes(
    conn: sqlite3.Connection,
    *,
    event_url: str,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Liquida cada luta (apostas simples) e, em seguida, parlays cujas pernas estão todas resolvidas.
    """
    by_fi: dict[int, dict[str, Any]] = {}
    for o in outcomes:
        try:
            fi = int(o.get("fight_index", 0))
        except (TypeError, ValueError):
            continue
        if fi >= 1:
            by_fi[fi] = o

    singles_details: list[dict[str, Any]] = []
    for fi in sorted(by_fi.keys()):
        o = by_fi[fi]
        ws = (o.get("winner_side") or "").strip()
        if not ws:
            continue
        r = settle_fight_bets(conn, event_url=event_url, fight_index=fi, winner_side=ws)
        singles_details.append({"fight_index": fi, **r})

    out_list = list(by_fi.values())
    par = settle_parlays_for_event(conn, event_url=event_url, outcomes=out_list)
    return {
        "ok": True,
        "event_url": event_url,
        "fights_settled": len(singles_details),
        "singles": singles_details,
        "parlays": par,
    }


def ensure_db(conn: sqlite3.Connection) -> None:
    init_schema(conn)
