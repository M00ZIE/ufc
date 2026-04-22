"""Painel admin: páginas HTML, login dedicado e API /api/admin/*."""

from __future__ import annotations

import csv
import io
import json
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import requests
from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from iptv import IptvError, fetch_m3u_parse_streaming, mask_url_credentials, maybe_github_raw, playlist_candidate_urls
from betting.db import connect, init_schema
from betting.service import (
    ServiceError,
    admin_dashboard_stats,
    admin_ranking,
    admin_set_balance,
    admin_set_blocked,
    enrich_bet_for_admin,
    get_user,
    list_all_bets,
    list_all_users,
    login_user,
    settle_fight_bets,
)

admin_bp = Blueprint("admin_panel", __name__)


def _db_path() -> Path:
    return Path(current_app.config["BETTING_DB_PATH"])


def _iptv_settings_path() -> Path:
    # Guardar no mesmo diretório do sqlite (em Vercel fica em /tmp)
    return _db_path().with_name("iptv_settings.json")


def _read_iptv_settings() -> dict[str, Any]:
    p = _iptv_settings_path()
    if not p.exists():
        return {
            "playlist_url": "",
            "autoplay_enabled": False,
            "selected_channel_name": "",
            "selected_channel_url": "",
            # legacy (compat)
            "autoplay_channel": "",
            "match_mode": "contains",
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("json não-objeto")
    except Exception:
        return {
            "playlist_url": "",
            "autoplay_enabled": False,
            "selected_channel_name": "",
            "selected_channel_url": "",
            "autoplay_channel": "",
            "match_mode": "contains",
        }
    out = {
        "playlist_url": str(data.get("playlist_url") or "").strip(),
        "autoplay_enabled": bool(data.get("autoplay_enabled") or False),
        "selected_channel_name": str(data.get("selected_channel_name") or "").strip(),
        "selected_channel_url": str(data.get("selected_channel_url") or "").strip(),
        "autoplay_channel": str(data.get("autoplay_channel") or "").strip(),
        "match_mode": str(data.get("match_mode") or "contains").strip().lower(),
    }
    if out["match_mode"] not in ("exact", "contains", "regex"):
        out["match_mode"] = "contains"
    return out


def _write_iptv_settings(data: dict[str, Any]) -> dict[str, Any]:
    incoming_playlist_url = str(data.get("playlist_url") or "").strip()
    current = _read_iptv_settings()
    playlist_url = incoming_playlist_url
    autoplay_enabled = bool(data.get("autoplay_enabled") or False)
    selected_channel_name = str(data.get("selected_channel_name") or "").strip()
    selected_channel_url = str(data.get("selected_channel_url") or "").strip()
    # legacy (mantido, mas não é mais o fluxo principal)
    autoplay_channel = str(data.get("autoplay_channel") or "").strip()
    match_mode = str(data.get("match_mode") or "contains").strip().lower()
    if match_mode not in ("exact", "contains", "regex"):
        match_mode = "contains"
    # Compat: se vier mascarada ou vazia, mantém a URL já salva.
    low_in = incoming_playlist_url.lower()
    if (not playlist_url) or ("***" in incoming_playlist_url) or ("%2a%2a%2a" in low_in):
        playlist_url = str(current.get("playlist_url") or "").strip()
    if playlist_url and not (playlist_url.startswith("http://") or playlist_url.startswith("https://")):
        raise ServiceError("bad_request", "A playlist_url deve ser http(s).", 400)
    if selected_channel_url and not (selected_channel_url.startswith("http://") or selected_channel_url.startswith("https://")):
        raise ServiceError("bad_request", "A selected_channel_url deve ser http(s).", 400)
    out = {
        "playlist_url": playlist_url,
        "autoplay_enabled": autoplay_enabled,
        "selected_channel_name": selected_channel_name,
        "selected_channel_url": selected_channel_url,
        "autoplay_channel": autoplay_channel,
        "match_mode": match_mode,
    }
    p = _iptv_settings_path()
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


@admin_bp.before_request
def _open_admin_db() -> None:
    g._admin_conn = connect(_db_path())
    init_schema(g._admin_conn)


@admin_bp.teardown_request
def _close_admin_db(exc: BaseException | None) -> None:
    conn = getattr(g, "_admin_conn", None)
    if conn is not None:
        conn.close()
        g._admin_conn = None


def _conn():
    return g._admin_conn


def _json_error(err: ServiceError) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": err.code, "message": err.message}), err.http_status


def _require_admin_api() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    uid = session.get("user_id")
    if not uid:
        return None, (jsonify({"ok": False, "error": "login_required", "message": "Faça login no painel admin."}), 401)
    u = get_user(_conn(), int(uid))
    if not u or not u.get("is_admin"):
        session.pop("is_admin", None)
        return None, (jsonify({"ok": False, "error": "forbidden", "message": "Acesso restrito a administradores."}), 403)
    session["is_admin"] = True
    return u, None


def admin_required_json(f: Callable) -> Callable:
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any):
        _, err = _require_admin_api()
        if err:
            return err
        return f(*args, **kwargs)

    return wrapped


