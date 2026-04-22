"""Fase 5 (RL leve) e fase 6 (stress adversarial) — regressão mínima."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
import unittest


class TestPhase5Phase6(unittest.TestCase):
    def test_adversarial_simulation_contract(self) -> None:
        from mma_predict.adversarial_sim import run_adversarial_simulation

        adv = run_adversarial_simulation(
            0.72,
            disagreement=0.25,
            volatility=3.0,
            regime="stable_card",
            model_agreement=0.82,
            simulations=200,
            seed=42,
        )
        for k in (
            "adversarial_hit_rate",
            "worst_case_roi",
            "stress_test_score",
            "vulnerability_index",
            "simulations",
        ):
            self.assertIn(k, adv)

    def test_select_action_skips_extreme_vulnerability(self) -> None:
        td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = td
        try:
            import mma_predict.learning as L
            import mma_predict.rl_policy as rlp

            importlib.reload(L)
            importlib.reload(rlp)
            action, _ = rlp.select_action(
                final_prob=0.66,
                edge_max=0.08,
                confidence=0.66,
                agreement=0.85,
                regime="stable_card",
                volatility=1.0,
                adversarial_risk=0.2,
                roi_context="general",
                has_odds_edge=True,
                vulnerability_index=0.92,
            )
            self.assertEqual(action, "SKIP")
        finally:
            shutil.rmtree(td, ignore_errors=True)
            del os.environ["MMA_LEARNING_DATA_DIR"]

    def test_read_active_model_variant(self) -> None:
        td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = td
        try:
            import mma_predict.learning as L
            import mma_predict.self_play as sp

            importlib.reload(L)
            importlib.reload(sp)
            v = sp.read_active_model_variant()
            self.assertIsInstance(v, str)
            self.assertTrue(len(v) > 0)
        finally:
            shutil.rmtree(td, ignore_errors=True)
            del os.environ["MMA_LEARNING_DATA_DIR"]

    def test_log_prediction_phase5_and_outcome(self) -> None:
        td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = td
        try:
            import mma_predict.learning as L

            importlib.reload(L)
            url = "https://ufc.com/p5-test"
            fi = 1
            fid = L.make_fight_id(url, fi)
            p5 = {
                "action": "BET_LOW",
                "stake_fraction": 0.01,
                "edge_max": 0.05,
                "decimal_odds_taken": 1.9,
                "bet_side": "red",
                "roi_context": "general",
                "stress_test_score": 0.3,
                "worst_case_roi": -1.0,
                "adversarial_risk": 0.4,
            }
            L.log_prediction(
                fight_id=fid,
                event_url=url,
                fight_index=fi,
                model_prob=0.6,
                monte_carlo_prob=0.59,
                confidence=0.6,
                volatility=1.0,
                term_strike=0.1,
                term_grap=0.1,
                term_tdd=0.1,
                term_card=0.1,
                phase5_snapshot=json.dumps(p5),
            )
            L.record_fight_outcome(url, fi, red_won=True)
        finally:
            shutil.rmtree(td, ignore_errors=True)
            del os.environ["MMA_LEARNING_DATA_DIR"]

    def test_predictor_import(self) -> None:
        from mma_predict.predictor import predict_fight_advanced

        self.assertTrue(callable(predict_fight_advanced))


if __name__ == "__main__":
    unittest.main()
