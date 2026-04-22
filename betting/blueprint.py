"""Rotas JSON: auth, apostas, odds, liquidação, admin."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, session

from betting.db import connect, default_db_path, init_schema
from betting.service import (
    ServiceError,
    ensure_default_admin,
    get_user,
    list_user_bets,
    list_user_parlays,
    login_user,
    max_stake_allowed,
    odds_for_event,
    place_bet,
    place_parlay,
    preview_parlay,
    register_user,
    settle_event_outcomes,
    settle_fight_bets,
)

betting_bp = Blueprint("betting", __name__, url_prefix="/api")


def _cache_dir() -> Path:
    return Path(current_app.root_path).resolve().parent / ".ufc_html_cache"


def _db_path() -> Path:
    return Path(current_app.config["BETTING_DB_PATH"])


@betting_bp.before_request
def _open_betting_db() -> None:
    g._betting_conn = connect(_db_path())
    init_schema(g._betting_conn)


@betting_bp.teardown_request
def _close_betting_db(exc: BaseException | None) -> None:
    conn = getattr(g, "_betting_conn", None)
    if conn is not None:
        conn.close()
        g._betting_conn = None


def _conn():
    return g._betting_conn


def _json_error(err: ServiceError):
    return jsonify({"ok": False, "error": err.code, "message": err.message}), err.http_status


def _session_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_user(_conn(), int(uid))


@betting_bp.route("/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    try:
        out = register_user(_conn(), data.get("email", ""), data.get("password", ""))
        return jsonify({"ok": True, **out}), 201
    except ServiceError as e:
        return _json_error(e)


@betting_bp.route("/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    try:
        u = login_user(_conn(), data.get("email", ""), data.get("password", ""))
        session.clear()
        session["user_id"] = u["id"]
        session["is_admin"] = bool(u.get("is_admin"))
        session.permanent = True
        return jsonify({"ok": True, "user": u})
    except ServiceError as e:
        return _json_error(e)


@betting_bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@betting_bp.route("/auth/me", methods=["GET"])
def auth_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "authenticated": False}), 200
    u = get_user(_conn(), int(uid))
    if not u:
        session.clear()
        return jsonify({"ok": False, "authenticated": False}), 200
    return jsonify({"ok": True, "authenticated": True, "user": u})


@betting_bp.route("/odds", methods=["GET"])
def api_odds():
    event_url = (request.args.get("url") or "").strip()
    if not event_url:
        return jsonify({"ok": False, "error": "missing_url", "message": "Parâmetro url obrigatório."}), 400
    try:
        data = odds_for_event(event_url, _cache_dir())
        return jsonify(data)
    except ServiceError as e:
        return _json_error(e)


@betting_bp.route("/bet", methods=["POST"])
def api_place_bet():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "login_required", "message": "Faça login para apostar."}), 401
    data = request.get_json(silent=True) or {}
    try:
        out = place_bet(
            _conn(),
            int(uid),
            event_url=(data.get("event_url") or "").strip(),
            fight_index=int(data.get("fight_index", 0)),
            side=data.get("side", ""),
            stake=int(data.get("stake", 0)),
            cache_dir=_cache_dir(),
        )
        return jsonify(out)
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


@betting_bp.route("/bet/history", methods=["GET"])
def api_bet_history():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "login_required", "message": "Faça login."}), 401
    lim = request.args.get("limit", "50")
    try:
        n = int(lim)
    except ValueError:
        n = 50
    conn = _conn()
    bets = list_user_bets(conn, int(uid), limit=n)
    parlays = list_user_parlays(conn, int(uid), limit=n)
    return jsonify({"ok": True, "bets": bets, "parlays": parlays})


def _settle_impl() -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}
    conn = _conn()
    outcomes = data.get("outcomes")
    if isinstance(outcomes, list) and outcomes:
        out = settle_event_outcomes(
            conn,
            event_url=(data.get("event_url") or "").strip(),
            outcomes=outcomes,
        )
        return out, 200
    out = settle_fight_bets(
        conn,
        event_url=(data.get("event_url") or "").strip(),
        fight_index=int(data.get("fight_index", 0)),
        winner_side=data.get("winner_side", ""),
    )
    return out, 200


@betting_bp.route("/bet/potential", methods=["GET", "POST"])
def api_bet_potential():
    """Pré-visualização de parlay (odds combinadas, retorno estimado) — sem debitar."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = dict(request.args)
        legs_raw = (data.get("legs") or "").strip()
        try:
            data["legs"] = json.loads(legs_raw) if legs_raw else []
        except Exception:
            return jsonify({"ok": False, "error": "bad_legs", "message": "Parâmetro legs (JSON) inválido."}), 400
    event_url = (data.get("event_url") or data.get("url") or "").strip()
    legs = data.get("legs") or []
    stake = int(data.get("stake", 0) or 0)
    try:
        prev = preview_parlay(event_url, legs, _cache_dir())
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": "bad_request", "message": str(e)}), 400
    uid = session.get("user_id")
    bal = 0
    cap = 0
    if uid:
        u = get_user(_conn(), int(uid))
        if u:
            bal = int(u["balance"])
            cap = max_stake_allowed(bal)
    comb = float(prev["combined_odds"])
    if stake >= 1:
        est_stake = stake
    elif cap >= 1:
        est_stake = min(cap, max(1, cap))
    else:
        est_stake = 1
    payout_est = int(round(est_stake * comb)) if est_stake >= 1 else 0
    return jsonify(
        {
            "ok": True,
            **prev,
            "stake_example": est_stake,
            "estimated_return": payout_est,
            "estimated_profit": max(0, payout_est - est_stake),
            "max_stake": cap,
            "balance": bal,
        }
    )


