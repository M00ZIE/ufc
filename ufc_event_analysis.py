#!/usr/bin/env python3
"""
Analisa o card de um evento UFC (ufc.com.br): estatísticas dos atletas,
probabilidade heurística de vitória, método e faixa de round.

Use --externo para notícias/RSS/Reddit/X (via Google News) e mais feeds; --externo-lutadores principal|todos para lesão/bio por lutador.

Os dados vêm do HTML público do site. Odds de casas costumam ser via JS.
Previsões são aproximações estatísticas, não garantias.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from ufc_external_context import extract_event_query_from_ufc_page
    from ufc_external_context import run_report as external_context_report
except ImportError:
    extract_event_query_from_ufc_page = None  # type: ignore[assignment, misc]
    external_context_report = None  # type: ignore[assignment, misc]

DEFAULT_URL = "https://www.ufc.com.br/event/ufc-fight-night-march-28-2026"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
JSONAPI_HEADERS = {
    **HEADERS,
    "Accept": "application/vnd.api+json, application/json",
}


def _url_cache_path(cache_dir: Path, url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{h}.html"


# ufc.com.br costuma demorar >45s em horários de pico; leitura mais longa + várias tentativas.
_UFC_HTTP_CONNECT = 22.0
_UFC_HTTP_READ = 95.0
_UFC_HTTP_ATTEMPTS = 3


def _session_get_resilient(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    connect_timeout: float = _UFC_HTTP_CONNECT,
    read_timeout: float = _UFC_HTTP_READ,
    attempts: int = _UFC_HTTP_ATTEMPTS,
) -> requests.Response:
    """GET com timeout (connect, read) e re-tentativas em timeout / 502–504 / falha de ligação."""
    timeout = (connect_timeout, read_timeout)
    last: Optional[BaseException] = None
    r: Optional[requests.Response] = None
    for i in range(attempts):
        try:
            r = session.get(url, headers=headers, timeout=timeout)
            if r.status_code in (502, 503, 504) and i + 1 < attempts:
                time.sleep(1.25 * (i + 1))
                continue
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last = e
            if i + 1 < attempts:
                time.sleep(1.25 * (i + 1))
                continue
            raise
    if last is not None:
        raise last
    assert r is not None
    return r


def fetch_html(
    url: str,
    session: requests.Session,
    *,
    cache_dir: Optional[Path] = None,
    cache_max_age_seconds: Optional[float] = None,
    force_refresh: bool = False,
) -> str:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _url_cache_path(cache_dir, url)
        if not force_refresh and path.is_file():
            age = time.time() - path.stat().st_mtime
            if cache_max_age_seconds is None or age <= cache_max_age_seconds:
                return path.read_text(encoding="utf-8", errors="replace")

    r = _session_get_resilient(session, url, headers=HEADERS)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    text = r.text
    if cache_dir is not None:
        path = _url_cache_path(cache_dir, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text


@dataclass
class HistoryBrief:
    """Últimas lutas listadas no HTML do perfil (o site pode truncar a lista)."""

    total_in_page: int
    last5_w: int
    last5_l: int
    last5_ko_losses: int
    last5_sub_losses: int
    sequence: str  # ex.: W-L-W-W-W (mais recente primeiro)
    line_detail: str  # uma linha legível com métodos


@dataclass
class FighterProfile:
    slug: str
    name: str
    wins: int
    losses: int
    draws: int
    ufc_rank: Optional[int]
    ko_wins: int = 0
    dec_wins: int = 0
    sub_wins: int = 0
    first_round_finishes: int = 0
    sig_str_lpm: Optional[float] = None
    sig_str_abs_lpm: Optional[float] = None
    td_avg: Optional[float] = None
    sub_per_15: Optional[float] = None
    str_def_pct: Optional[float] = None
    td_def_pct: Optional[float] = None
    kd_avg: Optional[float] = None
    avg_fight_minutes: Optional[float] = None
    history: Optional[HistoryBrief] = None


def _absolute_image_url(url: Optional[str]) -> Optional[str]:
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    if u.startswith("//"):
        return "https:" + u
    return u


@dataclass
class FightRow:
    fmid: str
    division: str
    red_name: str
    blue_name: str
    red_slug: str
    blue_slug: str
    red_rank_card: Optional[int]
    blue_rank_card: Optional[int]
    red_photo_url: Optional[str] = None
    blue_photo_url: Optional[str] = None
    listing_status: str = ""
    # Preenchidos quando o site já exibe resultado no card do evento
    result_winner_side: Optional[str] = None  # "red" | "blue"
    result_method_text: Optional[str] = None
    result_round: Optional[str] = None
    result_time: Optional[str] = None


def parse_record(html: str) -> tuple[int, int, int]:
    m = re.search(
        r'class="hero-profile__division-body">\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*\(V-D-E\)',
        html,
        re.I,
    )
    if not m:
        raise ValueError("Cartel (W-L-D) não encontrado no HTML do atleta.")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_profile_rank(html: str) -> Optional[int]:
    m = re.search(r'class="hero-profile__tag"[^>]*>\s*#(\d+)', html)
    if m:
        return int(m.group(1))
    return None


def _parse_float_loose(s: str) -> Optional[float]:
    s = s.strip().replace(",", ".").replace("%", "")
    if not s or s == "—":
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _stat_number_text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).replace("%", "").strip()


def parse_wins_by_method(soup: BeautifulSoup) -> tuple[int, int, int]:
    ko, dec_, sub = 0, 0, 0
    for bar in soup.select("div.c-stat-3bar"):
        title = bar.select_one("h2.c-stat-3bar__title")
        if not title:
            continue
        t = title.get_text(strip=True)
        if "Vitórias por Método" not in t and "Wins by Method" not in t:
            continue
        for group in bar.select(".c-stat-3bar__group"):
            lab = group.select_one(".c-stat-3bar__label")
            val = group.select_one(".c-stat-3bar__value")
            if not lab or not val:
                continue
            label = lab.get_text(strip=True).upper()
            vm = re.match(r"(\d+)", val.get_text(strip=True))
            n = int(vm.group(1)) if vm else 0
            if "KO" in label or "TKO" in label:
                ko = n
            elif "DEC" in label:
                dec_ = n
            elif "FIN" in label or "SUB" in label:
                sub = n
        break
    return ko, dec_, sub


def parse_hero_finish_stats(soup: BeautifulSoup) -> tuple[int, int, int]:
    """Vitórias por Nocaute, Finalização, 1º round (hero)."""
    ko, sub, r1 = 0, 0, 0
    for st in soup.select(".hero-profile__stat"):
        txt = st.select_one(".hero-profile__stat-text")
        num = st.select_one(".hero-profile__stat-numb")
        if not txt or not num:
            continue
        label = txt.get_text(strip=True).lower()
        try:
            v = int(num.get_text(strip=True))
        except ValueError:
            continue
        if "nocaute" in label or "knockout" in label:
            ko = v
        elif "finalização" in label or "submission" in label:
            sub = v
        elif "1º" in label or "first round" in label:
            r1 = v
    return ko, sub, r1


def parse_compare_stats(soup: BeautifulSoup) -> dict[str, float]:
    out: dict[str, float] = {}
    for block in soup.select("div.c-stat-compare"):
        for cls in ("c-stat-compare__group-1", "c-stat-compare__group-2"):
            grp = block.select_one(f".{cls}")
            if not grp:
                continue
            lab_el = grp.select_one(".c-stat-compare__label")
            suf_el = grp.select_one(".c-stat-compare__label-suffix")
            num_el = grp.select_one(".c-stat-compare__number")
            if not lab_el or not num_el:
                continue
            label = lab_el.get_text(strip=True)
            if suf_el:
                label = f"{label} {suf_el.get_text(strip=True)}"
            raw = _stat_number_text(num_el)
            val = _parse_float_loose(raw)
            if val is not None:
                out[label] = val
    return out


def merge_profile_stats(
    soup: BeautifulSoup,
    wins: int,
    ko_bar: int,
    dec_bar: int,
    sub_bar: int,
    ko_h: int,
    sub_h: int,
    r1_h: int,
) -> tuple[int, int, int, int]:
    ko, dec_, sub = ko_bar, dec_bar, sub_bar
    if ko + dec_ + sub == 0 and wins > 0:
        ko, sub = ko_h, sub_h
        dec_ = max(0, wins - ko - sub)
    fr = r1_h
    return ko, dec_, sub, fr


def build_fighter_profile(html: str, slug: str) -> FighterProfile:
    soup = BeautifulSoup(html, "html.parser")
    w, l, d = parse_record(html)
    rank = parse_profile_rank(html)
    h1 = soup.select_one("h1.hero-profile__name")
    name = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()

    ko_b, dec_b, sub_b = parse_wins_by_method(soup)
    ko_h, sub_h, r1_h = parse_hero_finish_stats(soup)
    ko, dec_, sub, fr = merge_profile_stats(soup, w, ko_b, dec_b, sub_b, ko_h, sub_h, r1_h)

    cmp_ = parse_compare_stats(soup)

    def pick(*keys: str) -> Optional[float]:
        for k in keys:
            if k in cmp_:
                return cmp_[k]
        return None

    sig_lpm = pick("Golpes Sig. Conectados Por Minuto", "Sig. Strikes Landed Per Min")
    sig_abs = pick("Golpes Sig. Absorvidos Por Minuto", "Sig. Strikes Absorbed Per Min")
    td_av = pick("Média de quedas Por 15 Min", "Average Takedowns Landed per 15 minutes")
    sub15 = pick(
        "Média de finalizações Por 15 Min",
        "Average Submissions Attempted per 15 minutes",
    )
    str_def = pick("Defesa de Golpes Sig.", "Sig. Str. Defense")
    td_def = pick("Defesa De Quedas", "Takedown Defense")
    kd = pick("Média de Knockdowns", "Knockdown Avg")
    avg_min = pick("Tempo médio de luta", "Average fight time")
    hist = parse_fight_history(soup, slug)

    return FighterProfile(
        slug=slug,
        name=name,
        wins=w,
        losses=l,
        draws=d,
        ufc_rank=rank,
        ko_wins=ko,
        dec_wins=dec_,
        sub_wins=sub,
        first_round_finishes=fr,
        sig_str_lpm=sig_lpm,
        sig_str_abs_lpm=sig_abs,
        td_avg=td_av,
        sub_per_15=sub15,
        str_def_pct=str_def,
        td_def_pct=td_def,
        kd_avg=kd,
        avg_fight_minutes=avg_min,
        history=hist,
    )


def athlete_slug_from_url(href: str) -> str:
    path = urlparse(href).path.strip("/")
    parts = path.split("/")
    if "athlete" in parts:
        i = parts.index("athlete")
        if i + 1 < len(parts):
            return parts[i + 1]
    raise ValueError(f"URL de atleta inválida: {href}")


def _base_origin_from_page_url(page_url: str) -> str:
    p = urlparse((page_url or "").strip())
    if p.scheme in ("http", "https") and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.ufc.com.br"


def resolve_athlete_href_to_slug(
    href: str,
    session: requests.Session,
    *,
    base: str = "https://www.ufc.com.br",
) -> str:
    """
    O card por vezes usa links internos Drupal ``/node/N`` em vez de ``/athlete/slug``.
    Segue redirects e, se preciso, lê ``<link rel=\"canonical\">`` no HTML.
    """
    try:
        return athlete_slug_from_url(href)
    except ValueError:
        pass
    root = base.rstrip("/")
    abs_url = urljoin(root + "/", (href or "").strip())
    p = urlparse(abs_url)
    path_norm = (p.path or "").strip("/")
    if not re.fullmatch(r"node/\d+", path_norm, flags=re.I):
        raise ValueError(f"URL de atleta inválida: {href}")
    r = _session_get_resilient(
        session,
        abs_url,
        headers=HEADERS,
        read_timeout=40.0,
        attempts=2,
    )
    r.raise_for_status()
    final = (r.url or "").strip()
    if final:
        try:
            return athlete_slug_from_url(final)
        except ValueError:
            pass
    chunk = (r.text or "")[:150000]
    can = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        chunk,
        re.I,
    )
    if can:
        try:
            return athlete_slug_from_url(can.group(1).strip())
        except ValueError:
            pass
    og = re.search(
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
        chunk,
        re.I,
    )
    if og:
        try:
            return athlete_slug_from_url(og.group(1).strip())
        except ValueError:
            pass
    # Perfil novo: canonical fica em /node/N e /athlete/slug ainda não existe (404→busca).
    # O HTML do node replica o layout do perfil (hero-profile).
    parts_nid = path_norm.split("/")
    if (
        len(parts_nid) == 2
        and parts_nid[0].lower() == "node"
        and parts_nid[1].isdigit()
        and "hero-profile" in chunk.lower()
        and "hero-profile__name" in chunk.lower()
    ):
        return f"node-{parts_nid[1]}"
    raise ValueError(f"Não foi possível resolver o atleta (node) a partir de: {href}")


def _categorize_result_method(raw: str) -> str:
    u = raw.upper().replace("Á", "A").replace("Ã", "A")
    if any(x in u for x in ("KO", "TKO", "NOCAUTE", "NOCK", "DOCTOR", "MÉDICO", "MÉDIC")):
        return "KO"
    if any(x in u for x in ("SUB", "FINALIZ", "SUBM", "CHAVE", "GUILHOTINA", "MATA", "STRANG")):
        return "SUB"
    if any(x in u for x in ("DEC", "DECIS", "UNÂNIME", "UNANIME", "MAIORIA", "SPLIT")):
        return "DEC"
    return "OUT"


def parse_fight_history(soup: BeautifulSoup, slug: str) -> HistoryBrief:
    """Histórico em `article.c-card-event--athlete-results` (ordem: mais recente primeiro)."""
    bouts: list[tuple[bool, str]] = []
    for art in soup.select("article.c-card-event--athlete-results"):
        red = art.select_one(".c-card-event--athlete-results__red-image")
        blue = art.select_one(".c-card-event--athlete-results__blue-image")
        if not red or not blue:
            continue
        r_link = red.select_one("a[href*='/athlete/']")
        b_link = blue.select_one("a[href*='/athlete/']")
        if not r_link or not b_link:
            continue
        try:
            rs = athlete_slug_from_url(r_link["href"])
            bs = athlete_slug_from_url(b_link["href"])
        except (ValueError, KeyError):
            continue
        if slug not in (rs, bs):
            continue
        side = red if rs == slug else blue
        is_win = "win" in (side.get("class") or [])
        method_raw = ""
        for row in art.select(".c-card-event--athlete-results__result"):
            lab = row.select_one(".c-card-event--athlete-results__result-label")
            txt = row.select_one(".c-card-event--athlete-results__result-text")
            if not lab or not txt:
                continue
            ltxt = lab.get_text(strip=True).lower()
            if "método" in ltxt or "method" in ltxt:
                method_raw = txt.get_text(strip=True)
                break
        cat = _categorize_result_method(method_raw) if method_raw else "OUT"
        bouts.append((is_win, cat))

    take = bouts[:5]
    w5 = sum(1 for w, _ in take if w)
    l5 = sum(1 for w, _ in take if not w)
    kl5 = sum(1 for w, m in take if not w and m == "KO")
    sl5 = sum(1 for w, m in take if not w and m == "SUB")
    seq_ch: list[str] = []
    detail_bits: list[str] = []
    for is_win, m in take:
        seq_ch.append("W" if is_win else "L")
        detail_bits.append(f"{'V' if is_win else 'D'}({m})")
    seq = "-".join(seq_ch) if seq_ch else "—"
    det = " | ".join(detail_bits) if detail_bits else "sem detalhe no card"

    return HistoryBrief(
        total_in_page=len(bouts),
        last5_w=w5,
        last5_l=l5,
        last5_ko_losses=kl5,
        last5_sub_losses=sl5,
        sequence=seq,
        line_detail=det,
    )


def _corner_outcome_win_loss(block, corner: str) -> Optional[str]:
    body = block.select_one(f".c-listing-fight__corner-body--{corner}")
    if not body:
        return None
    wrap = body.select_one(".c-listing-fight__outcome-wrapper")
    if wrap:
        if wrap.select_one(".c-listing-fight__outcome--win") or wrap.select_one(
            '[class*="outcome--win"]'
        ):
            return "win"
        if wrap.select_one(".c-listing-fight__outcome--loss") or wrap.select_one(
            '[class*="outcome--loss"]'
        ):
            return "loss"
        oc = wrap.select_one(".c-listing-fight__outcome")
        if oc:
            raw = oc.get_text(" ", strip=True).lower()
            if raw:
                if re.match(r"^(win|vit[oó]ria|victory)\b", raw, re.I):
                    return "win"
                if re.match(r"^(loss|derrota|defeat)\b", raw, re.I):
                    return "loss"
    if body.select_one(".c-listing-fight__outcome--win"):
        return "win"
    if body.select_one(".c-listing-fight__outcome--loss"):
        return "loss"
    return None


def _parse_listing_result_cells(block) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Round, tempo e método — percorre desktop + mobile (o site costuma esconder um deles no SSR)."""
    round_t = None
    time_t = None
    method_t = None
    for res_root in block.select(".js-listing-fight__results"):
        if not method_t:
            meth_el = res_root.select_one(".c-listing-fight__result-text.method")
            if meth_el:
                mt = meth_el.get_text(" ", strip=True) or ""
                if mt:
                    method_t = mt
        if not round_t:
            r_el = res_root.select_one(".c-listing-fight__result-text.round")
            if r_el:
                rt = r_el.get_text(" ", strip=True) or ""
                if rt:
                    round_t = rt
        if not time_t:
            t_el = res_root.select_one(".c-listing-fight__result-text.time")
            if t_el:
                tt = t_el.get_text(" ", strip=True) or ""
                if tt:
                    time_t = tt
        if method_t and round_t and time_t:
            break
    return round_t, time_t, method_t