def _attach_user_emails(bets: list[dict[str, Any]]) -> None:
    cache: dict[int, str] = {}
    for b in bets:
        uid = int(b["user_id"])
        if uid not in cache:
            u = get_user(_conn(), uid)
            cache[uid] = (u or {}).get("email") or ""
        b["user_email"] = cache[uid]


# --- HTML ---


@admin_bp.route("/admin/login", methods=["GET"])
def admin_login_page():
    uid = session.get("user_id")
    if uid:
        u = get_user(_conn(), int(uid))
        if u and u.get("is_admin"):
            session["is_admin"] = True
            return redirect(url_for("admin_panel.admin_dashboard"))
    err = (request.args.get("err") or "").strip()
    return render_template("admin_login.html", error=err or None)


@admin_bp.route("/admin/login", methods=["POST"])
def admin_login_post():
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    try:
        u = login_user(_conn(), email, password)
        if not u.get("is_admin"):
            return (
                render_template(
                    "admin_login.html",
                    error="Esta conta não tem permissão de administrador.",
                ),
                403,
            )
        session["user_id"] = u["id"]
        session["is_admin"] = True
        session.permanent = True
        return redirect(url_for("admin_panel.admin_dashboard"))
    except ServiceError as e:
        return render_template("admin_login.html", error=e.message), e.http_status


@admin_bp.route("/admin/logout", methods=["GET"])
def admin_logout():
    session.pop("user_id", None)
    session.pop("is_admin", None)
    return redirect(url_for("admin_panel.admin_login_page"))


@admin_bp.route("/admin", methods=["GET"])
def admin_dashboard():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("admin_panel.admin_login_page"))
    u = get_user(_conn(), int(uid))
    if not u or not u.get("is_admin"):
        session.pop("is_admin", None)
        return redirect(url_for("admin_panel.admin_login_page"))
    session["is_admin"] = True
    return render_template("admin.html", admin_email=u.get("email"))


# --- JSON API ---


@admin_bp.route("/api/admin/users", methods=["GET"])
@admin_required_json
def api_admin_users():
    q = (request.args.get("q") or "").strip().lower()
    users = list_all_users(_conn())
    if q:
        users = [u for u in users if q in (u.get("email") or "").lower()]
    return jsonify({"ok": True, "users": users})


@admin_bp.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@admin_required_json
def api_admin_patch_user(user_id: int):
    data = request.get_json(silent=True) or {}
    try:
        if "balance" in data:
            admin_set_balance(
                _conn(),
                user_id,
                int(data["balance"]),
                str(data.get("note") or "Painel admin"),
            )
        if "blocked" in data:
            admin_set_blocked(_conn(), user_id, bool(data["blocked"]))
        u = get_user(_conn(), user_id)
        return jsonify({"ok": True, "user": u})
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


@admin_bp.route("/api/admin/bets", methods=["GET"])
@admin_required_json
def api_admin_bets():
    event_url = (request.args.get("event_url") or "").strip() or None
    uid_raw = request.args.get("user_id")
    user_id = int(uid_raw) if uid_raw and str(uid_raw).isdigit() else None
    status = (request.args.get("status") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None
    lim = request.args.get("limit", "200")
    try:
        n = int(lim)
    except ValueError:
        n = 200
    fmt = (request.args.get("format") or "").strip().lower()
    bets = list_all_bets(
        _conn(),
        event_url=event_url,
        user_id=user_id,
        status=status if status in ("open", "won", "lost") else None,
        search=search,
        limit=n,
    )
    _attach_user_emails(bets)
    enriched = [enrich_bet_for_admin(b) for b in bets]

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "id",
                "user_id",
                "user_email",
                "event_url",
                "fight_index",
                "red_name",
                "blue_name",
                "side",
                "stake",
                "odds_taken",
                "odds_red",
                "odds_blue",
                "prob_red",
                "prob_blue",
                "risk_tier",
                "value_edge",
                "model_prob_side",
                "status",
                "payout",
                "winner_side",
                "created_at",
                "settled_at",
            ]
        )
        for b in enriched:
            w.writerow(
                [
                    b.get("id"),
                    b.get("user_id"),
                    b.get("user_email"),
                    b.get("event_url"),
                    b.get("fight_index"),
                    b.get("red_name"),
                    b.get("blue_name"),
                    b.get("side"),
                    b.get("stake"),
                    b.get("odds_taken"),
                    b.get("odds_red"),
                    b.get("odds_blue"),
                    b.get("prob_red"),
                    b.get("prob_blue"),
                    b.get("risk_tier"),
                    b.get("value_edge"),
                    b.get("model_prob_side"),
                    b.get("status"),
                    b.get("payout"),
                    b.get("winner_side"),
                    b.get("created_at"),
                    b.get("settled_at"),
                ]
            )
        out = buf.getvalue()
        return Response(
            "\ufeff" + out,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="ufc_apostas_admin.csv"'},
        )

    return jsonify({"ok": True, "bets": enriched})


