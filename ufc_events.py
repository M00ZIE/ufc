"""
Lista de eventos UFC recentes (ufc.com.br/events) com cache em disco.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

EVENTS_LIST_URL = "https://www.ufc.com.br/events"

_MONTH_SLUG = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_date_from_event_url(url: str) -> Optional[date]:
    """
    Extrai data a partir do slug do path (ex.: ...-march-28-2026).
    Eventos só com número (ufc-327) devolvem None.
    """
    path = (urlparse(url).path or "").lower()
    m = re.search(
        r"-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(\d{4})(?:/|$)",
        path,
        re.I,
    )
    if not m:
        return None
    month = _MONTH_SLUG.get(m.group(1).lower())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def list_future_events_ordered(events: list[dict[str, Any]], *, today: Optional[date] = None) -> list[dict[str, Any]]:
    """
    Lista só eventos futuros: data no URL >= hoje;
    depois eventos sem data no slug (ex.: ufc-327), na ordem do site.
    URLs com data anterior a hoje são ignoradas.
    """
    today = today or date.today()
    dated: list[tuple[date, int, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for i, e in enumerate(events):
        u = (e.get("url") or "").strip()
        d = parse_date_from_event_url(u)
        if d is None:
            undated.append(e)
        elif d >= today:
            dated.append((d, i, e))
    dated.sort(key=lambda x: (x[0].toordinal(), x[1]))
    return [x[2] for x in dated] + undated


def select_next_future_event(events: list[dict[str, Any]], *, today: Optional[date] = None) -> Optional[dict[str, Any]]:
    """
    Escolhe o próximo evento priorizando datas explícitas >= hoje.
    Eventos sem data no slug ficam como fallback.
    """
    if not events:
        return None
    today = today or date.today()
    candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for i, e in enumerate(events):
        u = (e.get("url") or "").strip()
        d = parse_date_from_event_url(u)
        if d is None:
            # Sem data no slug: entra depois de eventos datados.
            candidates.append(((1, i, 0), e))
        elif d >= today:
            candidates.append(((0, d.toordinal(), i), e))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _normalize_event_url(href: str, base: str) -> Optional[str]:
    if not href or "/event/" not in href:
        return None
    u = urljoin(base + "/", href)
    p = urlparse(u)
    if "ufc.com.br" not in (p.netloc or "").lower():
        return None
    path = (p.path or "").rstrip("/")
    if "/event/" not in path:
        return None
    return f"{p.scheme}://{p.netloc}{path}"


def parse_events_list_html(html: str, base: str = "https://www.ufc.com.br") -> list[dict[str, Any]]:
    """
    Extrai cards de evento: título em <h3> + link /event/... (ufc.com.br/events).
    Fallback: links soltos com texto genérico.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for card in soup.select("article, [class*='card-event'], [class*='event-card']"):
        a = card.select_one('a[href*="/event/"]')
        if not a:
            continue
        raw = a.get("href") or ""
        url = _normalize_event_url(raw, base)
        if not url or url in seen:
            continue
        h3 = card.select_one("h3")
        title = ""
        if h3:
            title = re.sub(r"\s+", " ", h3.get_text(" ", strip=True))
        if len(title) < 3:
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(title) < 3:
            title = url.rsplit("/", 1)[-1].replace("-", " ").title()
        seen.add(url)
        out.append({"title": title[:160], "url": url})

    if out:
        return out

    for a in soup.select("a[href]"):
        raw = a.get("href") or ""
        url = _normalize_event_url(raw, base)
        if not url or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(title) < 3:
            title = url.rsplit("/", 1)[-1].replace("-", " ").title()
        out.append({"title": title[:160], "url": url})
    return out


def _drop_past_events_by_hero_time(
    events: list[dict[str, Any]],
    session: requests.Session,
    *,
    cache_dir: Optional[Path],
    ttl: Optional[float],
) -> list[dict[str, Any]]:
    """
    Remove eventos já encerrados (hero no passado),     exceto:
    mesmo dia local que o horário do hero.
    Sem timestamp no HTML: mantém; com falha de fetch: mantém.
    """
    from ufc_event_analysis import extract_event_hero_timestamp_unix, fetch_html

    now = time.time()
    today = date.today()
    kept: list[dict[str, Any]] = []
    for e in events:
        url = (e.get("url") or "").strip()
        if not url:
            continue
        try:
            page_html = fetch_html(
                url,
                session,
                cache_dir=cache_dir,
                cache_max_age_seconds=ttl,
                force_refresh=False,
            )
        except Exception:
            kept.append(e)
            continue
        ts = extract_event_hero_timestamp_unix(page_html)
        if ts is not None:
            if ts >= now:
                kept.append(e)
                continue
            started_ago = now - ts
            try:
                event_day = date.fromtimestamp(ts)
            except (ValueError, OSError):
                event_day = None
            # Mesmo dia local: mantém enquanto o evento ainda pode estar ocorrendo.
            if event_day is not None and event_day >= today:
                kept.append(e)
                continue
            continue
        d = parse_date_from_event_url(url)
        if d is not None and d < today:
            continue
        kept.append(e)
    return kept


def fetch_events_list(
    session: requests.Session,
    *,
    cache_dir: Optional[Path] = None,
    cache_hours: float = 2.0,
    refresh: bool = False,
    base: str = "https://www.ufc.com.br",
) -> dict[str, Any]:
    """
    Retorna { ok, events: [{ title, url }], source_url }.
    Cache curto (padrão 2h) para não martelar o site.
    """
    from ufc_event_analysis import fetch_html

    ttl = None if cache_hours <= 0 else cache_hours * 3600.0
    try:
        html = fetch_html(
            EVENTS_LIST_URL,
            session,
            cache_dir=cache_dir,
            cache_max_age_seconds=ttl,
            force_refresh=refresh,
        )
    except requests.RequestException as e:
        return {
            "ok": False,
            "events": [],
            "future_events": [],
            "errors": [str(e)],
            "source_url": EVENTS_LIST_URL,
            "next_future": None,
        }
    events = parse_events_list_html(html, base=base)
    if not events:
        return {
            "ok": False,
            "events": [],
            "future_events": [],
            "errors": ["Nenhum evento encontrado no HTML (layout pode ter mudado)."],
            "source_url": EVENTS_LIST_URL,
            "next_future": None,
        }
    future_ordered = list_future_events_ordered(events)
    # Em serverless (Vercel), evitar N requests extras por evento
    # no bootstrap da home para não congelar a UI por muito tempo.
    # A filtragem por data no slug já remove eventos antigos na maior parte.
    on_vercel = bool(os.environ.get("VERCEL"))
    if not on_vercel:
        future_ordered = _drop_past_events_by_hero_time(
            future_ordered,
            session,
            cache_dir=cache_dir,
            ttl=ttl,
        )
    nxt = select_next_future_event(future_ordered) if future_ordered else None
    return {
        "ok": True,
        "events": events,
        "future_events": future_ordered,
        "source_url": EVENTS_LIST_URL,
        "errors": [],
        "next_future": nxt,
    }
