"""Adaptador: análise de eventos UFC (delega a ufc_event_analysis)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ufc_event_analysis import analyze_event_json

from sports.types import merge_with_sport
from sports.ufc_urls import allowed_ufc_event_url


class UfcEventAnalyzer:
    _id = "ufc"

    @property
    def sport_id(self) -> str:
        return self._id

    def validate_event_url(self, url: str) -> bool:
        return allowed_ufc_event_url(url)

    def analyze(
        self,
        event_url: str,
        *,
        cache_dir: Optional[Path] = None,
        cache_hours: float = 24.0,
        refresh: bool = False,
    ) -> dict[str, Any]:
        raw = analyze_event_json(
            event_url,
            cache_dir=cache_dir,
            cache_hours=cache_hours,
            refresh=refresh,
        )
        return merge_with_sport(raw, self.sport_id)
