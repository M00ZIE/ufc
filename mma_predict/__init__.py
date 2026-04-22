"""Modelo ponderado + risco/EV opcional, alimentado por perfis UFC (ufc_event_analysis)."""

from mma_predict.learning import (
    detect_drift,
    get_learning_api_payload,
    log_prediction,
    record_fight_outcome,
    write_learning_report,
)
from mma_predict.predictor import predict_fight_advanced
from mma_predict.simulation import monte_carlo_prob, simulate_parlay

__all__ = [
    "predict_fight_advanced",
    "monte_carlo_prob",
    "simulate_parlay",
    "detect_drift",
    "get_learning_api_payload",
    "log_prediction",
    "record_fight_outcome",
    "write_learning_report",
]
