#!/usr/bin/env python3
"""
Site local: card UFC + % de vitória e método previsto (modelo ufc_event_analysis).

Uso:
  pip install flask
  python app.py
  Abra http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin

import requests
from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from admin_blueprint import admin_bp
from betting import init_betting
from betting.db import connect, init_schema
from betting.service import get_user
from iptv import IptvError, M3UCache, RateLimiter, fetch_m3u_parse_streaming, maybe_github_raw, playlist_candidate_urls, probe_url
from sports import DEFAULT_SPORT, get_analyzer, list_sport_ids
from sports.ufc_urls import allowed_ufc_image_url
from ufc_events import fetch_events_list

# Mesmo default que ufc_event_analysis.DEFAULT_URL (evita import pesado no cold start).
DEFAULT_URL = "https://www.ufc.com.br/event/ufc-fight-night-march-28-2026"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "dev-secret-change-me"

# IPTV defaults (pode sobrescrever por env)
app.config["IPTV_MAX_BYTES"] = int(os.environ.get("IPTV_MAX_BYTES") or "60000000")  # 60MB
app.config["IPTV_M3U_CACHE_TTL_SECONDS"] = int(os.environ.get("IPTV_M3U_CACHE_TTL_SECONDS") or "300")  # 5min
app.config["IPTV_MAX_CHANNELS"] = int(os.environ.get("IPTV_MAX_CHANNELS") or "50000")
app.config["IPTV_MAX_REDIRECTS"] = int(os.environ.get("IPTV_MAX_REDIRECTS") or "3")
app.config["IPTV_RATE_LIMIT_PER_MIN"] = int(os.environ.get("IPTV_RATE_LIMIT_PER_MIN") or "30")

# Vercel: filesystem gravavel so em /tmp; sessao HTTPS
if os.environ.get("VERCEL"):
    import tempfile

    _tmp = Path(tempfile.gettempdir()) / "ufc_instance"
    _tmp.mkdir(parents=True, exist_ok=True)
    app.config["BETTING_DB_PATH"] = str(_tmp / "betting.sqlite3")
    app.config["SESSION_COOKIE_SECURE"] = True

init_betting(app)
app.register_blueprint(admin_bp)

_iptv_cache = M3UCache()
_iptv_rl = RateLimiter(per_ip_limit=app.config["IPTV_RATE_LIMIT_PER_MIN"], window_seconds=60)

if os.environ.get("VERCEL"):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


def _cache_dir() -> Path:
    if os.environ.get("VERCEL"):
        import tempfile

        p = Path(tempfile.gettempdir()) / "ufc_html_cache"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent / ".ufc_html_cache"


def _iptv_settings_path() -> Path:
    # Mesmo local do sqlite do betting (em Vercel: /tmp)
    return Path(app.config["BETTING_DB_PATH"]).with_name("iptv_settings.json")


def _read_iptv_settings_public() -> dict[str, Any]:
    p = _iptv_settings_path()
    if not p.exists():
        return {
            "playlist_url": "",
            "autoplay_enabled": False,
            "selected_channel_name": "",
            "selected_channel_url": "",
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
        }
    return {
        "playlist_url": str(data.get("playlist_url") or "").strip(),
        "autoplay_enabled": bool(data.get("autoplay_enabled") or False),
        "selected_channel_name": str(data.get("selected_channel_name") or "").strip(),
        "selected_channel_url": str(data.get("selected_channel_url") or "").strip(),
    }


def _client_ip() -> str:
    # ProxyFix já está ativo em Vercel; ainda assim suportar X-Forwarded-For
    xf = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xf or (request.remote_addr or "unknown")


def _iptv_rate_limit_or_429():
    ip = _client_ip()
    if not _iptv_rl.allow(ip):
        return jsonify({"ok": False, "errors": ["Rate limit: tente novamente em instantes."]}), 429
    return None


def _is_http_url(u: str) -> bool:
    return bool(u) and (u.startswith("http://") or u.startswith("https://"))


def _norm_ct(ct: str | None) -> str:
    return ((ct or "").split(";", 1)[0]).strip().lower()


def _is_hls_manifest(url: str, ct: str) -> bool:
    if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl"):
        return True
    low = (url or "").lower()
    return ".m3u8" in low


def _to_stream_proxy_url(absolute_url: str) -> str:
    return "/api/iptv/stream?url=" + quote(absolute_url, safe="")


def _rewrite_manifest_uris(text: str, base_url: str) -> str:
    """
    Reescreve URIs de playlist HLS para passarem pelo proxy local.
    Isso evita bloqueios CORS/referrer em manifests e segmentos.
    """
    import re

    def _rewrite_tag_uri(line: str) -> str:
        pat = r'URI="([^"]+)"'

        def _sub(m):
            raw = (m.group(1) or "").strip()
            if not raw or raw.startswith("data:"):
                return m.group(0)
            absu = urljoin(base_url, raw)
            if not _is_http_url(absu):
                return m.group(0)
            return f'URI="{_to_stream_proxy_url(absu)}"'

        return re.sub(pat, _sub, line)

    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            out.append(raw_line)
            continue
        if line.startswith("#"):
            out.append(_rewrite_tag_uri(raw_line))
            continue
        absu = urljoin(base_url, line)
        if _is_http_url(absu):
            out.append(_to_stream_proxy_url(absu))
        else:
            out.append(raw_line)
    # Mantém quebra final para compatibilidade com alguns players
    return "\n".join(out) + "\n"


def _current_user_from_db() -> dict[str, Any] | None:
    """Usuário da sessão com is_admin/blocked (para páginas HTML)."""
    uid = session.get("user_id")
    if not uid:
        return None
    p = Path(app.config["BETTING_DB_PATH"])
    conn = connect(p)
    init_schema(conn)
    try:
        return get_user(conn, int(uid))
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html", default_url=DEFAULT_URL)


@app.route("/login")
def page_login():
    nxt = (request.args.get("next") or "/").strip()
    if not nxt.startswith("/"):
        nxt = "/"
    return render_template("login.html", next_url=nxt)


@app.route("/register")
def page_register():
    return render_template("register.html")


@app.route("/bets")
def page_bets():
    return render_template("bets.html")


@app.route("/api/sports")
def api_sports():
    """Lista analisadores registrados (UFC)."""
    return jsonify({"sports": list_sport_ids(), "default": DEFAULT_SPORT})


@app.route("/api/ufc/events")
def api_ufc_events():
    """Eventos recentes (ufc.com.br/events) com cache em disco."""
    refresh = request.args.get("refresh", "0") == "1"
    sess = requests.Session()
    data = fetch_events_list(sess, cache_dir=_cache_dir(), refresh=refresh)
    return jsonify(data)


@app.route("/api/ufc/event-meta")
def api_ufc_event_meta():
    """
    Metadados leves da página do evento (og:image, título, data) — mesmo cache TTL ~2h.
    Usado pelo hero no topo, sem rodar a análise completa do card.
    """
    url = (request.args.get("url") or "").strip()
    refresh = request.args.get("refresh", "0") == "1"
    if not url:
        return jsonify({"ok": False, "errors": ["URL vazia"]}), 400
    try:
        analyzer = get_analyzer(DEFAULT_SPORT)
    except KeyError:
        return jsonify({"ok": False, "errors": ["Analisador indisponível"]}), 400
    if not analyzer.validate_event_url(url):
        return jsonify({"ok": False, "errors": ["URL inválida"]}), 400
    from ufc_event_analysis import extract_event_page_meta, fetch_html

    sess = requests.Session()
    ttl = 2.0 * 3600.0
    try:
        html = fetch_html(
            url,
            sess,
            cache_dir=_cache_dir(),
            cache_max_age_seconds=ttl,
            force_refresh=refresh,
        )
    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 502
    meta = extract_event_page_meta(html)
    return jsonify(
        {
            "ok": True,
            "event_url": url,
            "hero_image_url": meta.get("hero_image_url"),
            "event_starts_at": meta.get("event_starts_at"),
            "event_title": meta.get("og_title"),
        }
    )


@app.route("/api/ufc/event-results")
def api_ufc_event_results():
    """
    Resultados oficiais já publicados no HTML do card (Win/Loss, método, round).
    Usado para conferir se as previsões bateram após o evento.
    """
    url = (request.args.get("url") or "").strip()
    refresh = request.args.get("refresh", "0") == "1"
    if not url:
        return jsonify({"ok": False, "errors": ["URL vazia"]}), 400
    try:
        analyzer = get_analyzer(DEFAULT_SPORT)
    except KeyError:
        return jsonify({"ok": False, "errors": ["Analisador indisponível"]}), 400
    if not analyzer.validate_event_url(url):
        return jsonify({"ok": False, "errors": ["URL inválida"]}), 400
    from ufc_event_analysis import (
        build_event_results_payload,
        drupal_event_nid_from_html,
        fetch_event_fights_jsonapi,
        fetch_html,
        jsonapi_origin_for_event_url,
        paired_ufc_event_mirror_url,
    )

    sess = requests.Session()
    # Resultados mudam rápido no dia do evento; cache curto (HTML costuma vir incompleto no SSR).
    ttl = 300.0 if not refresh else 0.0
    try:
        html = fetch_html(
            url,
            sess,
            cache_dir=_cache_dir(),
            cache_max_age_seconds=ttl,
            force_refresh=refresh,
        )
    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 502
    mirror = paired_ufc_event_mirror_url(url)
    mirror_html: str | None = None
    if mirror:
        try:
            mirror_html = fetch_html(
                mirror,
                sess,
                cache_dir=_cache_dir(),
                cache_max_age_seconds=ttl,
                force_refresh=refresh,
            )
        except Exception:
            mirror_html = None
    event_nid = drupal_event_nid_from_html(html)
    if event_nid is None and mirror_html:
        event_nid = drupal_event_nid_from_html(mirror_html)
    jsonapi_doc: dict | None = None
    if event_nid is not None:
        jsonapi_doc = fetch_event_fights_jsonapi(
            sess,
            jsonapi_origin_url=jsonapi_origin_for_event_url(url),
            event_nid=event_nid,
            cache_dir=_cache_dir(),
            cache_max_age_seconds=ttl,
            force_refresh=refresh,
        )
    payload = build_event_results_payload(
        url,
        html,
        mirror_url=mirror if mirror_html else None,
        mirror_html=mirror_html,
        jsonapi_doc=jsonapi_doc,
        session=sess,
    )
    use_external = (request.args.get("external", "1") or "1").strip().lower() not in ("0", "false", "no")
    if use_external:
        try:
            from ufc_external_context import enrich_event_results_fights_from_external

            ext_meta = enrich_event_results_fights_from_external(
                payload.get("fights") or [],
                payload.get("event_title"),
                sess,
                cache_dir=_cache_dir(),
                cache_ttl_seconds=600.0 if not refresh else 0.0,
                force_refresh=refresh,
            )
            payload["external_enrichment"] = ext_meta
            if ext_meta.get("filled"):
                note = (
                    f"{ext_meta.get('filled')} luta(s) marcadas como concluídas com base em títulos de "
                    "Google News, Reddit (r/MMA, r/UFC), RSS (Super Lutas, MMA Junkie, Sherdog, etc.) e ecos X via Google — "
                    "confira os links nas fontes; não substitui o registro oficial da UFC."
                )
                payload["results_note"] = (
                    f"{payload.get('results_note') or ''} {note}".strip()
                    if payload.get("results_note")
                    else note
                )
        except Exception as e:
            payload["external_enrichment"] = {"attempted": True, "error": str(e)}
    return jsonify({"ok": True, **payload})


@app.route("/api/v1")
def api_v1_discovery():
    """Descoberta da API v1 (UFC)."""
    return jsonify(
        {
            "ok": True,
            "name": "Fight Analytics API v1",
            "resources": {
                "ufc_events": "/api/ufc/events (refresh=1 para forçar cache)",
                "ufc_event_meta": "/api/ufc/event-meta?url=EVENT_URL (og:image, título, data)",
                "ufc_event_results": "/api/ufc/event-results?url=EVENT_URL&external=1 (external=0 desliga imprensa/Reddit)",
                "analyze": "/api/analyze?url=EVENT_URL",
            },
            "_meta": {"api": "fight-analytics/v1", "focus": "UFC"},
        }
    )


@app.route("/api/proxy-image")
def api_proxy_image():
    """
    Repassa imagens da UFC pelo próprio servidor.
    Evita bloqueios de hotlink / referrer no navegador ao carregar <img> de 127.0.0.1.
    """
    raw = (request.args.get("url") or "").strip()
    if not raw:
        return "", 400
    try:
        url = unquote(raw)
    except Exception:
        url = raw
    if not allowed_ufc_image_url(url):
        return "", 400
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        r.raise_for_status()
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            return "", 502
        return Response(
            r.content,
            mimetype=ct,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return "", 502


@app.route("/api/analyze")
def api_analyze():
    sport = (request.args.get("sport") or DEFAULT_SPORT).strip().lower()
    url = request.args.get("url", DEFAULT_URL).strip()
    if not url:
        return jsonify({"ok": False, "errors": ["URL vazia"]}), 400

    try:
        analyzer = get_analyzer(sport)
    except KeyError:
        return jsonify(
            {
                "ok": False,
                "errors": [f"Esporte «{sport}» não suportado."],
                "supported_sports": list_sport_ids(),
            }
        ), 400

    if not analyzer.validate_event_url(url):
        return jsonify(
            {
                "ok": False,
                "errors": ["URL do evento inválida ou não permitida para este esporte."],
            }
        ), 400

    refresh = request.args.get("refresh", "0") == "1"
    try:
        data = analyzer.analyze(
            url,
            cache_dir=_cache_dir(),
            cache_hours=24.0,
            refresh=refresh,
        )
    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "sport": sport,
                "event_url": url,
                "fights": [],
                "errors": [str(e) or type(e).__name__],
            }
        ), 500
    try:
        status = 200 if data.get("ok") else 502
        return jsonify(data), status
    except (TypeError, ValueError, OverflowError) as e:
        return jsonify(
            {
                "ok": False,
                "sport": sport,
                "event_url": url,
                "fights": [],
                "errors": [f"Resposta não serializável em JSON: {e}"],
            }
        ), 500


@app.errorhandler(HTTPException)
def _api_http_exception(e: HTTPException):
    """Rotas /api/* devolvem JSON (evita página HTML no browser)."""
    if request.path.startswith("/api/"):
        return jsonify(
            {
                "ok": False,
                "errors": [e.description or str(e)],
            }
        ), e.code or 500
    return e.get_response()


@app.route("/api/iptv/m3u")
def api_iptv_m3u():
    """
    Baixa e parseia uma playlist M3U no servidor (evita CORS no navegador).
    Query: ?url=...
    """
    rl = _iptv_rate_limit_or_429()
    if rl:
        return rl
    original_url = (request.args.get("url") or "").strip()
    url = maybe_github_raw(original_url)
    if not url:
        return jsonify({"ok": False, "errors": ["URL vazia"]}), 400
    try:
        ttl = int(app.config.get("IPTV_M3U_CACHE_TTL_SECONDS") or 0)
        cached = _iptv_cache.get(url, ttl_seconds=ttl)
        if cached:
            return jsonify(cached)

        def _fetch_once(target_url: str) -> dict[str, Any]:
            sess = requests.Session()
            return fetch_m3u_parse_streaming(
                sess,
                target_url,
                timeout=25,
                max_bytes=int(app.config.get("IPTV_MAX_BYTES") or 0),
                max_channels=int(app.config.get("IPTV_MAX_CHANNELS") or 0),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "*/*",
                },
                max_redirects=int(app.config.get("IPTV_MAX_REDIRECTS") or 3),
            )

        candidates = playlist_candidate_urls(original_url)
        attempted: list[str] = []
        last_err: IptvError | None = None
        out: dict[str, Any] | None = None
        used_url = ""
        for cand in candidates:
            attempted.append(cand)
            try:
                out = _fetch_once(cand)
                used_url = cand
                break
            except IptvError as e:
                last_err = e
                continue
        if out is None:
            if last_err:
                return jsonify(
                    {
                        "ok": False,
                        "errors": [last_err.message],
                        "message": last_err.message,
                        "source_url": original_url,
                        "fetched_url": url,
                        "attempted_urls": attempted,
                    }
                ), last_err.http_status
            return jsonify(
                {
                    "ok": False,
                    "errors": ["Falha ao baixar M3U."],
                    "message": "Falha ao baixar M3U.",
                    "source_url": original_url,
                    "fetched_url": url,
                    "attempted_urls": attempted,
                }
            ), 502

        chans = out["channels"]
        groups = out["groups"]
        payload = {
            "ok": True,
            "source_url": original_url,
            "fetched_url": used_url,
            "channels_count": len(chans),
            "groups": groups,
            "channels": chans,
        }
        _iptv_cache.set(used_url, payload)
        return jsonify(payload)
    except IptvError as e:
        return jsonify({"ok": False, "errors": [e.message], "source_url": original_url, "fetched_url": url}), e.http_status
    except requests.RequestException as e:
        return jsonify({"ok": False, "errors": [f"Falha ao baixar M3U: {e}"]}), 502
    except Exception as e:
        return jsonify({"ok": False, "errors": [f"Falha ao ler M3U: {e}"]}), 502