@admin_bp.route("/api/admin/ranking", methods=["GET"])
@admin_required_json
def api_admin_ranking():
    lim = request.args.get("limit", "100")
    try:
        n = int(lim)
    except ValueError:
        n = 100
    rows = admin_ranking(_conn(), limit=n)
    return jsonify({"ok": True, "ranking": rows})


@admin_bp.route("/api/admin/stats", methods=["GET"])
@admin_required_json
def api_admin_stats():
    return jsonify({"ok": True, **admin_dashboard_stats(_conn())})


@admin_bp.route("/api/admin/settle", methods=["POST"])
@admin_required_json
def api_admin_settle():
    data = request.get_json(silent=True) or {}
    try:
        out = settle_fight_bets(
            _conn(),
            event_url=(data.get("event_url") or "").strip(),
            fight_index=int(data.get("fight_index", 0)),
            winner_side=data.get("winner_side", ""),
        )
        return jsonify(out)
    except ServiceError as e:
        return _json_error(e)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


@admin_bp.route("/api/admin/iptv-settings", methods=["GET"])
@admin_required_json
def api_admin_iptv_settings_get():
    s = _read_iptv_settings()
    # Não expor credenciais no browser
    playlist_raw = s.get("playlist_url") or ""
    s["playlist_url_masked"] = mask_url_credentials(playlist_raw) if playlist_raw else ""
    s["playlist_url"] = ""  # nunca retornar a URL real
    s["has_playlist_url"] = bool(playlist_raw)
    return jsonify({"ok": True, "settings": s})


@admin_bp.route("/api/admin/iptv-settings", methods=["PUT"])
@admin_required_json
def api_admin_iptv_settings_put():
    data = request.get_json(silent=True) or {}
    try:
        saved = _write_iptv_settings(data)
        playlist_raw = saved.get("playlist_url") or ""
        saved["playlist_url_masked"] = mask_url_credentials(playlist_raw) if playlist_raw else ""
        saved["playlist_url"] = ""
        saved["has_playlist_url"] = bool(playlist_raw)
        return jsonify({"ok": True, "settings": saved})
    except ServiceError as e:
        return _json_error(e)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request", "message": "JSON inválido."}), 400


@admin_bp.route("/api/admin/iptv-playlist", methods=["GET"])
@admin_required_json
def api_admin_iptv_playlist():
    """
    Carrega a playlist M3U do servidor usando a URL salva (sem expor credenciais ao browser).
    """
    s = _read_iptv_settings()
    raw = (s.get("playlist_url") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "missing_playlist", "message": "Configure a playlist_url primeiro."}), 400
    url = maybe_github_raw(raw)
    try:
        out = None
        attempted: list[str] = []
        last_err: IptvError | None = None
        for cand in playlist_candidate_urls(raw):
            attempted.append(cand)
            try:
                sess = requests.Session()
                out = fetch_m3u_parse_streaming(
                    sess,
                    cand,
                    timeout=25,
                    max_bytes=int(current_app.config.get("IPTV_MAX_BYTES") or 0),
                    max_channels=int(current_app.config.get("IPTV_MAX_CHANNELS") or 0),
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "*/*",
                    },
                    max_redirects=int(current_app.config.get("IPTV_MAX_REDIRECTS") or 3),
                )
                break
            except IptvError as e:
                last_err = e
                continue
        if out is None and last_err:
            return jsonify(
                {
                    "ok": False,
                    "error": "iptv_error",
                    "message": last_err.message,
                    "attempted_urls": attempted,
                }
            ), last_err.http_status
        if out is None:
            return jsonify({"ok": False, "error": "iptv_error", "message": "Falha ao baixar M3U."}), 502
        chans = out["channels"]
        groups = out["groups"]
        return jsonify({"ok": True, "channels_count": len(chans), "groups": groups, "channels": chans})
    except IptvError as e:
        return jsonify({"ok": False, "error": "iptv_error", "message": e.message}), e.http_status
    except Exception as e:
        return jsonify({"ok": False, "error": "iptv_error", "message": str(e)}), 502

