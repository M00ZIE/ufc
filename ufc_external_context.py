#!/usr/bin/env python3
"""
Agrega menções públicas sobre um evento/luta UFC para enriquecer análises.

Fontes (sem login):
  - Google News (RSS), incl. variação com filtro site:twitter.com / site:x.com (ecos no X)
  - Reddit r/MMA e r/UFC (.json search)
  - RSS: MMA Fighting, BJPenn, MMA Junkie, Super Lutas (BR), LowKick, MiddleEasy, Sherdog, Cageside, MMA Mania, Bloody Elbow (filtrados)
  - Opcional por lutador (--lutador): buscas para lesão/saúde (EN+PT), trajetória, Reddit, ecos X/Twitter via Google News

Respeite robots.txt e termos dos sites; há pausa entre requisições.
Não é parecer médico nem confirmação de lesão; valide na fonte.
Não há API oficial do X/Twitter aqui: menções vêm do índice do Google News (artigos/páginas ligadas ao X).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import requests

HEADERS = {
    "User-Agent": "UFC-ResearchBot/1.0 (+local script; educational)",
    "Accept": "application/rss+xml, application/xml, application/json, text/html;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
REDDIT_SEARCH_JSON_TMPL = "https://www.reddit.com/r/{sub}/search.json"
MMA_FIGHTING_RSS = "https://www.mmafighting.com/rss/index.xml"
BJPENN_RSS = "https://www.bjpenn.com/feed/"
# Feeds extras (podem falhar se o site mudar o URL)
EXTRA_RSS_FEEDS: list[tuple[str, str]] = [
    ("https://mmajunkie.usatoday.com/feed", "MMA Junkie"),
    ("https://www.superlutas.com.br/feed/", "Super Lutas"),
    ("https://www.lowkickmma.com/feed/", "LowKick MMA"),
    ("https://www.middleeasy.com/category/mma/feed/", "MiddleEasy MMA"),
    ("https://www.sherdog.com/rss/news.xml", "Sherdog"),
    ("https://www.cagesidepress.com/feed/", "Cageside Press"),
    ("https://www.mmamania.com/rss/index.xml", "MMA Mania"),
    ("https://www.bloodyelbow.com/feed/", "Bloody Elbow"),
    ("https://www.ufc.com/rss/news", "UFC.com news"),
]


@dataclass
class Mention:
    source: str
    title: str
    link: str
    extra: str = ""


def _get(url: str, session: requests.Session, timeout: float = 30.0) -> requests.Response:
    return session.get(url, headers=HEADERS, timeout=timeout)


def fetch_google_news(
    query: str,
    session: requests.Session,
    limit: int = 25,
    *,
    source_label: str = "Google News",
    hl: str = "pt-BR",
    gl: str = "BR",
    ceid: str = "BR:pt-419",
) -> list[Mention]:
    """RSS do Google News (resultados de busca)."""
    params = {
        "q": query,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    }
    url = f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"
    r = _get(url, session)
    r.raise_for_status()
    mentions: list[Mention] = []
    root = ET.fromstring(r.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    # Google pode devolver RSS 2.0 ou Atom
    channel = root.find("channel")
    if channel is not None:
        items = channel.findall("item")
        for item in items[:limit]:
            t = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            mentions.append(Mention(source_label, t.strip(), link.strip(), pub))
        return mentions

    entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for ent in entries[:limit]:
        t = ent.findtext("{http://www.w3.org/2005/Atom}title") or ""
        link_el = ent.find("{http://www.w3.org/2005/Atom}link")
        href = ""
        if link_el is not None:
            href = link_el.get("href") or ""
        pub = ent.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        mentions.append(Mention(source_label, t.strip(), href, pub))
    return mentions


def fetch_google_news_twitter_x(
    query: str,
    session: requests.Session,
    limit: int = 18,
) -> list[Mention]:
    """
    Google News com filtro de site (twitter.com / x.com).
    Não chama a API do X; só o que o Google indexa como vindo desses domínios.
    """
    q = f'{query.strip()} (site:twitter.com OR site:x.com)'
    return fetch_google_news(q, session, limit=limit, source_label="Google News · X/Twitter")


def fetch_reddit_sub(
    subreddit: str,
    query: str,
    session: requests.Session,
    limit: int = 25,
) -> list[Mention]:
    """Busca em um subreddit via .json (API legada, somente leitura)."""
    params = {
        "q": query,
        "restrict_sr": "1",
        "sort": "relevance",
        "t": "all",
        "limit": str(min(limit, 25)),
    }
    url = f"{REDDIT_SEARCH_JSON_TMPL.format(sub=subreddit)}?{urllib.parse.urlencode(params)}"
    r = _get(url, session)
    label = f"Reddit r/{subreddit}"
    if r.status_code == 403:
        return [
            Mention(
                label,
                "[Bloqueado 403 — tente outro horário ou outra rede]",
                "",
                "",
            )
        ]
    r.raise_for_status()
    data = r.json()
    mentions: list[Mention] = []
    children = data.get("data", {}).get("children", [])
    for ch in children:
        p = ch.get("data", {})
        title = p.get("title") or ""
        permalink = p.get("permalink") or ""
        url_full = "https://www.reddit.com" + permalink if permalink else ""
        score = p.get("score")
        ncom = p.get("num_comments")
        extra = f"↑{score}  comentários:{ncom}" if score is not None else ""
        if title:
            mentions.append(Mention(label, title.strip(), url_full, extra))
    return mentions


def fetch_reddit_mma(
    query: str,
    session: requests.Session,
    limit: int = 25,
) -> list[Mention]:
    """Busca em r/MMA via .json (API legada, somente leitura)."""
    return fetch_reddit_sub("MMA", query, session, limit=limit)


def _parse_rss_items_generic(xml_bytes: bytes, limit: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item")[:limit]:
            t = item.findtext("title") or ""
            link = item.findtext("link") or ""
            desc = item.findtext("description") or ""
            out.append((t.strip(), link.strip(), desc[:200]))
        return out
    # Atom (ex.: alguns feeds UFC / Sherdog)
    atom = "{http://www.w3.org/2005/Atom}"
    for ent in root.findall(f".//{atom}entry")[:limit]:
        t = ent.findtext(f"{atom}title") or ""
        href = ""
        link_el = ent.find(f"{atom}link")
        if link_el is not None:
            href = link_el.get("href") or link_el.text or ""
        summ = ent.findtext(f"{atom}summary") or ent.findtext(f"{atom}content") or ""
        plain = re.sub(r"<[^>]+>", " ", summ)
        out.append((t.strip(), href.strip(), plain[:200]))
    return out


def fetch_rss_filtered(
    feed_url: str,
    session: requests.Session,
    keywords: list[str],
    source_label: str,
    scan_limit: int = 120,
    max_return: int = 40,
) -> list[Mention]:
    """Baixa RSS e mantém só itens que batem palavras-chave (case insensitive)."""
    r = _get(feed_url, session)
    r.raise_for_status()
    items = _parse_rss_items_generic(r.content, scan_limit)
    kw = [k.lower() for k in keywords if k.strip()]
    if not kw:
        return [
            Mention(source_label, t, link, "") for t, link, _ in items[:max_return]
        ]
    mentions: list[Mention] = []
    for t, link, _ in items:
        low = t.lower()
        if any(k in low for k in kw):
            mentions.append(Mention(source_label, t, link, ""))
    return mentions[:max_return]


def extract_event_query_from_ufc_page(html: str) -> Optional[str]:
    """Tenta tirar um título útil do HTML do evento UFC."""
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def fetch_event_page_query(event_url: str, session: requests.Session) -> Optional[str]:
    try:
        r = _get(event_url, session)
        r.raise_for_status()
        return extract_event_query_from_ufc_page(r.text)
    except requests.RequestException:
        return None


def run_fighter_intel_block(
    name: str,
    session: requests.Session,
    *,
    sub_limit: int = 10,
) -> tuple[str, dict[str, Any]]:
    """
    Busca agregada por nome: lesão/saúde (EN+PT), notícias gerais, Reddit.
    Resultados duplicados entre sub-buscas são omitidos pelo título.
    """
    lines: list[str] = []
    seen: set[str] = set()
    counts: dict[str, Any] = {
        "nome": name,
        "injury_en": 0,
        "injury_pt": 0,
        "bio": 0,
        "twitter_x_echo": 0,
        "reddit_mma": 0,
        "reddit_ufc": 0,
    }

    def add_items(items: list[Mention]) -> None:
        for m in items:
            key = (m.title or "").strip().lower()[:400]
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")

    lines.append(f"### {name}")
    lines.append("")
    lines.append(
        "#### Lesão / saúde / desistência (busca — não é laudo médico; confira a fonte)"
    )
    time.sleep(0.65)
    try:
        gn = fetch_google_news(
            f"{name} UFC injury injured withdrawal medical",
            session,
            limit=sub_limit,
        )
        counts["injury_en"] = len(gn)
        add_items(gn)
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append("#### Lesão / notícias (português)")
    time.sleep(0.65)
    try:
        gn2 = fetch_google_news(
            f"{name} UFC lesão lesionado desistência médico",
            session,
            limit=sub_limit,
        )
        counts["injury_pt"] = len(gn2)
        add_items(gn2)
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append("#### Trajetória / entrevistas / vida pública (notícias)")
    time.sleep(0.65)
    try:
        gn3 = fetch_google_news(
            f"{name} UFC MMA fighter career interview",
            session,
            limit=sub_limit,
        )
        counts["bio"] = len(gn3)
        add_items(gn3)
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append("#### Eco X / Twitter (Google News · site twitter.com / x.com)")
    time.sleep(0.65)
    try:
        gnx = fetch_google_news_twitter_x(f"{name} UFC MMA", session, limit=min(10, sub_limit))
        counts["twitter_x_echo"] = len(gnx)
        add_items(gnx)
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append("#### Reddit r/MMA")
    time.sleep(0.85)
    try:
        rd = fetch_reddit_mma(f"{name} UFC", session, limit=min(15, sub_limit))
        counts["reddit_mma"] = len(rd)
        for m in rd:
            key = (m.title or "").strip().lower()[:400]
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append("#### Reddit r/UFC")
    time.sleep(0.75)
    try:
        rd2 = fetch_reddit_sub("ufc", f"{name} UFC", session, limit=min(12, sub_limit))
        counts["reddit_ufc"] = len(rd2)
        for m in rd2:
            key = (m.title or "").strip().lower()[:400]
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")

    lines.append("")
    return "\n".join(lines), counts


def run_report(
    query: str,
    session: requests.Session,
    include_feeds: bool,
    *,
    google_limit: int = 25,
    reddit_limit: int = 25,
    rss_max_each: int = 40,
    rss_scan: int = 120,
    fighter_names: Optional[list[str]] = None,
    fighter_sub_limit: int = 10,
) -> str:
    """Monta o texto do relatório. Limites são explícitos no rodapé (## Resumo)."""
    lines: list[str] = []
    n_google = n_google_x = n_reddit = n_reddit_ufc = n_mmaf = n_bjp = 0
    rss_extra_counts: dict[str, int] = {}
    fighter_blocks: list[dict[str, Any]] = []

    tw_limit = max(5, min(google_limit, 20))

    lines.append(f"# Enriquecimento externo — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"# Consulta: {query}")
    lines.append("")

    lines.append(f"## Google News (RSS) — até {google_limit} itens")
    try:
        time.sleep(0.8)
        gn = fetch_google_news(query, session, limit=google_limit)
        n_google = len(gn)
        for m in gn:
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append(
        f"## Google News — ecos X / Twitter (via índice: site:twitter.com / site:x.com) — até {tw_limit} itens"
    )
    lines.append(
        "_Não é a API do X: são notícias/páginas que o Google associa a esses domínios._"
    )
    try:
        time.sleep(0.85)
        gnx = fetch_google_news_twitter_x(query, session, limit=tw_limit)
        n_google_x = len(gnx)
        for m in gnx:
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    lines.append(f"## Reddit r/MMA (busca) — até {reddit_limit} itens (máx. da API: 25)")
    try:
        time.sleep(1.0)
        rd = fetch_reddit_mma(query, session, limit=reddit_limit)
        n_reddit = len(rd)
        for m in rd:
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    ufc_lim = min(reddit_limit, 20)
    lines.append(f"## Reddit r/UFC (busca) — até {ufc_lim} itens (máx. da API: 25)")
    try:
        time.sleep(0.9)
        rd_u = fetch_reddit_sub("ufc", query, session, limit=ufc_lim)
        n_reddit_ufc = len(rd_u)
        for m in rd_u:
            lines.append(f"- {m.title}")
            if m.link:
                lines.append(f"  {m.link}")
            if m.extra:
                lines.append(f"  ({m.extra})")
    except Exception as e:
        lines.append(f"(erro: {e})")
    lines.append("")

    if include_feeds:
        kws = [w for w in re.split(r"[\s,]+", query) if len(w) > 2][:8]
        lines.append(
            f"## MMA Fighting (RSS filtrado) — lê até {rss_scan} posts do feed, mostra até {rss_max_each} com match"
        )
        try:
            time.sleep(0.8)
            mmaf = fetch_rss_filtered(
                MMA_FIGHTING_RSS,
                session,
                kws,
                "MMA Fighting",
                scan_limit=rss_scan,
                max_return=rss_max_each,
            )
            n_mmaf = len(mmaf)
            for m in mmaf:
                lines.append(f"- {m.title}")
                if m.link:
                    lines.append(f"  {m.link}")
        except Exception as e:
            lines.append(f"(erro: {e})")
        lines.append("")

        lines.append(
            f"## BJPenn.com (RSS filtrado) — lê até {rss_scan} posts do feed, mostra até {rss_max_each} com match"
        )
        try:
            time.sleep(0.8)
            bjp = fetch_rss_filtered(
                BJPENN_RSS,
                session,
                kws,
                "BJPenn",
                scan_limit=rss_scan,
                max_return=rss_max_each,
            )
            n_bjp = len(bjp)
            for m in bjp:
                lines.append(f"- {m.title}")
                if m.link:
                    lines.append(f"  {m.link}")
        except Exception as e:
            lines.append(f"(erro: {e})")

        for feed_url, feed_label in EXTRA_RSS_FEEDS:
            lines.append(
                f"## {feed_label} (RSS filtrado) — lê até {rss_scan}, até {rss_max_each} com match"
            )
            try:
                time.sleep(0.75)
                ex = fetch_rss_filtered(
                    feed_url,
                    session,
                    kws,
                    feed_label,
                    scan_limit=rss_scan,
                    max_return=rss_max_each,
                )
                rss_extra_counts[feed_label] = len(ex)
                for m in ex:
                    lines.append(f"- {m.title}")
                    if m.link:
                        lines.append(f"  {m.link}")
            except Exception as e:
                lines.append(f"(erro: {e})")
                rss_extra_counts[feed_label] = 0
            lines.append("")

    if fighter_names:
        lines.append("## Por lutador(a) — lesões, notícias, contexto (agregado da web)")
        lines.append("")
        lines.append(
            "Não é diagnóstico médico nem fonte oficial da UFC; use para orientar leitura extra."
        )
        lines.append("")
        for raw_name in fighter_names:
            nm = (raw_name or "").strip()
            if not nm:
                continue
            try:
                blk, fc = run_fighter_intel_block(
                    nm,
                    session,
                    sub_limit=fighter_sub_limit,
                )
                fighter_blocks.append(fc)
                lines.append(blk)
                lines.append("---")
                lines.append("")
            except Exception as e:
                lines.append(f"### {nm}\n(erro: {e})\n")
                lines.append("---")
                lines.append("")

    lines.append("")
    lines.append("## Resumo (itens listados neste relatório)")
    lines.append("")
    lines.append(f"- Google News: {n_google} (limite pedido: {google_limit})")
    lines.append(f"- Google News (X/Twitter via site:): {n_google_x} (até {tw_limit})")
    lines.append(f"- Reddit r/MMA: {n_reddit} (limite pedido: {reddit_limit})")
    lines.append(f"- Reddit r/UFC: {n_reddit_ufc} (até {ufc_lim})")
    if include_feeds:
        lines.append(f"- MMA Fighting (após filtro): {n_mmaf} (máx. {rss_max_each})")
        lines.append(f"- BJPenn (após filtro): {n_bjp} (máx. {rss_max_each})")
        for lbl, cnt in rss_extra_counts.items():
            lines.append(f"- {lbl} (após filtro): {cnt}")
    if fighter_blocks:
        lines.append(f"- Blocos por lutador: {len(fighter_blocks)} (até ~{fighter_sub_limit} notícias por sub-busca)")
    lines.append(
        "- Nota: o terminal pode cortar a visualização; o arquivo em disco contém o texto completo."
    )
    lines.append("")

    lines.append("## JSON (resumo para outro script)")
    summary = {
        "query": query,
        "counts": {
            "google_news": n_google,
            "google_news_twitter_x": n_google_x,
            "reddit_mma": n_reddit,
            "reddit_ufc": n_reddit_ufc,
            "mma_fighting": n_mmaf if include_feeds else None,
            "bjpenn": n_bjp if include_feeds else None,
            "rss_extra": rss_extra_counts if include_feeds else {},
        },
        "fighters": fighter_blocks if fighter_blocks else None,
        "limits": {
            "google": google_limit,
            "reddit": reddit_limit,
            "rss_max_each": rss_max_each if include_feeds else None,
            "fighter_sub_queries": fighter_sub_limit if fighter_blocks else None,
        },
        "sources": [
            "google_news_rss",
            "google_news_twitter_x_filter",
            "reddit_mma",
            "reddit_ufc",
        ]
        + (["mma_fighting_rss", "bjpenn_rss"] + [x[1] for x in EXTRA_RSS_FEEDS] if include_feeds else []),
        "note": "Texto acima é agregado; valide links manualmente.",
    }
    lines.append("```json")
    lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _last_name_token(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if not parts:
        return ""
    return _strip_accents(parts[-1]).lower()


def _fighter_match_tokens(display_name: str, athlete_slug: str) -> list[str]:
    """
    Tokens para casar títulos de imprensa com o card.
    Inclui o último token do nome exibido e o último segmento do slug UFC (sobrenome real),
    ex.: «Patricio Pitbull» + patricio-pitbull-freire → pitbull, freire.
    """
    out: list[str] = []
    seen: set[str] = set()

    def push(raw: str) -> None:
        s = _strip_accents((raw or "").strip().lower())
        if len(s) < 3 or not re.fullmatch(r"[a-z]+", s) or s in seen:
            return
        if s in ("junior", "senior"):
            return
        seen.add(s)
        out.append(s)

    push(_last_name_token(display_name))
    sl = (athlete_slug or "").strip().lower().strip("/")
    parts = [p for p in sl.split("-") if p]
    if parts:
        push(parts[-1])
    return out


def _any_ln_in_text(tokens: list[str], text_lower: str) -> bool:
    return any(_ln_in_text(t, text_lower) for t in tokens if len(t) >= 3)


def _rfind_any_token(tokens: list[str], segment_lower: str) -> int:
    best = -1
    for t in tokens:
        if len(t) < 3:
            continue
        p = segment_lower.rfind(t)
        if p > best:
            best = p
    return best


def _ln_in_text(ln: str, text_lower: str) -> bool:
    if not ln or len(ln) < 3:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(ln)}(?![a-z0-9])", text_lower))


def _pair_in_mention_title_tokens(
    title: str, red_tokens: list[str], blue_tokens: list[str]
) -> bool:
    low = (title or "").lower()
    return _any_ln_in_text(red_tokens, low) and _any_ln_in_text(blue_tokens, low)


def _vote_winner_from_title(
    low: str, red_tokens: list[str], blue_tokens: list[str]
) -> Optional[Literal["red", "blue"]]:
    """
    Heurística em títulos de notícia/Reddit (EN/PT).
    Requer que algum token de cada canto apareça em low.
    """
    if not _any_ln_in_text(red_tokens, low) or not _any_ln_in_text(blue_tokens, low):
        return None

    win_seps = (
        " defeats ",
        " defeat ",
        " def. ",
        " def ",
        " beats ",
        " beat ",
        " knocks out ",
        " knocked out ",
        " submits ",
        " submitted ",
        " outpoints ",
        " outpointed ",
        " edges ",
        " stops ",
        " finishes ",
        " finished ",
        " tko ",
        " ko ",
        " over ",
    )
    for sep in win_seps:
        if sep not in low:
            continue
        i = low.find(sep)
        before, after = low[:i], low[i + len(sep) :]
        r_before = _any_ln_in_text(red_tokens, before)
        b_before = _any_ln_in_text(blue_tokens, before)
        r_after = _any_ln_in_text(red_tokens, after)
        b_after = _any_ln_in_text(blue_tokens, after)
        if r_before and b_after and not b_before and not r_after:
            return "red"
        if b_before and r_after and not r_before and not b_after:
            return "blue"
        if r_before and b_after and b_before and r_after:
            br = _rfind_any_token(red_tokens, before)
            bb = _rfind_any_token(blue_tokens, before)
            if br > bb:
                return "red"
            if bb > br:
                return "blue"

    loss_seps = (" loses to ", " lost to ", " falls to ", " defeated by ", " beaten by ")
    for sep in loss_seps:
        if sep not in low:
            continue
        i = low.find(sep)
        before, after = low[:i], low[i + len(sep) :]
        if _any_ln_in_text(red_tokens, before) and _any_ln_in_text(blue_tokens, after):
            return "blue"
        if _any_ln_in_text(blue_tokens, before) and _any_ln_in_text(red_tokens, after):
            return "red"

    pt_transitive = (
        " nocauteia ",
        " nocauteou ",
        " finaliza ",
        " finalizou ",
        " derrota ",
        " derrotou ",
        " supera ",
        " superou ",
    )
    for sep in pt_transitive:
        if sep not in low:
            continue
        i = low.find(sep)
        before, after = low[:i], low[i + len(sep) :]
        if _any_ln_in_text(red_tokens, before) and _any_ln_in_text(blue_tokens, after):
            return "red"
        if _any_ln_in_text(blue_tokens, before) and _any_ln_in_text(red_tokens, after):
            return "blue"

    if " vence " in low or " venceu " in low:
        sep = " venceu " if " venceu " in low else " vence "
        i = low.find(sep)
        before_core = low[:i]
        after = low[i + len(sep) :]
        if _any_ln_in_text(blue_tokens, before_core[-40:]) and _any_ln_in_text(red_tokens, after[:50]):
            return "blue"
        if _any_ln_in_text(red_tokens, before_core[-40:]) and _any_ln_in_text(blue_tokens, after[:50]):
            return "red"

    for marker in (" wins", " won ", " winner:", " gets the nod", " takes ", " victoire "):
        if marker not in low:
            continue
        i = low.find(marker)
        window = low[max(0, i - 55) : i]
        if _any_ln_in_text(red_tokens, window) and not _any_ln_in_text(blue_tokens, window):
            return "red"
        if _any_ln_in_text(blue_tokens, window) and not _any_ln_in_text(red_tokens, window):
            return "blue"

    return None


def infer_winner_for_pair_from_mentions(
    mentions: list[Mention],
    red_name: str,
    blue_name: str,
    *,
    red_slug: str = "",
    blue_slug: str = "",
) -> Optional[tuple[Literal["red", "blue"], int, int]]:
    """
    Devolve (lado, votos_a_favor, votos_contra) ou None.
    Exige maioria clara (≥2 a mais que o outro, ou 1 voto só com padrão forte).
    """
    r_tokens = _fighter_match_tokens(red_name, red_slug)
    b_tokens = _fighter_match_tokens(blue_name, blue_slug)
    if not r_tokens or not b_tokens:
        return None

    strong_red = strong_blue = weak_red = weak_blue = 0
    strong_markers = (
        "defeat",
        "def.",
        "beats",
        "knocks out",
        "submits",
        "loses to",
        "lost to",
        " over ",
        " vence",
        " venceu",
        "nocaute",
        "finaliz",
        "derrot",
        "supera",
        "superou",
    )

    for m in mentions:
        t = m.title or ""
        if not _pair_in_mention_title_tokens(t, r_tokens, b_tokens):
            continue
        low = t.lower()
        side = _vote_winner_from_title(low, r_tokens, b_tokens)
        if not side:
            continue
        is_strong = any(x in low for x in strong_markers)
        if side == "red":
            if is_strong:
                strong_red += 1
            else:
                weak_red += 1
        else:
            if is_strong:
                strong_blue += 1
            else:
                weak_blue += 1

    tot_r = strong_red + weak_red
    tot_b = strong_blue + weak_blue
    if strong_red >= 1 and strong_red > strong_blue and tot_r > tot_b:
        return ("red", tot_r, tot_b)
    if strong_blue >= 1 and strong_blue > strong_red and tot_b > tot_r:
        return ("blue", tot_b, tot_r)
    if tot_r >= 2 and tot_r >= tot_b + 2:
        return ("red", tot_r, tot_b)
    if tot_b >= 2 and tot_b >= tot_r + 2:
        return ("blue", tot_b, tot_r)
    if tot_r == 1 and tot_b == 0:
        return ("red", 1, 0)
    if tot_b == 1 and tot_r == 0:
        return ("blue", 1, 0)
    return None


def _mentions_cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"event_ext_mentions_{key}.json"


def gather_event_result_mentions(
    event_title: Optional[str],
    fighter_last_names: list[str],
    session: requests.Session,
    *,
    cache_dir: Optional[Path] = None,
    cache_ttl_seconds: float = 600.0,
    force_refresh: bool = False,
) -> list[Mention]:
    """
    Agrega Google News, X (via Google News), Reddit r/MMA e r/UFC, e RSS filtrados,
    para localizar títulos que mencionem resultados do evento.
    """
    base = re.sub(r"\s*\|\s*UFC.*$", "", (event_title or "").strip(), flags=re.I)
    kws_unique: list[str] = []
    seen_kw: set[str] = set()
    for tok in re.findall(r"[A-Za-z0-9]+", base):
        tlow = tok.lower()
        if len(tlow) > 2 and tlow not in seen_kw:
            seen_kw.add(tlow)
            kws_unique.append(tok)
    for ln in fighter_last_names:
        ln = (ln or "").strip()
        if len(ln) > 2 and ln.lower() not in seen_kw:
            seen_kw.add(ln.lower())
            kws_unique.append(ln)

    cache_key = hashlib.sha256(
        f"{base}|{','.join(sorted(seen_kw))}".encode("utf-8")
    ).hexdigest()[:22]
    if cache_dir is not None and not force_refresh:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cpath = _mentions_cache_path(cache_dir, cache_key)
        if cpath.is_file():
            age = time.time() - cpath.stat().st_mtime
            if cache_ttl_seconds <= 0 or age <= cache_ttl_seconds:
                try:
                    raw = json.loads(cpath.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        return [
                            Mention(
                                str(x.get("source") or ""),
                                str(x.get("title") or ""),
                                str(x.get("link") or ""),
                                str(x.get("extra") or ""),
                            )
                            for x in raw
                            if isinstance(x, dict)
                        ]
                except (json.JSONDecodeError, OSError, TypeError):
                    pass

    mentions: list[Mention] = []
    seen_titles: set[str] = set()

    def add_batch(items: list[Mention]) -> None:
        for m in items:
            k = (m.title or "").strip().lower()[:500]
            if not k or k in seen_titles:
                continue
            seen_titles.add(k)
            mentions.append(m)

    queries: list[str] = []
    if base:
        queries.append(f"{base} UFC results")
        queries.append(f"{base} UFC recap")
    queries.append("UFC latest results fight night")

    for q in queries[:3]:
        try:
            time.sleep(0.35)
            add_batch(fetch_google_news(q, session, limit=16))
        except Exception:
            pass

    try:
        time.sleep(0.35)
        qx = f"{base} UFC" if base else "UFC fight results"
        add_batch(fetch_google_news_twitter_x(qx, session, limit=12))
    except Exception:
        pass

    rq = f"{base} results" if base else "UFC results"
    try:
        time.sleep(0.45)
        add_batch(fetch_reddit_mma(rq, session, limit=14))
    except Exception:
        pass
    try:
        time.sleep(0.4)
        add_batch(fetch_reddit_sub("UFC", rq, session, limit=12))
    except Exception:
        pass

    if base:
        try:
            time.sleep(0.32)
            add_batch(
                fetch_google_news(
                    f"site:superlutas.com.br {base} UFC",
                    session,
                    limit=12,
                    source_label="Google News · Super Lutas",
                )
            )
        except Exception:
            pass

    kw_for_rss = kws_unique[:12]
    if kw_for_rss:
        for feed_url, label in EXTRA_RSS_FEEDS[:6]:
            try:
                time.sleep(0.22)
                add_batch(
                    fetch_rss_filtered(
                        feed_url,
                        session,
                        kw_for_rss,
                        label,
                        scan_limit=90,
                        max_return=12,
                    )
                )
            except Exception:
                pass

    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            ser = [
                {"source": m.source, "title": m.title, "link": m.link, "extra": m.extra}
                for m in mentions
            ]
            _mentions_cache_path(cache_dir, cache_key).write_text(
                json.dumps(ser, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    return mentions


def fetch_pair_google_news_mentions(
    red_name: str,
    blue_name: str,
    session: requests.Session,
    *,
    red_slug: str = "",
    blue_slug: str = "",
) -> list[Mention]:
    """
    Buscas focadas no confronto (BR + EN). O agregado global do evento muitas vezes não traz
    títulos com os dois sobrenomes; isto melhora Pitbull/Freire vs Pico, etc.
    """
    r_toks = _fighter_match_tokens(red_name, red_slug)
    b_toks = _fighter_match_tokens(blue_name, blue_slug)
    if not r_toks or not b_toks:
        return []

    out: list[Mention] = []
    seen: set[str] = set()

    def add_batch(items: list[Mention]) -> None:
        for m in items:
            k = (m.title or "").strip().lower()[:500]
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(m)

    # Sobrenomes mais «jornalísticos» costumam ser o último token derivado do slug
    r_key = r_toks[-1]
    b_key = b_toks[-1]
    queries: list[tuple[str, str, str, str]] = [
        (f"{r_key} {b_key} UFC", "pt-BR", "BR", "BR:pt-419"),
        (f"{r_key} {b_key} UFC", "en", "US", "US:en"),
        (f"{r_key} {b_key} MMA", "en", "US", "US:en"),
    ]
    vs_q = f"{(red_name or '').strip()} vs {(blue_name or '').strip()} UFC"
    if len(vs_q) <= 130:
        queries.append((vs_q, "pt-BR", "BR", "BR:pt-419"))

    for q, hl, gl, ceid in queries[:5]:
        try:
            time.sleep(0.32)
            label = "Google News" if hl == "pt-BR" else "Google News (EN)"
            add_batch(
                fetch_google_news(
                    q,
                    session,
                    limit=12,
                    source_label=label,
                    hl=hl,
                    gl=gl,
                    ceid=ceid,
                )
            )
        except Exception:
            continue

    return out


def enrich_event_results_fights_from_external(
    fights: list[dict[str, Any]],
    event_title: Optional[str],
    session: requests.Session,
    *,
    cache_dir: Optional[Path] = None,
    cache_ttl_seconds: float = 600.0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Para lutas ainda ``scheduled``, tenta inferir vencedor a partir de menções na imprensa/Reddit/RSS.
    Preenche ``result_source`` = ``external_press``; não substitui resultados oficiais já preenchidos.
    """
    scheduled = [f for f in fights if isinstance(f, dict) and f.get("status") == "scheduled"]
    if not scheduled:
        return {"attempted": False, "mentions_count": 0, "filled": 0}

    last_names: list[str] = []
    for f in scheduled:
        for nk, sk in (("red_name", "red_slug"), ("blue_name", "blue_slug")):
            n = str(f.get(nk) or "").strip()
            slug = str(f.get(sk) or "").strip()
            if n or slug:
                for tok in _fighter_match_tokens(n, slug):
                    last_names.append(tok)

    mentions = gather_event_result_mentions(
        event_title,
        last_names,
        session,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )

    filled = 0
    pair_mentions_total = 0
    for f in scheduled:
        rn = str(f.get("red_name") or "")
        bn = str(f.get("blue_name") or "")
        rs = str(f.get("red_slug") or "")
        bs = str(f.get("blue_slug") or "")
        merged: list[Mention] = list(mentions)
        inferred = infer_winner_for_pair_from_mentions(
            merged,
            rn,
            bn,
            red_slug=rs,
            blue_slug=bs,
        )
        if not inferred:
            try:
                extra = fetch_pair_google_news_mentions(
                    rn, bn, session, red_slug=rs, blue_slug=bs
                )
                pair_mentions_total += len(extra)
                if extra:
                    merged = merged + extra
                    inferred = infer_winner_for_pair_from_mentions(
                        merged,
                        rn,
                        bn,
                        red_slug=rs,
                        blue_slug=bs,
                    )
            except Exception:
                inferred = None
        if not inferred:
            continue
        side, vr, vb = inferred
        f["status"] = "completed"
        f["winner_side"] = side
        rn = str(f.get("red_name") or "")
        bn = str(f.get("blue_name") or "")
        f["winner_name"] = rn if side == "red" else bn
        f["result_source"] = "external_press"
        f["external_inferred"] = True
        f["external_mention_votes"] = {"red": vr, "blue": vb}
        if not f.get("method_text"):
            f["method_text"] = None
        f["method_bucket"] = None
        filled += 1

    return {
        "attempted": True,
        "mentions_count": len(mentions),
        "pair_mentions_added": pair_mentions_total,
        "filled": filled,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Busca menções em sites (RSS/API) sobre UFC para contexto extra."
    )
    parser.add_argument(
        "--query",
        "-q",
        help="Termos de busca, ex.: 'UFC Seattle Adesanya Pyfer 2026'",
    )
    parser.add_argument(
        "--event-url",
        "-u",
        help="URL do evento em ufc.com.br — usa o título (og:title) como consulta.",
    )
    parser.add_argument(
        "--no-feeds",
        action="store_true",
        help="Não busca RSS de MMA Fighting / BJPenn (só Google News + Reddit).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        metavar="N",
        help="Máximo de itens no Google News e no Reddit (Reddit: no máx. 25). Padrão: 25.",
    )
    parser.add_argument(
        "--rss-max",
        type=int,
        default=40,
        metavar="N",
        help="Máximo de posts por feed MMA Fighting / BJPenn após filtro. Padrão: 40.",
    )
    parser.add_argument(
        "--rss-scan",
        type=int,
        default=120,
        metavar="N",
        help="Quantos itens ler de cada feed RSS antes de filtrar. Padrão: 120.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="ufc_enriquecimento_externo.txt",
        help="Arquivo de saída (texto).",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Se definido, grava também um JSON mínimo com lista de títulos/links.",
    )
    parser.add_argument(
        "--lutador",
        action="append",
        default=[],
        metavar="NOME",
        help="Repetir por lutador: bloco extra (lesão, notícias PT/EN, trajetória, Reddit).",
    )
    parser.add_argument(
        "--fighter-limit",
        type=int,
        default=10,
        metavar="N",
        help="Máx. resultados por sub-busca em cada bloco de lutador. Padrão: 10.",
    )
    args = parser.parse_args()

    session = requests.Session()
    query = args.query
    if args.event_url:
        q = fetch_event_page_query(args.event_url.strip(), session)
        if q:
            query = q
            print(f"Consulta derivada da página do evento: {query}\n", file=sys.stderr)
        elif not query:
            print(
                "Não foi possível ler o título do evento; use --query.",
                file=sys.stderr,
            )
            return 1
    if not query:
        print("Informe --query ou --event-url.", file=sys.stderr)
        return 1

    lim = max(1, min(args.limit, 100))
    rlim = min(lim, 25)
    lutadores = [x.strip() for x in args.lutador if x and str(x).strip()]
    text = run_report(
        query,
        session,
        include_feeds=not args.no_feeds,
        google_limit=lim,
        reddit_limit=rlim,
        rss_max_each=max(1, args.rss_max),
        rss_scan=max(20, args.rss_scan),
        fighter_names=lutadores if lutadores else None,
        fighter_sub_limit=max(3, min(args.fighter_limit, 20)),
    )
    out_path = Path(args.output).expanduser().resolve()
    out_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSalvo: {out_path}", file=sys.stderr)

    if args.json_out:
        # Coleta estruturada mínima
        payload = {
            "query": query,
            "google_news": [],
            "reddit": [],
        }
        try:
            time.sleep(0.5)
            for m in fetch_google_news(query, session, limit=20):
                payload["google_news"].append({"title": m.title, "link": m.link})
            time.sleep(1.0)
            for m in fetch_reddit_mma(query, session, limit=15):
                payload["reddit"].append({"title": m.title, "link": m.link, "meta": m.extra})
        except Exception as e:
            payload["error"] = str(e)
        Path(args.json_out).expanduser().resolve().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON: {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