@app.route("/api/iptv/settings")
def api_iptv_settings_public():
    # Público: usado pela página principal para tocar o canal configurado no admin.
    return jsonify({"ok": True, "settings": _read_iptv_settings_public()})


@app.route("/api/iptv/probe")
def api_iptv_probe():
    """
    Faz um probe leve do stream/manifest (HEAD/GET curto) para ajudar o player no browser.
    Query: ?url=...
    """
    rl = _iptv_rate_limit_or_429()
    if rl:
        return rl
    original_url = (request.args.get("url") or "").strip()
    url = maybe_github_raw(original_url)
    if not url:
        return jsonify({"ok": False, "errors": ["URL vazia"]}), 400

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Range": "bytes=0-2047",
    }

    try:
        sess = requests.Session()
        p = probe_url(
            sess,
            url,
            timeout_head=12,
            timeout_get=18,
            headers_base=headers,
            max_redirects=int(app.config.get("IPTV_MAX_REDIRECTS") or 3),
        )
        return jsonify({"ok": True, "url": original_url, "fetched_url": url, **p})
    except IptvError as e:
        return jsonify({"ok": False, "errors": [e.message], "url": original_url, "fetched_url": url}), e.http_status
    except requests.RequestException as e:
        return jsonify({"ok": False, "errors": [f"Probe falhou: {e}"]}), 502
    except Exception as e:
        return jsonify({"ok": False, "errors": [f"Probe falhou: {e}"]}), 502