def paired_ufc_event_mirror_url(url: str) -> Optional[str]:
    """
    Outro host oficial do mesmo evento (mesmo path):
    ufc.com.br ↔ ufc.com. O HTML costuma ser híbrido (muito resultado só no JS);
    fundir as duas versões recupera mais lutas com Win/Loss no SSR.
    """
    try:
        p = urlparse((url or "").strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        host = p.netloc.lower()
        if "ufc.com.br" in host:
            new_netloc = host.replace("ufc.com.br", "ufc.com", 1)
            return urlunparse((p.scheme, new_netloc, p.path, p.params, p.query, p.fragment))
        if host.endswith("ufc.com"):
            new_netloc = host[:-3] + "com.br"
            return urlunparse((p.scheme, new_netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        return None
    return None


def drupal_event_nid_from_html(event_html: str) -> Optional[int]:
    """Lê o nid do nó Drupal a partir do bloco drupal-settings-json (página de evento)."""
    m = re.search(r'"currentPath":"node/(\d+)"', event_html or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def jsonapi_origin_for_event_url(event_url: str) -> str:
    """Origem para /jsonapi (mesmo host que a página do evento quando for ufc.com / ufc.com.br)."""
    p = urlparse((event_url or "").strip())
    if p.scheme in ("http", "https") and p.netloc and "ufc.com" in p.netloc.lower():
        return f"{p.scheme}://{p.netloc}"
    return "https://www.ufc.com"


def _jsonapi_event_cache_path(cache_dir: Path, event_nid: int) -> Path:
    return cache_dir / f"event_nid_{event_nid}_fightcard.json"


def fetch_event_fights_jsonapi(
    session: requests.Session,
    *,
    jsonapi_origin_url: str,
    event_nid: int,
    cache_dir: Optional[Path] = None,
    cache_max_age_seconds: Optional[float] = None,
    force_refresh: bool = False,
) -> Optional[dict[str, Any]]:
    """
    Card de lutas com resultados via JSON:API (fightmetric_id = data-fmid do HTML).
    Resposta enxuta (~30KB) com método, round, tempo e outcome por canto.
    """
    root = jsonapi_origin_url.rstrip("/")
    q = {
        "filter[drupal_internal__nid]": str(event_nid),
        "include": "fights.red_corner,fights.blue_corner,fights.fight_final_winner",
        "fields[node--fight]": (
            "fightmetric_id,red_corner_fight_outcome,blue_corner_fight_outcome,"
            "fight_final_method,fight_final_round,fight_final_time,title"
        ),
        "fields[node--athlete]": "title,path",
    }
    url = f"{root}/jsonapi/node/event?{urlencode(q)}"

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _jsonapi_event_cache_path(cache_dir, event_nid)
        if not force_refresh and path.is_file():
            age = time.time() - path.stat().st_mtime
            if cache_max_age_seconds is None or age <= cache_max_age_seconds:
                try:
                    raw = path.read_text(encoding="utf-8")
                    return json.loads(raw)
                except (json.JSONDecodeError, OSError):
                    pass

    try:
        r = _session_get_resilient(session, url, headers=JSONAPI_HEADERS)
        r.raise_for_status()
        doc = r.json()
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return None

    if cache_dir is not None:
        try:
            path = _jsonapi_event_cache_path(cache_dir, event_nid)
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return doc if isinstance(doc, dict) else None


def _jsonapi_winner_side_from_relationships(fight: dict[str, Any]) -> Optional[str]:
    """fight_final_winner (UUID) igual a red_corner ou blue_corner → red | blue."""
    rel = fight.get("relationships")
    if not isinstance(rel, dict):
        return None
    fw = rel.get("fight_final_winner")
    if not isinstance(fw, dict):
        return None
    data = fw.get("data")
    if not isinstance(data, dict):
        return None
    wid = data.get("id")
    if not wid:
        return None
    rc = rel.get("red_corner")
    bc = rel.get("blue_corner")
    rcd = rc.get("data") if isinstance(rc, dict) else None
    bcd = bc.get("data") if isinstance(bc, dict) else None
    rid = rcd.get("id") if isinstance(rcd, dict) else None
    bid = bcd.get("id") if isinstance(bcd, dict) else None
    if rid and wid == rid:
        return "red"
    if bid and wid == bid:
        return "blue"
    return None


def parse_jsonapi_fight_enrichment_by_fmid(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """fightmetric_id → {attributes, winner_side_api (red|blue|None)}."""
    out: dict[str, dict[str, Any]] = {}
    inc = doc.get("included")
    if not isinstance(inc, list):
        return out
    for item in inc:
        if not isinstance(item, dict) or item.get("type") != "node--fight":
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            continue
        fmid = attrs.get("fightmetric_id")
        if fmid is None:
            continue
        key = str(int(fmid)) if isinstance(fmid, (int, float)) else str(fmid).strip()
        if not key:
            continue
        out[key] = {
            "attributes": attrs,
            "winner_side_api": _jsonapi_winner_side_from_relationships(item),
        }
    return out


def parse_jsonapi_fights_attrs_by_fmid(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compat: fightmetric_id → só attributes (para chamadas legadas)."""
    return {k: v["attributes"] for k, v in parse_jsonapi_fight_enrichment_by_fmid(doc).items()}


def _merge_fight_row_with_jsonapi_attrs(
    fr: FightRow,
    api_attrs: dict[str, Any],
    *,
    winner_side_api: Optional[str] = None,
) -> FightRow:
    method_raw = api_attrs.get("fight_final_method")
    method_s = str(method_raw).strip() if method_raw is not None else ""
    ro = str(api_attrs.get("red_corner_fight_outcome") or "").strip()
    bo = str(api_attrs.get("blue_corner_fight_outcome") or "").strip()
    rl = ro.lower()
    bl = bo.lower()
    has_pair = (rl == "win" and bl == "loss") or (bl == "win" and rl == "loss")
    ws = winner_side_api if winner_side_api in ("red", "blue") else None
    if not has_pair and not method_s and not ws:
        return fr

    round_v = api_attrs.get("fight_final_round")
    time_v = api_attrs.get("fight_final_time")
    round_s = str(round_v).strip() if round_v is not None else ""
    time_s = str(time_v).strip() if time_v is not None else ""

    bucket = categorize_ufc_listing_method(method_s if method_s else None)
    new_winner = fr.result_winner_side
    if bucket == "empate" or bucket == "outro":
        new_winner = None
    elif rl == "win" and bl == "loss":
        new_winner = "red"
    elif bl == "win" and rl == "loss":
        new_winner = "blue"
    elif ws is not None:
        new_winner = ws

    new_method = method_s or (fr.result_method_text or "") or None
    new_round = round_s or fr.result_round or None
    new_time = time_s or fr.result_time or None

    return FightRow(
        fmid=fr.fmid,
        division=fr.division,
        red_name=fr.red_name,
        blue_name=fr.blue_name,
        red_slug=fr.red_slug,
        blue_slug=fr.blue_slug,
        red_rank_card=fr.red_rank_card,
        blue_rank_card=fr.blue_rank_card,
        red_photo_url=fr.red_photo_url,
        blue_photo_url=fr.blue_photo_url,
        listing_status=fr.listing_status,
        result_winner_side=new_winner,
        result_method_text=new_method,
        result_round=new_round,
        result_time=new_time,
    )


def enrich_fight_rows_from_jsonapi(rows: list[FightRow], doc: Optional[dict[str, Any]]) -> list[FightRow]:
    if not doc:
        return rows
    by_fmid = parse_jsonapi_fight_enrichment_by_fmid(doc)
    if not by_fmid:
        return rows
    out: list[FightRow] = []
    for fr in rows:
        k = (fr.fmid or "").strip()
        if k in by_fmid:
            p = by_fmid[k]
            out.append(
                _merge_fight_row_with_jsonapi_attrs(
                    fr,
                    p["attributes"],
                    winner_side_api=p.get("winner_side_api"),
                )
            )
        else:
            out.append(fr)
    return out


def _merge_fight_row_pair(a: FightRow, b: FightRow) -> FightRow:
    """Une duas linhas do mesmo fmid (ex.: BR + .com) escolhendo o que tiver mais dado de resultado."""
    def score(fr: FightRow) -> int:
        s = 0
        if fr.result_winner_side:
            s += 5
        if fr.result_method_text:
            s += 3
        if fr.result_round:
            s += 1
        if fr.result_time:
            s += 1
        return s

    first, second = (a, b) if score(a) >= score(b) else (b, a)
    ws = first.result_winner_side or second.result_winner_side
    if (
        first.result_winner_side
        and second.result_winner_side
        and first.result_winner_side != second.result_winner_side
    ):
        ws = first.result_winner_side
    return FightRow(
        fmid=first.fmid or second.fmid,
        division=first.division or second.division,
        red_name=first.red_name or second.red_name,
        blue_name=first.blue_name or second.blue_name,
        red_slug=first.red_slug or second.red_slug,
        blue_slug=first.blue_slug or second.blue_slug,
        red_rank_card=first.red_rank_card if first.red_rank_card is not None else second.red_rank_card,
        blue_rank_card=first.blue_rank_card if first.blue_rank_card is not None else second.blue_rank_card,
        red_photo_url=first.red_photo_url or second.red_photo_url,
        blue_photo_url=first.blue_photo_url or second.blue_photo_url,
        listing_status=(first.listing_status or second.listing_status or "").strip() or "",
        result_winner_side=ws,
        result_method_text=first.result_method_text or second.result_method_text,
        result_round=first.result_round or second.result_round,
        result_time=first.result_time or second.result_time,
    )


def merge_fight_card_rows(primary: list[FightRow], secondary: list[FightRow]) -> list[FightRow]:
    if not secondary:
        return primary
    by_fmid: dict[str, FightRow] = {}
    for fr in secondary:
        k = (fr.fmid or "").strip()
        if k:
            by_fmid[k] = fr
    out: list[FightRow] = []
    for fr in primary:
        k = (fr.fmid or "").strip()
        if k and k in by_fmid:
            out.append(_merge_fight_row_pair(fr, by_fmid[k]))
        else:
            out.append(fr)
    seen = {((x.fmid or "").strip()) for x in primary if (x.fmid or "").strip()}
    for fr in secondary:
        k = (fr.fmid or "").strip()
        if k and k not in seen:
            out.append(fr)
    return out


def categorize_ufc_listing_method(raw: Optional[str]) -> Optional[str]:
    """
    Agrupa texto do site (PT/EN) em buckets compatíveis com predicted_method do modelo:
    KO/TKO, Decisão, Finalização.
    """
    if not raw:
        return None
    s = raw.lower()
    if "no contest" in s or "sem resultado" in s:
        return "outro"
    if "draw" in s or "empate" in s:
        return "empate"
    if "decision" in s or "decis" in s:
        return "decisao"
    if "ko" in s or "tko" in s or "nocaute" in s or "knockout" in s or "doctor" in s:
        return "ko_tko"
    if (
        "sub" in s
        or "finaliza" in s
        or "guillotine" in s
        or "choke" in s
        or "rear-naked" in s
        or "armbar" in s
        or "triangle" in s
        or "kimura" in s
    ):
        return "finalizacao"
    return None


def predicted_method_to_bucket(meth: Optional[str]) -> Optional[str]:
    if not meth:
        return None
    t = meth.strip().lower()
    if "ko" in t or "tko" in t:
        return "ko_tko"
    if "decis" in t:
        return "decisao"
    if "final" in t:
        return "finalizacao"
    return None


def parse_fight_card(
    event_html: str,
    *,
    session: Optional[requests.Session] = None,
    base: str = "https://www.ufc.com.br",
) -> list[FightRow]:
    soup = BeautifulSoup(event_html, "html.parser")
    fights: list[FightRow] = []
    node_slug_cache: dict[str, str] = {}
    for block in soup.select("div.c-listing-fight"):
        fmid = block.get("data-fmid") or ""
        listing_status = block.get("data-status") or ""
        class_el = block.select_one(".c-listing-fight__class--mobile .c-listing-fight__class-text")
        division = (class_el.get_text(strip=True) if class_el else "") or "—"

        red_a = block.select_one(".c-listing-fight__corner-name--red a")
        blue_a = block.select_one(".c-listing-fight__corner-name--blue a")
        if not red_a or not blue_a:
            continue

        def corner_display_name(link) -> str:
            given = link.select_one(".c-listing-fight__corner-given-name")
            family = link.select_one(".c-listing-fight__corner-family-name")
            if given or family:
                return f"{given.get_text(strip=True) if given else ''} {family.get_text(strip=True) if family else ''}".strip()
            return link.get_text(strip=True)

        red_name = corner_display_name(red_a)
        blue_name = corner_display_name(blue_a)

        def slug_from_corner(link) -> str:
            href = (link.get("href") or "").strip()
            if not href:
                raise ValueError("Link do canto sem href.")
            if href in node_slug_cache:
                return node_slug_cache[href]
            try:
                slug = athlete_slug_from_url(href)
            except ValueError:
                if session is None:
                    raise
                slug = resolve_athlete_href_to_slug(href, session, base=base)
            node_slug_cache[href] = slug
            return slug

        red_slug = slug_from_corner(red_a)
        blue_slug = slug_from_corner(blue_a)

        rank_spans = block.select(".c-listing-fight__class--mobile .c-listing-fight__corner-rank span")
        red_rank_card: Optional[int] = None
        blue_rank_card: Optional[int] = None
        if len(rank_spans) >= 2:
            for label, target in ((rank_spans[0], "red"), (rank_spans[1], "blue")):
                t = label.get_text(strip=True)
                mm = re.match(r"#(\d+)", t)
                if mm:
                    if target == "red":
                        red_rank_card = int(mm.group(1))
                    else:
                        blue_rank_card = int(mm.group(1))

        img_r = block.select_one(".c-listing-fight__corner-image--red img")
        img_b = block.select_one(".c-listing-fight__corner-image--blue img")
        red_photo = _absolute_image_url(img_r.get("src") if img_r else None)
        blue_photo = _absolute_image_url(img_b.get("src") if img_b else None)

        rw = _corner_outcome_win_loss(block, "red")
        bw = _corner_outcome_win_loss(block, "blue")
        result_winner_side: Optional[str] = None
        if rw == "win" and bw == "loss":
            result_winner_side = "red"
        elif bw == "win" and rw == "loss":
            result_winner_side = "blue"
        r_round, r_time, r_method = _parse_listing_result_cells(block)
        if r_method:
            low_m = r_method.lower()
            if "no contest" in low_m or "sem resultado" in low_m:
                result_winner_side = None

        fights.append(
            FightRow(
                fmid=fmid,
                division=division,
                red_name=red_name,
                blue_name=blue_name,
                red_slug=red_slug,
                blue_slug=blue_slug,
                red_rank_card=red_rank_card,
                blue_rank_card=blue_rank_card,
                red_photo_url=red_photo,
                blue_photo_url=blue_photo,
                listing_status=listing_status,
                result_winner_side=result_winner_side,
                result_method_text=r_method,
                result_round=r_round,
                result_time=r_time,
            )
        )
    return fights


def build_event_results_payload(
    event_url: str,
    event_html: str,
    *,
    mirror_url: Optional[str] = None,
    mirror_html: Optional[str] = None,
    jsonapi_doc: Optional[dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Lutas do card com resultados oficiais (HTML + opcional JSON:API Drupal)."""
    meta = extract_event_page_meta(event_html)
    base = _base_origin_from_page_url(event_url)
    rows = parse_fight_card(event_html, session=session, base=base)
    if mirror_html:
        rows_m = parse_fight_card(mirror_html, session=session, base=base)
        rows = merge_fight_card_rows(rows, rows_m)
    rows = enrich_fight_rows_from_jsonapi(rows, jsonapi_doc)
    fights_out: list[dict[str, Any]] = []
    for i, fr in enumerate(rows, 1):
        bucket = categorize_ufc_listing_method(fr.result_method_text)
        side = fr.result_winner_side
        wname: Optional[str] = None
        status = "scheduled"
        if bucket == "empate":
            status = "draw"
            side = None
        elif bucket == "outro":
            status = "no_contest"
            side = None
        elif side == "red":
            wname = fr.red_name
            status = "completed"
        elif side == "blue":
            wname = fr.blue_name
            status = "completed"
        fights_out.append(
            {
                "index": i,
                "fmid": fr.fmid,
                "division": fr.division,
                "red_name": fr.red_name,
                "blue_name": fr.blue_name,
                "red_slug": fr.red_slug or None,
                "blue_slug": fr.blue_slug or None,
                "status": status,
                "winner_side": side,
                "winner_name": wname,
                "method_text": fr.result_method_text,
                "method_bucket": bucket,
                "round": fr.result_round,
                "time": fr.result_time,
                "listing_status": fr.listing_status or None,
            }
        )
    out: dict[str, Any] = {
        "event_url": event_url,
        "event_title": meta.get("og_title"),
        "event_starts_at": meta.get("event_starts_at"),
        "hero_image_url": meta.get("hero_image_url"),
        "fights": fights_out,
        "fights_count": len(fights_out),
    }
    jidx = parse_jsonapi_fight_enrichment_by_fmid(jsonapi_doc) if jsonapi_doc else {}
    parts: list[str] = []
    if jidx:
        parts.append(
            "Os resultados são enriquecidos pelo JSON:API oficial (Drupal) do site UFC, usando o mesmo id da luta "
            "que aparece no card (fightmetric_id / data-fmid). Esta fonte costuma refletir o placar antes do HTML "
            "estático mostrar Win/Loss em todas as lutas."
        )
    if mirror_url:
        out["mirror_url"] = mirror_url
        parts.append(
            "Também foi feita fusão entre duas versões regionais da página (.com.br e .com) quando disponível."
        )
    if parts:
        out["results_note"] = " ".join(parts)
    else:
        out["results_note"] = (
            "O site UFC muitas vezes injeta resultados via JavaScript. Se faltar luta, marque «Ignorar cache» "
            "após o evento ou abra o evento no domínio alternativo (.com / .com.br)."
        )
    return out


def _normalize_meta_url(raw: str) -> str:
    return raw.strip().replace("&amp;", "&").replace("&#038;", "&")


def _extract_hero_image_url(event_html: str) -> Optional[str]:
    """
    Imagem principal do evento para o hero.

    O site costuma expor `og:image`, mas em muitas páginas ufc.com.br isso vem vazio;
    a arte oficial aparece em <picture>/<img> como URL ...EVENT-ART.jpg em ufc.com/images/.
    """
    pats = (
        r'<meta\s+property="og:image"\s+content="([^"]+)"',
        r'<meta\s+content="([^"]+)"\s+property="og:image"',
        r'<meta\s+property="og:image:secure_url"\s+content="([^"]+)"',
        r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
    )
    for pat in pats:
        m = re.search(pat, event_html, re.I)
        if m:
            return _normalize_meta_url(m.group(1))
    for frag in (
        "background_image_xl",
        "background_image_lg",
        "background_image_md",
        "background_image_sm",
    ):
        m = re.search(
            rf'(https://(?:www\.)?ufc\.com/images/styles/{frag}/s3/[^"\s<>]*EVENT-ART[^"\s<>]*\.(?:jpg|jpeg|png|webp)[^"\s<>]*)',
            event_html,
            re.I,
        )
        if m:
            return _normalize_meta_url(m.group(1))
    m = re.search(
        r'(https://(?:www\.)?ufc\.com[^"\s<>]*EVENT-ART[^"\s<>]*\.(?:jpg|jpeg|png|webp)[^"\s<>]*)',
        event_html,
        re.I,
    )
    if m:
        return _normalize_meta_url(m.group(1))
    m = re.search(r'"image"\s*:\s*"(https://[^"]+)"', event_html)
    if m and "ufc" in m.group(1).lower():
        return _normalize_meta_url(m.group(1))
    return None


def extract_event_hero_timestamp_unix(event_html: str) -> Optional[int]:
    """
    Epoch Unix (segundos) do atributo data-timestamp no hero (início do evento no site).
    Usado para filtrar eventos já realizados quando o slug não traz data (ex.: /event/ufc-326).
    """
    for pattern in (
        r'c-hero__headline-suffix[\s\S]{0,500}?data-timestamp="(\d{10,})"',
        r'hero-fixed-bar__date--mobile[\s\S]{0,200}?data-timestamp="(\d{10,})"',
        r'hero-fixed-bar__date tz-change-inner[\s\S]{0,200}?data-timestamp="(\d{10,})"',
        r'c-event-fight-card-broadcaster__time[\s\S]{0,120}?data-timestamp="(\d{10,})"',
    ):
        m = re.search(pattern, event_html, re.I)
        if m:
            try:
                ts = int(m.group(1))
            except ValueError:
                continue
            if 1_000_000_000 < ts < 5_000_000_000:
                return ts
    return None


def _extract_event_starts_at_iso(event_html: str) -> Optional[str]:
    """
    Data/hora do evento: JSON-LD startDate (ufc.com) ou data-timestamp Unix no hero (ufc.com.br).
    """
    m = re.search(r'"startDate"\s*:\s*"([^"]+)"', event_html)
    if m:
        return m.group(1).strip()
    ts = extract_event_hero_timestamp_unix(event_html)
    if ts is not None:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    return None


def extract_event_page_meta(event_html: str) -> dict[str, Any]:
    """Imagem do hero (og: ou EVENT-ART), og:title, data/hora do evento."""
    meta: dict[str, Any] = {"hero_image_url": None, "event_starts_at": None, "og_title": None}
    meta["hero_image_url"] = _extract_hero_image_url(event_html)
    mt = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', event_html, re.I)
    if mt:
        meta["og_title"] = re.sub(r"\s+", " ", mt.group(1)).strip()
    meta["event_starts_at"] = _extract_event_starts_at_iso(event_html)
    return meta


def win_rate(w: int, l: int, d: int) -> float:
    total = w + l + d
    if total <= 0:
        return 0.5
    return (w + 0.5 * d) / total


def rank_score(rank_card: Optional[int], rank_profile: Optional[int]) -> float:
    r = rank_card if rank_card is not None else rank_profile
    if r is None:
        return 16.0
    return float(r)


def _history_logit_edge_red(a: FighterProfile, b: FighterProfile) -> float:
    """Forma recente (últimas 5 no site) + vulnerabilidade a nocaute/sub nos últimos resultados."""
    fa = fb = 0.0
    va = vb = 0.0
    if a.history:
        fa = 0.10 * (a.history.last5_w - a.history.last5_l)
        va = 0.06 * min(3, a.history.last5_ko_losses) + 0.035 * min(2, a.history.last5_sub_losses)
    if b.history:
        fb = 0.10 * (b.history.last5_w - b.history.last5_l)
        vb = 0.06 * min(3, b.history.last5_ko_losses) + 0.035 * min(2, b.history.last5_sub_losses)
    return (fa - fb) + (vb - va)


def implied_probability_red(
    a: FighterProfile,
    b: FighterProfile,
    red_rank_card: Optional[int],
    blue_rank_card: Optional[int],
) -> float:
    wr_a = win_rate(a.wins, a.losses, a.draws)
    wr_b = win_rate(b.wins, b.losses, b.draws)
    ra = rank_score(red_rank_card, a.ufc_rank)
    rb = rank_score(blue_rank_card, b.ufc_rank)
    edge_wr = wr_a - wr_b
    edge_rank = (rb - ra) / 15.0
    edge_str = 0.0
    if a.sig_str_lpm is not None and b.sig_str_abs_lpm is not None:
        edge_str += 0.04 * (a.sig_str_lpm - b.sig_str_abs_lpm)
    if b.sig_str_lpm is not None and a.sig_str_abs_lpm is not None:
        edge_str -= 0.04 * (b.sig_str_lpm - a.sig_str_abs_lpm)
    edge_hist = _history_logit_edge_red(a, b)
    logit = 3.5 * edge_wr + 2.2 * edge_rank + edge_str + edge_hist
    return 1.0 / (1.0 + math.exp(-logit))


def _norm3(a: float, b: float, c: float) -> tuple[float, float, float]:
    s = a + b + c
    if s <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return a / s, b / s, c / s


def method_weights_if_win(w: FighterProfile, opp: FighterProfile) -> tuple[float, float, float]:
    """Pesos relativos KO, Dec, Sub se `w` vence esta luta (heurística)."""
    wins = max(w.wins, 1)
    ko_share = w.ko_wins / wins
    dec_share = w.dec_wins / wins
    sub_share = w.sub_wins / wins
    ssum = ko_share + dec_share + sub_share
    if ssum < 0.01:
        ko_share, dec_share, sub_share = 0.4, 0.4, 0.2
    else:
        ko_share /= ssum
        dec_share /= ssum
        sub_share /= ssum

    kd = w.kd_avg or 0.0
    ko_share *= 1.0 + 0.35 * min(1.5, kd)
    if w.sig_str_lpm is not None and opp.sig_str_abs_lpm is not None:
        ko_share *= 1.0 + 0.12 * max(-1.5, min(1.5, w.sig_str_lpm - opp.sig_str_abs_lpm))
    if opp.str_def_pct is not None:
        ko_share *= 0.55 + 0.45 * (100.0 - opp.str_def_pct) / 100.0
    if opp.history and opp.history.last5_ko_losses >= 2:
        ko_share *= 1.14
    if opp.history and opp.history.last5_sub_losses >= 2:
        sub_share *= 1.1

    td = (w.td_avg or 0.0) + (w.sub_per_15 or 0.0) * 0.5
    sub_share *= 1.0 + 0.4 * min(2.0, td)
    if opp.td_def_pct is not None:
        sub_share *= 0.5 + 0.5 * (100.0 - opp.td_def_pct) / 100.0
    if w.sig_str_lpm is not None and opp.sig_str_abs_lpm is not None:
        sub_share *= 1.0 + 0.08 * max(-1.0, min(1.0, opp.sig_str_lpm - w.sig_str_abs_lpm))

    dec_share *= 1.0 + 0.15 * (w.avg_fight_minutes or 12.0) / 15.0
    return _norm3(ko_share, dec_share, sub_share)


def combined_method_probs(
    p_red: float,
    red: FighterProfile,
    blue: FighterProfile,
) -> tuple[float, float, float]:
    k_r, d_r, s_r = method_weights_if_win(red, blue)
    k_b, d_b, s_b = method_weights_if_win(blue, red)
    pk = p_red * k_r + (1 - p_red) * k_b
    pd = p_red * d_r + (1 - p_red) * d_b
    ps = p_red * s_r + (1 - p_red) * s_b
    return _norm3(pk, pd, ps)


def joint_scenario_table(
    p_red: float,
    red: FighterProfile,
    blue: FighterProfile,
) -> list[tuple[str, str, float]]:
    """Cenários (vencedor do canto × método) com probabilidade conjunta aproximada."""
    k_r, d_r, s_r = method_weights_if_win(red, blue)
    k_b, d_b, s_b = method_weights_if_win(blue, red)
    items = [
        ("Vermelho", "KO/TKO", p_red * k_r),
        ("Vermelho", "Decisão", p_red * d_r),
        ("Vermelho", "Finalização", p_red * s_r),
        ("Azul", "KO/TKO", (1 - p_red) * k_b),
        ("Azul", "Decisão", (1 - p_red) * d_b),
        ("Azul", "Finalização", (1 - p_red) * s_b),
    ]
    items.sort(key=lambda x: -x[2])
    return items


def round_band_for_winner(w: FighterProfile, ko_w: float, sub_w: float, dec_w: float) -> str:
    """Faixa de round plausível para o vencedor mais provável."""
    wins = max(w.wins, 1)
    fr = w.first_round_finishes / wins
    kd = w.kd_avg or 0.0
    avg_m = w.avg_fight_minutes or 12.0

    top = max(ko_w, sub_w, dec_w)
    if top == dec_w and dec_w >= ko_w and dec_w >= sub_w:
        return "3º-5º (painel — decisão)"
    if top == sub_w:
        if fr > 0.35 or (w.sub_per_15 or 0) > 0.35:
            return "1º-2º (finalização)"
        return "1º-3º (finalização)"
    # KO/TKO
    if fr >= 0.42 and kd >= 0.35:
        return "1º round (nocaute / volume alto no início)"
    if fr >= 0.28 or kd >= 0.45:
        return "1º-2º (nocaute)"
    if avg_m >= 13.0:
        return "3º-5º (nocaute tardio ou acúmulo)"
    return "2º-4º (nocaute)"


def print_fighter_block(label: str, f: FighterProfile) -> None:
    rr = f"#{f.ufc_rank}" if f.ufc_rank else "—"
    print(f"    --- {label} ({f.name}) [{rr}] ---")
    print(f"        Cartel: {f.wins}-{f.losses}-{f.draws} | Vitórias: KO/TKO {f.ko_wins} | Dec {f.dec_wins} | Finaliz. {f.sub_wins} | 1ºR {f.first_round_finishes}")
    if f.history:
        h = f.history
        print(
            f"        Histórico (no site): {h.total_in_page} lutas listadas | "
            f"últimas 5: {h.sequence} ({h.last5_w}V-{h.last5_l}D)"
        )
        print(f"        Detalhe (recente primeiro): {h.line_detail}")
    slpm = f"{f.sig_str_lpm:.2f}" if f.sig_str_lpm is not None else "—"
    sapm = f"{f.sig_str_abs_lpm:.2f}" if f.sig_str_abs_lpm is not None else "—"
    td = f"{f.td_avg:.2f}" if f.td_avg is not None else "—"
    su = f"{f.sub_per_15:.2f}" if f.sub_per_15 is not None else "—"
    sd = f"{f.str_def_pct:.0f}%" if f.str_def_pct is not None else "—"
    tdd = f"{f.td_def_pct:.0f}%" if f.td_def_pct is not None else "—"
    kd = f"{f.kd_avg:.2f}" if f.kd_avg is not None else "—"
    tm = f"{f.avg_fight_minutes:.1f} min" if f.avg_fight_minutes is not None else "—"
    print(f"        Striking: {slpm} sig/min landados | {sapm} abs | def. golpes {sd}")
    print(f"        Grappling: {td} quedas/15min | {su} finaliz./15min | def. quedas {tdd}")
    print(f"        Knockdowns (média/15min): {kd} | Tempo médio de luta: {tm}")


def load_fighter(
    session: requests.Session,
    slug: str,
    cache: dict[str, FighterProfile],
    base: str,
    *,
    html_cache_dir: Optional[Path],
    html_cache_max_age: Optional[float],
    force_refresh: bool,
) -> FighterProfile:
    if slug in cache:
        return cache[slug]
    root = base.rstrip("/")
    if re.fullmatch(r"node-\d+", slug):
        url = f"{root}/node/{slug.split('-', 1)[1]}"
    else:
        url = f"{root}/athlete/{slug}"
    html = fetch_html(
        url,
        session,
        cache_dir=html_cache_dir,
        cache_max_age_seconds=html_cache_max_age,
        force_refresh=force_refresh,
    )
    prof = build_fighter_profile(html, slug)
    cache[slug] = prof
    return prof


def _fighter_brief(f: FighterProfile) -> dict[str, Any]:
    return {
        "name": f.name,
        "slug": f.slug,
        "record": f"{f.wins}-{f.losses}-{f.draws}",
        "rank": f.ufc_rank,
    }


def analyze_event_json(
    event_url: str,
    *,
    base: str = "https://www.ufc.com.br",
    cache_dir: Optional[Path] = None,
    cache_hours: float = 24.0,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    Executa a análise do card e devolve um dict pronto para JSON (ex.: API web).
    """
    try:
        from mma_predict.predictor import predict_fight_advanced as _mma_predict_fight
    except ImportError:
        _mma_predict_fight = None  # type: ignore[assignment, misc]

    base = base.rstrip("/")
    ttl: Optional[float] = None if cache_hours <= 0 else cache_hours * 3600.0
    session = requests.Session()
    out: dict[str, Any] = {
        "ok": False,
        "event_url": event_url,
        "event_title": None,
        "fights": [],
        "errors": [],
    }
    try:
        event_html = fetch_html(
            event_url,
            session,
            cache_dir=cache_dir,
            cache_max_age_seconds=ttl,
            force_refresh=refresh,
        )
    except requests.RequestException as e:
        out["errors"].append(str(e))
        return out

    try:
        meta = extract_event_page_meta(event_html)
        out["hero_image_url"] = meta.get("hero_image_url")
        out["event_starts_at"] = meta.get("event_starts_at")
        if meta.get("og_title"):
            out["event_title"] = meta["og_title"]

        fights = parse_fight_card(event_html, session=session, base=base)
        # Antes filtrávamos só lutas não finalizadas (menos pedidos em noite ao vivo), mas o
        # data-status do site falha por vezes e o card ficava vazio ou com poucas lutas — analisamos o card inteiro.
        if not fights:
            out["errors"].append("Nenhuma luta encontrada no HTML do evento (ou todas já finalizadas).")
            return out

        mf0 = fights[0]
        out["main_fight_preview"] = {
            "red_name": mf0.red_name,
            "blue_name": mf0.blue_name,
            "red_photo_url": mf0.red_photo_url,
            "blue_photo_url": mf0.blue_photo_url,
        }

        cache: dict[str, FighterProfile] = {}
        prev_volatilities: list[float] = []
        peer_final_probs: list[float] = []
        prior_phase7_stake = 0.0
        cumulative_phase7_stake = 0.0
        for i, fr in enumerate(fights, 1):
            row: dict[str, Any] = {
                "index": i,
                "division": fr.division,
                "red": None,
                "blue": None,
                "prob_red_pct": None,
                "prob_blue_pct": None,
                "favorite_corner": None,
                "methods_pct": None,
                "top_scenarios": [],
                "if_favorite_wins_pct": None,
                "round_hint": None,
                "predicted_winner": None,
                "predicted_method": None,
                "error": None,
            }
            try:
                red = load_fighter(
                    session,
                    fr.red_slug,
                    cache,
                    base,
                    html_cache_dir=cache_dir,
                    html_cache_max_age=ttl,
                    force_refresh=refresh,
                )
                blue = load_fighter(
                    session,
                    fr.blue_slug,
                    cache,
                    base,
                    html_cache_dir=cache_dir,
                    html_cache_max_age=ttl,
                    force_refresh=refresh,
                )
                p_red = implied_probability_red(red, blue, fr.red_rank_card, fr.blue_rank_card)
                p_blue = 1.0 - p_red
                fav_is_red = p_red >= p_blue
                fav = red if fav_is_red else blue
                fav_name = fr.red_name if fav_is_red else fr.blue_name
                pk, pd, ps = combined_method_probs(p_red, red, blue)
                kw, dw, sw = (
                    method_weights_if_win(fav, blue if fav is red else red)
                    if fav is red
                    else method_weights_if_win(fav, red)
                )
                joint = joint_scenario_table(p_red, red, blue)
                best_corner, best_meth, _ = joint[0]
                winner_name = fr.red_name if best_corner == "Vermelho" else fr.blue_name
                band = round_band_for_winner(fav, kw, sw, dw)

                row["red"] = {
                    **_fighter_brief(red),
                    "rank_card": fr.red_rank_card,
                    "photo_url": fr.red_photo_url,
                }
                row["blue"] = {
                    **_fighter_brief(blue),
                    "rank_card": fr.blue_rank_card,
                    "photo_url": fr.blue_photo_url,
                }
                row["prob_red_pct"] = round(100.0 * p_red, 2)
                row["prob_blue_pct"] = round(100.0 * p_blue, 2)
                row["favorite_corner"] = "red" if fav_is_red else "blue"
                row["methods_pct"] = {
                    "ko_tko": round(100.0 * pk, 2),
                    "decisao": round(100.0 * pd, 2),
                    "finalizacao": round(100.0 * ps, 2),
                }
                row["top_scenarios"] = [
                    {
                        "label": (
                            fr.red_name if c == "Vermelho" else fr.blue_name
                        )
                        + " — "
                        + meth,
                        "prob_pct": round(100.0 * pj, 2),
                    }
                    for c, meth, pj in joint[:3]
                ]
                row["if_favorite_wins_pct"] = {
                    "ko_tko": round(100.0 * kw, 2),
                    "decisao": round(100.0 * dw, 2),
                    "finalizacao": round(100.0 * sw, 2),
                }
                row["round_hint"] = band
                row["predicted_winner"] = winner_name
                row["predicted_method"] = best_meth
                row["favorite_name"] = fav_name

                if _mma_predict_fight is not None:
                    try:
                        card_avg_vol = (
                            sum(prev_volatilities) / len(prev_volatilities)
                            if prev_volatilities
                            else None
                        )
                        row["advanced_prediction"] = _mma_predict_fight(
                            red,
                            blue,
                            red_rank_card=fr.red_rank_card,
                            blue_rank_card=fr.blue_rank_card,
                            red_display_name=fr.red_name,
                            blue_display_name=fr.blue_name,
                            event_title=out.get("event_title"),
                            division=fr.division,
                            card_avg_volatility=card_avg_vol,
                            phase7_event_url=event_url,
                            phase7_fight_index=i,
                            phase7_event_total_fights=len(fights),
                            phase7_peer_final_probs=list(peer_final_probs),
                            phase7_prior_event_stake_fraction=prior_phase7_stake,
                        )
                        try:
                            from mma_predict.learning import (
                                log_bayesian_prior_snapshot,
                                log_prediction,
                                make_fight_id,
                            )

                            ap = row["advanced_prediction"]
                            if isinstance(ap, dict) and "error" not in ap and ap.get("ok") is not False:
                                wm = ap.get("weighted_model") or {}
                                ft = wm.get("feature_terms_vector") or [0.0, 0.0, 0.0, 0.0]
                                if len(ft) == 4:
                                    vdet = ap.get("value_bet_detail")
                                    fid = make_fight_id(event_url, i)
                                    pr = float(ap.get("model_prob", 0.5))
                                    p4 = ap.get("phase4_model") or {}
                                    pm3 = ap.get("phase3_model") or {}
                                    snap = None
                                    if p4.get("ensemble_weights") and p4.get("roi_context") is not None:
                                        snap = {
                                            "context_bucket": str(p4.get("roi_context")),
                                            "regime": str(p4.get("regime")),
                                            "p_heuristic": float(pr),
                                            "p_bayesian": float(pm3.get("bayesian_prob", pr)),
                                            "p_elo": float(pm3.get("elo_prob", 0.5)),
                                            "p_ml": float(pm3["ml_prob_red"])
                                            if pm3.get("ml_prob_red") is not None
                                            else None,
                                            "has_ml": pm3.get("ml_prob_red") is not None,
                                            "ensemble_weights": dict(p4.get("ensemble_weights") or {}),
                                        }
                                    p5 = ap.get("phase5_policy")
                                    snap5 = json.dumps(p5, ensure_ascii=False) if isinstance(p5, dict) else None
                                    log_prediction(
                                        fight_id=fid,
                                        event_url=event_url,
                                        fight_index=i,
                                        model_prob=pr,
                                        monte_carlo_prob=float(ap.get("monte_carlo_prob", pr)),
                                        confidence=float(max(pr, 1.0 - pr)),
                                        volatility=float(ap.get("volatility", 0.0)),
                                        term_strike=float(ft[0]),
                                        term_grap=float(ft[1]),
                                        term_tdd=float(ft[2]),
                                        term_card=float(ft[3]),
                                        value_flag=bool(ap.get("value_bet")),
                                        value_side=str(vdet["side"])
                                        if isinstance(vdet, dict) and vdet.get("side") in ("red", "blue")
                                        else None,
                                        value_edge=float(vdet["edge_prob"])
                                        if isinstance(vdet, dict) and vdet.get("edge_prob") is not None
                                        else None,
                                        red_slug=fr.red_slug,
                                        blue_slug=fr.blue_slug,
                                        phase4_snapshot=json.dumps(snap)
                                        if isinstance(snap, dict)
                                        else None,
                                        phase5_snapshot=snap5,
                                    )
                                    try:
                                        prev_volatilities.append(float(ap.get("volatility", 0.0)))
                                    except (TypeError, ValueError):
                                        pass
                                    pm = ap.get("phase3_model") or {}
                                    bw = pm.get("bayesian_weights") or {}
                                    log_bayesian_prior_snapshot(
                                        fight_id=fid,
                                        prior_red=pr,
                                        posterior_red=float(pm.get("bayesian_prob", pr)),
                                        ml_red=float(pm["ml_prob_red"])
                                        if pm.get("ml_prob_red") is not None
                                        else None,
                                        w_prior=float(bw.get("w_prior", 0.0)),
                                        w_ml=float(bw.get("w_ml", 0.0)),
                                    )
                                p7b = ap.get("phase7_bankroll")
                                if isinstance(p7b, dict):
                                    try:
                                        rs = float(p7b.get("recommended_stake") or 0.0)
                                        prior_phase7_stake += rs
                                        cumulative_phase7_stake += rs
                                    except (TypeError, ValueError):
                                        pass
                                pmx = ap.get("phase3_model") or {}
                                if isinstance(pmx, dict) and pmx.get("final_prob") is not None:
                                    try:
                                        peer_final_probs.append(float(pmx["final_prob"]))
                                    except (TypeError, ValueError):
                                        pass
                        except Exception:
                            pass
                    except Exception as ex:
                        row["advanced_prediction"] = {"ok": False, "error": str(ex)}
            except Exception as e:
                row["error"] = str(e)
            out["fights"].append(row)

        out["ok"] = True
        try:
            from mma_predict.learning import get_learning_api_payload

            out["learning"] = get_learning_api_payload()
        except Exception:
            pass
        try:
            from mma_predict.bankroll import maybe_log_event_completion

            maybe_log_event_completion(
                event_url=event_url,
                cumulative_stake_fraction=float(cumulative_phase7_stake),
            )
        except Exception:
            pass
        return out
    except Exception as e:
        out["errors"].append(str(e))
        out["ok"] = False
        return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Análise do card UFC (ufc.com.br).")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL da página do evento.")
    parser.add_argument("--base", default="https://www.ufc.com.br", help="Base do site.")
    parser.add_argument("--no-disk-cache", action="store_true", help="Sem cache em disco.")
    parser.add_argument("--cache-dir", default=".ufc_html_cache", help="Pasta de cache HTML.")
    parser.add_argument(
        "--cache-hours",
        type=float,
        default=24.0,
        help="TTL do cache em horas (0 = não expira).",
    )
    parser.add_argument("--refresh", action="store_true", help="Força download.")
    parser.add_argument(
        "--output",
        "-o",
        default="ufc_previsoes.txt",
        help="Arquivo TXT com uma linha por luta: Ganhador - Modo de vitória (padrão: ufc_previsoes.txt).",
    )
    parser.add_argument(
        "--externo",
        action="store_true",
        help="Após a análise, busca menções (Google News, Reddit r/MMA, RSS opcional) e grava arquivo extra.",
    )
    parser.add_argument(
        "--externo-sem-feeds",
        action="store_true",
        help="Com --externo: não usa RSS dos sites (só Google News, X/Twitter via GN e Reddit r/MMA + r/UFC).",
    )
    parser.add_argument(
        "--externo-query",
        default="",
        help="Com --externo: termos de busca manuais. Se vazio, usa o título do evento (og:title) no HTML.",
    )
    parser.add_argument(
        "--externo-output",
        default="ufc_enriquecimento_externo.txt",
        help="Arquivo das menções externas (padrão: ufc_enriquecimento_externo.txt).",
    )
    parser.add_argument(
        "--relatorio-completo",
        default="",
        metavar="ARQUIVO",
        help="Com --externo: grava também um único arquivo (previsões + menções externas). Ex.: ufc_relatorio_completo.txt",
    )
    parser.add_argument(
        "--externo-limit",
        type=int,
        default=25,
        metavar="N",
        help="Com --externo: máx. itens Google News e Reddit (Reddit API: no máx. 25). Padrão: 25.",
    )
    parser.add_argument(
        "--externo-rss-max",
        type=int,
        default=40,
        metavar="N",
        help="Com --externo: máx. posts por feed MMA Fighting/BJPenn após filtro. Padrão: 40.",
    )
    parser.add_argument(
        "--externo-rss-scan",
        type=int,
        default=120,
        metavar="N",
        help="Com --externo: itens lidos de cada feed RSS antes do filtro. Padrão: 120.",
    )
    parser.add_argument(
        "--externo-lutadores",
        choices=("off", "principal", "todos"),
        default="off",
        help="Com --externo: off=só evento | principal=1ª luta (geralmente main) lesão/notícias | todos=todos (demorado).",
    )
    parser.add_argument(
        "--externo-fighter-limit",
        type=int,
        default=10,
        metavar="N",
        help="Com --externo-lutadores: máx. itens por sub-busca (lesão, bio…) por lutador. Padrão: 10.",
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")

    cache_dir: Optional[Path] = None
    if not args.no_disk_cache and args.cache_dir and str(args.cache_dir).strip():
        cache_dir = Path(args.cache_dir).expanduser().resolve()
    ttl: Optional[float] = None if args.cache_hours <= 0 else args.cache_hours * 3600.0

    session = requests.Session()
    try:
        event_html = fetch_html(
            args.url,
            session,
            cache_dir=cache_dir,
            cache_max_age_seconds=ttl,
            force_refresh=args.refresh,
        )
    except requests.RequestException as e:
        print(f"Erro ao baixar o evento: {e}", file=sys.stderr)
        return 1

    fights = parse_fight_card(event_html, session=session, base=args.base.rstrip("/"))
    if not fights:
        print("Nenhuma luta encontrada no HTML.", file=sys.stderr)
        return 1

    cache: dict[str, FighterProfile] = {}
    print(f"Evento: {args.url}\n")
    if cache_dir:
        print(f"Cache HTML: {cache_dir} (TTL: {'sem expiração' if ttl is None else f'{args.cache_hours} h'})\n")
    print(
        "Modelo heurístico: cartel, ranking, stats do perfil, histórico recente no site\n"
        "(últimas lutas listadas no HTML do atleta).\n"
        "Não usa odds de casas. Método/round são cenários plausíveis, não certezas.\n"
    )
    print("=" * 72)

    txt_lines: list[str] = []
    txt_lines.append(f"# UFC — previsões ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    txt_lines.append(f"# Evento: {args.url}")
    txt_lines.append(
        "# Formato: Ganhador previsto - Modo de vitória (cenário conjunto mais provável: canto × método)"
    )
    txt_lines.append("")
    saved_predictions = 0

    for i, fr in enumerate(fights, 1):
        try:
            red = load_fighter(
                session,
                fr.red_slug,
                cache,
                base,
                html_cache_dir=cache_dir,
                html_cache_max_age=ttl,
                force_refresh=args.refresh,
            )
            blue = load_fighter(
                session,
                fr.blue_slug,
                cache,
                base,
                html_cache_dir=cache_dir,
                html_cache_max_age=ttl,
                force_refresh=args.refresh,
            )
        except Exception as e:
            print(f"\n[{i}] {fr.division} — {fr.red_name} vs {fr.blue_name}")
            print(f"    Erro: {e}")
            continue

        p_red = implied_probability_red(red, blue, fr.red_rank_card, fr.blue_rank_card)
        p_blue = 1.0 - p_red
        fav = red if p_red >= p_blue else blue
        fav_name = fr.red_name if p_red >= p_blue else fr.blue_name
        pk, pd, ps = combined_method_probs(p_red, red, blue)
        kw, dw, sw = (
            method_weights_if_win(fav, blue if fav is red else red)
            if fav is red
            else method_weights_if_win(fav, red)
        )

        rr = f"#{fr.red_rank_card}" if fr.red_rank_card else "—"
        br = f"#{fr.blue_rank_card}" if fr.blue_rank_card else "—"

        print(f"\n[{i}] {fr.division}")
        print(f"    Card: {fr.red_name} [{rr}] vs {fr.blue_name} [{br}]")
        print_fighter_block("Canto vermelho", red)
        print_fighter_block("Canto azul", blue)

        print(f"    --- Probabilidade (modelo) ---")
        print(f"        {fr.red_name}: {100*p_red:.1f}% | {fr.blue_name}: {100*p_blue:.1f}%")
        print(f"        Favorito: {fav_name}")

        print(f"    --- Método (toda a luta, misturando quem vence) ---")
        print(f"        KO/TKO ~{100*pk:.1f}% | Decisão ~{100*pd:.1f}% | Finalização ~{100*ps:.1f}%")

        print(f"    --- Cenários conjuntos (vencedor × método) — top 3 ---")
        for corner, meth, pj in joint_scenario_table(p_red, red, blue)[:3]:
            print(f"        {corner} + {meth}: ~{100*pj:.1f}%")

        print(f"    --- Se o favorito ({fav_name}) vencer ---")
        print(f"        Peso relativo: KO/TKO ~{100*kw:.1f}% | Dec ~{100*dw:.1f}% | Finaliz. ~{100*sw:.1f}%")
        band = round_band_for_winner(fav, kw, sw, dw)
        print(f"        Faixa de round (cenário): {band}")

        joint = joint_scenario_table(p_red, red, blue)
        best_corner, best_meth, _ = joint[0]
        winner_txt = fr.red_name if best_corner == "Vermelho" else fr.blue_name
        txt_lines.append(f"{winner_txt} - {best_meth}")
        saved_predictions += 1

        print("-" * 72)

    previsoes_body = "\n".join(txt_lines) + "\n"
    out_path = Path(args.output).expanduser().resolve()
    out_path.write_text(previsoes_body, encoding="utf-8")
    print(f"\nSalvo: {out_path} ({saved_predictions} lutas)")

    if args.externo:
        if external_context_report is None or extract_event_query_from_ufc_page is None:
            print(
                "\n[externo] Módulo ufc_external_context não encontrado; instale os arquivos no mesmo diretório.",
                file=sys.stderr,
            )
            return 0
        q = (args.externo_query or "").strip()
        if not q:
            q = extract_event_query_from_ufc_page(event_html) or ""
        if not q and fights:
            q = f"{fights[0].red_name} {fights[0].blue_name} UFC"
        if not q:
            print("\n[externo] Não foi possível definir a consulta; use --externo-query.", file=sys.stderr)
            return 0

        fighter_names_list: Optional[list[str]] = None
        if args.externo_lutadores == "principal" and fights:
            fighter_names_list = [fights[0].red_name, fights[0].blue_name]
        elif args.externo_lutadores == "todos":
            seen: list[str] = []
            for fr in fights:
                for n in (fr.red_name, fr.blue_name):
                    if n and n not in seen:
                        seen.append(n)
            fighter_names_list = seen
        if fighter_names_list:
            print(
                f"[externo] Blocos por lutador: {len(fighter_names_list)} nome(s)",
                file=sys.stderr,
            )

        print("\n" + "=" * 72)
        print("ENRIQUECIMENTO EXTERNO (notícias / Reddit — não altera o modelo)")
        print("=" * 72)
        print(f"Consulta: {q}\n", flush=True)
        try:
            el = max(1, min(args.externo_limit, 100))
            er = min(el, 25)
            ext_text = external_context_report(
                q,
                session,
                include_feeds=not args.externo_sem_feeds,
                google_limit=el,
                reddit_limit=er,
                rss_max_each=max(1, args.externo_rss_max),
                rss_scan=max(20, args.externo_rss_scan),
                fighter_names=fighter_names_list,
                fighter_sub_limit=max(3, min(args.externo_fighter_limit, 20)),
            )
        except Exception as e:
            print(f"[externo] Erro: {e}", file=sys.stderr)
            return 0
        print(ext_text)
        ext_out = Path(args.externo_output).expanduser().resolve()
        ext_out.write_text(ext_text, encoding="utf-8")
        print(f"\nSalvo (externo): {ext_out}", file=sys.stderr)

        rel = (args.relatorio_completo or "").strip()
        if rel:
            rel_path = Path(rel).expanduser().resolve()
            merged = (
                previsoes_body.rstrip()
                + "\n\n"
                + "=" * 72
                + "\n"
                + "ENRIQUECIMENTO EXTERNO\n"
                + "=" * 72
                + "\n\n"
                + ext_text
            )
            rel_path.write_text(merged, encoding="utf-8")
            print(f"Relatório completo: {rel_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
