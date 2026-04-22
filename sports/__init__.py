"""
Camada multi-esporte: Protocol, registry e analisadores por modalidade.

Hoje só existe `ufc`. Novos esportes: implementar `EventAnalyzer`, registrar em
`registry._ANALYZERS` (ou usar `register_analyzer` em bootstrap).
"""

from sports.protocol import EventAnalyzer
from sports.registry import DEFAULT_SPORT, get_analyzer, list_sport_ids, register_analyzer
from sports.types import EventAnalysisEnvelope, SportId, merge_with_sport

__all__ = [
    "DEFAULT_SPORT",
    "EventAnalysisEnvelope",
    "EventAnalyzer",
    "SportId",
    "get_analyzer",
    "list_sport_ids",
    "merge_with_sport",
    "register_analyzer",
]
