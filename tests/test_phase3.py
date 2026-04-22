"""Ensemble fase 3 (Elo + Bayesiano + híbrido)."""

from __future__ import annotations

import unittest

from mma_predict import phase3_hybrid as p3
from mma_predict.bayesian import bayesian_posterior_red
from mma_predict.elo import elo_prob_red


class TestPhase3(unittest.TestCase):
    def test_elo_prob_symmetric(self) -> None:
        self.assertAlmostEqual(elo_prob_red(1500, 1500), 0.5, places=4)
        self.assertGreater(elo_prob_red(1600, 1500), 0.5)

    def test_bayesian_no_ml(self) -> None:
        p, w1, w2 = bayesian_posterior_red(0.62, None, w_prior=0.7, w_likelihood=0.0)
        self.assertAlmostEqual(p, 0.62, places=5)
        self.assertEqual(w2, 0.0)

    def test_ensemble_bounds(self) -> None:
        f = p3.ensemble_final_prob_red(0.6, None, 0.58, 0.55, has_ml=False)
        self.assertGreater(f, 0.4)
        self.assertLess(f, 0.75)


if __name__ == "__main__":
    unittest.main()