def _api_place_parlay():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "login_required", "message": "Faça login para apostar."}), 401
    data = request.get_json(silent=True) or {}
    try:
        out = place_parlay(
            _conn(),
            int(uid),
            event_url=(data.get("event_url") or "").strip(),
            legs_in=data.get("legs") or [],
            stake=int(data.get("stake", 0)),
            cache_dir=_cache_dir(),
        )
        return jsonify(out)
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


@betting_bp.route("/bet/multi", methods=["POST"])
def api_bet_multi():
    return _api_place_parlay()


@betting_bp.route("/bet/confirm", methods=["POST"])
def api_bet_confirm():
    """Confirma aposta múltipla (mesmo corpo que /bet/multi)."""
    return _api_place_parlay()


@betting_bp.route("/bet/settle", methods=["POST"])
def api_settle():
    """Liquidação: admin logado OU chave X-Settle-Key (automação)."""
    u = _session_user()
    key = (request.headers.get("X-Settle-Key") or "").strip()
    expected = (os.environ.get("BETTING_SETTLE_KEY") or "").strip()
    key_ok = bool(expected) and key == expected
    admin_ok = bool(u and u.get("is_admin"))
    if not admin_ok and not key_ok:
        if not expected and not admin_ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "settle_forbidden",
                        "message": "Use conta admin ou defina BETTING_SETTLE_KEY.",
                    }
                ),
                403,
            )
        return jsonify({"ok": False, "error": "forbidden", "message": "Sem permissão para liquidar."}), 403
    try:
        out, st = _settle_impl()
        return jsonify(out), st
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


def init_betting(app) -> None:
    root = Path(app.root_path).resolve().parent
    default_path = default_db_path(root)
    app.config.setdefault("BETTING_DB_PATH", str(default_path))
    p = Path(app.config["BETTING_DB_PATH"])
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    init_schema(conn)
    ensure_default_admin(conn)
    conn.close()

    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    from datetime import timedelta

    app.permanent_session_lifetime = timedelta(days=7)

    app.register_blueprint(betting_bp)
