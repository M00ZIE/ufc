"""Fase 7 — bankroll, orçamentos e MC de portefólio."""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import unittest


class TestPhase7Bankroll(unittest.TestCase):
    def test_init_tables_and_meta(self) -> None:
        td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = td
        try:
            import mma_predict.learning as L
            import mma_predict.bankroll as br

            importlib.reload(L)
            importlib.reload(br)
            conn = L._conn()
            try:
                br.init_phase7_tables(conn)
                s = br.load_portfolio_norms(conn)
                self.assertIn("bankroll_norm", s)
                self.assertGreaterEqual(float(s["current_drawdown"]), 0.0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)
            del os.environ["MMA_LEARNING_DATA_DIR"]

    def test_portfolio_mc_shape(self) -> None:
        from mma_predict.bankroll import monte_carlo_portfolio_correlated

        r = monte_carlo_portfolio_correlated(
            [0.6, 0.55, 0.62],
            stake_fraction_per_fight=0.02,
            decimal_odds=1.9,
            correlation=0.25,
            simulations=800,
            seed=7,
        )
        self.assertIn("expected_bankroll_growth", r)
        self.assertEqual(r["n_legs"], 3)

    def test_disagreement_blocks_budget(self) -> None:
        td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = td
        try:
            import mma_predict.learning as L
            import mma_predict.bankroll as br

            importlib.reload(L)
            importlib.reload(br)
            conn = L._conn()
            try:
                br.init_phase7_tables(conn)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_meta (
                        k TEXT PRIMARY KEY,
                        v TEXT NOT NULL
                    )
                    """
                )
                d = br.evaluate_risk_budgets(
                    disagreement=0.95,
                    regime="stable_card",
                    context_bucket="mw",
                    prior_event_stake=0.0,
                    proposed_stake=0.05,
                    conn=conn,
                    event_id="evt1",
                )
                self.assertEqual(d["status"], "blocked")
                self.assertEqual(d["stake_after_budget"], 0.0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)
            del os.environ["MMA_LEARNING_DATA_DIR"]


if __name__ == "__main__":
    unittest.main()
