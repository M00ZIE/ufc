"""Aprendizagem online leve (mma_predict.learning)."""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import unittest


class TestLearningModule(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.mkdtemp()
        os.environ["MMA_LEARNING_DATA_DIR"] = self._td
        import mma_predict.learning as learning_mod

        self.learning = importlib.reload(learning_mod)

    def tearDown(self) -> None:
        shutil.rmtree(self._td, ignore_errors=True)
        del os.environ["MMA_LEARNING_DATA_DIR"]

    def test_make_fight_id_stable(self) -> None:
        L = self.learning
        a = L.make_fight_id("https://ufc.com/e1", 2)
        b = L.make_fight_id("https://ufc.com/e1", 2)
        c = L.make_fight_id("https://ufc.com/e1", 3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_log_outcome_adjusts_weights(self) -> None:
        L = self.learning
        w0 = L.get_matchup_weights()
        self.assertEqual(len(w0), 4)
        self.assertAlmostEqual(sum(w0), 1.0, places=5)

        url = "https://ufc.com/test-event"
        fi = 1
        fid = L.make_fight_id(url, fi)
        L.log_prediction(
            fight_id=fid,
            event_url=url,
            fight_index=fi,
            model_prob=0.85,
            monte_carlo_prob=0.84,
            confidence=0.85,
            volatility=1.2,
            term_strike=0.4,
            term_grap=0.1,
            term_tdd=-0.05,
            term_card=0.02,
            value_flag=False,
            red_slug="fighter-red-test",
            blue_slug="fighter-blue-test",
        )
        L.record_fight_outcome(url, fi, red_won=False)
        w1 = L.get_matchup_weights()
        self.assertAlmostEqual(sum(w1), 1.0, places=5)
        self.assertNotEqual(w0, w1)

        payload = L.get_learning_api_payload()
        self.assertIn("drift_score", payload)
        self.assertIn("model_stability", payload)
        self.assertIn("adaptive_confidence", payload)
        self.assertIn(payload["model_stability"], ("stable", "degrading", "improving"))

    def test_monte_noise_scale_non_negative(self) -> None:
        self.assertGreaterEqual(self.learning.get_monte_carlo_noise_scale(), 1.0)


if __name__ == "__main__":
    unittest.main()
