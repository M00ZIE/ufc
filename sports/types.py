"""Identificadores e envelope comum de análise por esporte."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SportId = Literal["ufc"]


class EventAnalysisEnvelope(TypedDict, total=False):
    """
    Campos presentes em respostas JSON de qualquer analisador registrado.
    Esportes podem acrescentar chaves próprias (ex.: fights no UFC, matches no futebol).
    """

    ok: bool
    sport: str
    event_url: str
    errors: list[str]


def merge_with_sport(payload: dict[str, Any], sport_id: str) -> dict[str, Any]:
    """Garante a chave `sport` no dict retornado ao cliente."""
    return {**payload, "sport": sport_id}
