"""Contrato para analisadores de evento (um por esporte)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class EventAnalyzer(Protocol):
    """Implementação: validar URL do evento + produzir JSON de análise."""

    @property
    def sport_id(self) -> str: ...

    def validate_event_url(self, url: str) -> bool:
        """True se a URL pode ser usada como fonte para este esporte."""
        ...

    def analyze(
        self,
        event_url: str,
        *,
        cache_dir: Optional[Path] = None,
        cache_hours: float = 24.0,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Executa a análise e devolve um dict serializável em JSON."""
        ...
