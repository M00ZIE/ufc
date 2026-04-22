"""Parlay: odds de perna, combinação e regras de liquidação."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from betting.db import connect, default_db_path, init_schema
from betting.parlay_math import (
    combined_decimal,
    compute_leg,
    leg_wins_against_result,
    normalize_method_option,
)
from betting.service import (
    ServiceError,
    max_stake_allowed,
    preview_parlay,
    settle_parlays_for_event,
)


def _fake_fight(*, tier: str = "SAFE") -> dict:
    return {
        "prob_red_pct": 60,
        "prob_blue_pct": 40,
        "favorite_corner": "red",
        "red": {"name": "A"},
        "blue": {"name": "B"},
        "methods_pct": {"ko_tko": 40, "decisao": 35, "finalizacao": 25},
        "if_favorite_wins_pct": {"ko_tko": 45, "decisao": 30, "finalizacao": 25},
        "advanced_prediction": {
            "weighted_model": {"prob_red_pct": 55, "prob_blue_pct": 45, "edge_score": 0.1},
            "risk": {"tier": tier, "reasons": [], "max_confidence_pct": 70},
            "value_bet": None,
        },
    }


class TestParlayMath(unittest.TestCase):
    def test_normalize_method(self) -> None:
        self.assertEqual(normalize_method_option("KO"), "ko_tko")
        self.assertEqual(normalize_method_option("Decisão"), "decisao")

    def test_final_result_leg(self) -> None:
        f = _fake_fight()
        leg = compute_leg(f, bet_type="final_result", side="red", option=None)
        self.assertFalse(leg.get("betting_blocked"))
        self.assertGreater(float(leg["decimal_odds"]), 1.0)

    def test_skip_blocks_leg(self) -> None:
        f = _fake_fight(tier="SKIP")
        leg = compute_leg(f, bet_type="final_result", side="red", option=None)
        self.assertTrue(leg.get("betting_blocked"))

    def test_combined_decimal(self) -> None:
        a = compute_leg(_fake_fight(), bet_type="final_result", side="red", option=None)
        b = compute_leg(_fake_fight(), bet_type="final_result", side="blue", option=None)
        c = combined_decimal([a, b])
        self.assertGreater(c, float(a["decimal_odds"]))

    def test_leg_wins_method(self) -> None:
        leg = {"bet_type": "method", "side": "red", "method_key": "ko_tko", "option": "KO"}
        self.assertTrue(
            leg_wins_against_result(leg, winner_side="red", official_method="ko_tko", round_num=None)
        )
        self.assertFalse(
            leg_wins_against_result(leg, winner_side="red", official_method="decisao", round_num=None)
        )

    def test_leg_wins_round(self) -> None:
        leg = {"bet_type": "round_winner", "side": "blue", "option": 3}
        self.assertTrue(leg_wins_against_result(leg, winner_side="blue", official_method=None, round_num=3))
        self.assertFalse(leg_wins_against_result(leg, winner_side="blue", official_method=None, round_num=2))


class TestParlayService(unittest.TestCase):
    def test_max_stake(self) -> None:
        self.assertEqual(max_stake_allowed(1000), 200)
        self.assertEqual(max_stake_allowed(17), 3)

    def test_preview_requires_two_legs(self) -> None:
        root = Path(__file__).resolve().parent.parent
        cache = root / ".ufc_html_cache"
        with self.assertRaises(ServiceError):
            preview_parlay("https://www.ufc.com.br/event/foo", [], cache)


class TestParlayDb(unittest.TestCase):
    def test_settle_parlay(self) -> None:
        root = Path(__file__).resolve().parent.parent
        dbp = root / "instance" / "test_parlay.sqlite3"
        dbp.parent.mkdir(parents=True, exist_ok=True)
        if dbp.exists():
            dbp.unlink()
        conn = connect(dbp)
        init_schema(conn)
        conn.execute(
            """INSERT INTO users (id, email, password_hash, balance, is_admin, blocked)
               VALUES (2, 'p@test.local', 'x', 5000, 0, 0)"""
        )
        legs = [
            {
                "fight_index": 1,
                "bet_type": "final_result",
                "side": "red",
                "decimal_odds": 2.0,
                "prob_leg": 0.5,
            },
            {
                "fight_index": 2,
                "bet_type": "final_result",
                "side": "blue",
                "decimal_odds": 2.0,
                "prob_leg": 0.5,
            },
        ]
        conn.execute(
            """INSERT INTO parlay_bets (user_id, event_url, stake, combined_odds, legs_json, status)
               VALUES (2, 'https://ufc.com.br/event/x', 100, 4.0, ?, 'open')""",
            (json.dumps(legs),),
        )
        conn.commit()

        out = settle_parlays_for_event(
            conn,
            event_url="https://ufc.com.br/event/x",
            outcomes=[
                {"fight_index": 1, "winner_side": "red"},
                {"fight_index": 2, "winner_side": "blue"},
            ],
        )
        self.assertTrue(out.get("ok"))
        self.assertGreaterEqual(out.get("parlays_settled", 0), 1)
        row = conn.execute("SELECT status, payout FROM parlay_bets WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "won")
        self.assertEqual(int(row["payout"]), 400)
        conn.close()


if __name__ == "__main__":
    unittest.main()