@app.route("/api/iptv/stream")
def api_iptv_stream():
    """
    Proxy de stream/manifest para contornar bloqueios de origem no browser.
    Query: ?url=...
    """
    rl = _iptv_rate_limit_or_429()
    if rl:
        return rl
    raw = (request.args.get("url") or "").strip()
    url = maybe_github_raw(raw)
    if not _is_http_url(url):
        return jsonify({"ok": False, "errors": ["URL inválida (use http/https)."]}), 400

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    rg = (request.headers.get("Range") or "").strip()
    if rg:
        headers["Range"] = rg

    try:
        upstream = requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=25,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return jsonify({"ok": False, "errors": [f"Falha ao buscar stream: {e}"]}), 502

    ct = _norm_ct(upstream.headers.get("Content-Type"))
    final_url = upstream.url or url
    if _is_hls_manifest(final_url, ct):
        try:
            manifest_text = upstream.content.decode("utf-8", errors="replace")
        finally:
            try:
                upstream.close()
            except Exception:
                pass
        rewritten = _rewrite_manifest_uris(manifest_text, final_url)
        return Response(
            rewritten,
            status=upstream.status_code,
            mimetype="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    pass_headers = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        v = upstream.headers.get(h)
        if v:
            pass_headers[h] = v
    pass_headers["Cache-Control"] = "no-store"
    pass_headers["Access-Control-Allow-Origin"] = "*"

    def _gen():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    return Response(_gen(), status=upstream.status_code, headers=pass_headers)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
