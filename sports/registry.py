"""Registro de analisadores por esporte."""

from __future__ import annotations

from sports.protocol import EventAnalyzer

_ANALYZERS: dict[str, EventAnalyzer] = {}

DEFAULT_SPORT = "ufc"


def _ensure_ufc_analyzer() -> None:
    """Import pesado (ufc_event_analysis) só quando precisar — ajuda cold start na Vercel."""
    if "ufc" in _ANALYZERS:
        return
    from sports.ufc_analyzer import UfcEventAnalyzer

    _ANALYZERS["ufc"] = UfcEventAnalyzer()


def get_analyzer(sport_id: str) -> EventAnalyzer:
    _ensure_ufc_analyzer()
    key = (sport_id or "").strip().lower()
    if key not in _ANALYZERS:
        raise KeyError(key)
    return _ANALYZERS[key]


def list_sport_ids() -> list[str]:
    _ensure_ufc_analyzer()
    return sorted(_ANALYZERS.keys())


def register_analyzer(sport_id: str, analyzer: EventAnalyzer) -> None:
    """Para testes ou plugins."""
    _ANALYZERS[sport_id.strip().lower()] = analyzer
