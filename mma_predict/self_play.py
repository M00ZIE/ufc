"""
Self-play e versionamento de modelos: ganchos leves para avaliação offline e promoção de variantes.

A promoção automática pesada fica desativada por defeito; ``record_self_play_result`` alimenta o log.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from mma_predict.learning import learning_data_dir

MODEL_VERSION_DEFAULT = "model_v4"
_read_lock = threading.Lock()


def init_self_play_tables(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS self_play_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version_a TEXT NOT NULL,
            model_version_b TEXT NOT NULL,
            performance_diff REAL,
            winner_model TEXT,
            context_note TEXT,
            created_ts REAL NOT NULL
        )
        """
    )


def get_active_model_version(conn: Any) -> str:
    row = conn.execute("SELECT v FROM learning_meta WHERE k = ?", ("active_model_variant",)).fetchone()
    if row and row["v"]:
        return str(row["v"]).strip() or MODEL_VERSION_DEFAULT
    return MODEL_VERSION_DEFAULT


def set_active_model_version(conn: Any, version: str) -> None:
    conn.execute(
        "INSERT INTO learning_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        ("active_model_variant", version.strip() or MODEL_VERSION_DEFAULT),
    )


def record_self_play_result(
    conn: Any,
    *,
    version_a: str,
    version_b: str,
    performance_diff: float,
    winner: str,
    context_note: str = "",
) -> None:
    init_self_play_tables(conn)
    conn.execute(
        """
        INSERT INTO self_play_log (
            model_version_a, model_version_b, performance_diff, winner_model, context_note, created_ts
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (version_a, version_b, float(performance_diff), winner, context_note[:500], time.time()),
    )


def dynamic_model_router(
    *,
    context_bucket: str,
    regime: str,
    conn: Any,
) -> str:
    """
    Por defeito devolve a variante ativa em meta; futuro: comparar candidatos via self-play.
    """
    _ = (context_bucket, regime)
    return get_active_model_version(conn)


def read_active_model_variant() -> str:
    """Leitura isolada (sem depender de ``learning._lock``)."""
    path = learning_data_dir() / "predictions_store.sqlite"
    with _read_lock:
        conn = sqlite3.connect(str(path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
            init_self_play_tables(conn)
            return get_active_model_version(conn)
        finally:
            conn.close()
