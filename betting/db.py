from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  balance INTEGER NOT NULL DEFAULT 1000,
  is_admin INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  amount INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  type TEXT NOT NULL,
  bet_id INTEGER,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  event_url TEXT NOT NULL,
  fight_index INTEGER NOT NULL,
  division TEXT,
  red_name TEXT,
  blue_name TEXT,
  side TEXT NOT NULL,
  stake INTEGER NOT NULL,
  odds_red REAL NOT NULL,
  odds_blue REAL NOT NULL,
  odds_taken REAL NOT NULL,
  prob_red REAL NOT NULL,
  prob_blue REAL NOT NULL,
  risk_tier TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  payout INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  settled_at TEXT,
  winner_side TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id);
CREATE INDEX IF NOT EXISTS idx_bets_event_fight ON bets(event_url, fight_index);
CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);

CREATE TABLE IF NOT EXISTS parlay_bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  event_url TEXT NOT NULL,
  stake INTEGER NOT NULL,
  combined_odds REAL NOT NULL,
  legs_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  payout INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  settled_at TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_parlay_user ON parlay_bets(user_id);
CREATE INDEX IF NOT EXISTS idx_parlay_event ON parlay_bets(event_url);
CREATE INDEX IF NOT EXISTS idx_parlay_status ON parlay_bets(status);
"""


def default_db_path(root: Path) -> Path:
    inst = root / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    return inst / "betting.sqlite3"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    migrate_users_columns(conn)
    migrate_parlay_tables(conn)
    migrate_transactions_parlay(conn)


def migrate_users_columns(conn: sqlite3.Connection) -> None:
    """Adiciona is_admin / blocked em bases antigas."""
    rows = conn.execute("PRAGMA table_info(users)").fetchall()
    cols = {r[1] for r in rows}
    if "is_admin" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "blocked" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def migrate_parlay_tables(conn: sqlite3.Connection) -> None:
    """Garante tabela parlay_bets em bases antigas (CREATE IF NOT EXISTS já cobre novas)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS parlay_bets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          event_url TEXT NOT NULL,
          stake INTEGER NOT NULL,
          combined_odds REAL NOT NULL,
          legs_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          payout INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          settled_at TEXT,
          FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parlay_user ON parlay_bets(user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parlay_event ON parlay_bets(event_url)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parlay_status ON parlay_bets(status)"
    )
    conn.commit()


def migrate_transactions_parlay(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(transactions)").fetchall()
    cols = {r[1] for r in rows}
    if "parlay_bet_id" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN parlay_bet_id INTEGER")
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}
