"""Odds derivadas de probabilidades (betting/odds_math)."""

from __future__ import annotations

import unittest

from betting.odds_math import (
    decimal_odds_for_side,
    odds_pair_from_probs,
    odds_pair_from_probs_vig,
)


class TestOddsMath(unittest.TestCase):
    def test_pair_sums_reasonable(self) -> None:
        pr, pb, orr, orb = odds_pair_from_probs(0.7, 0.3)
        self.assertAlmostEqual(pr + pb, 1.0, places=5)
        self.assertGreater(orr, 1.0)
        self.assertGreater(orb, 1.0)

    def test_seventy_thirty_example(self) -> None:
        _, _, orr, orb = odds_pair_from_probs(0.7, 0.3)
        self.assertAlmostEqual(orr, round(1 / 0.7, 2), places=1)
        self.assertAlmostEqual(orb, round(1 / 0.3, 2), places=1)

    def test_decimal_clamped(self) -> None:
        o = decimal_odds_for_side(0.01)
        self.assertLessEqual(o, 50.0)
        self.assertGreaterEqual(o, 1.01)
        self.assertAlmostEqual(o, 25.0, places=1)

    def test_skip_blocks(self) -> None:
        d = odds_pair_from_probs_vig(0.5, 0.5, "SKIP")
        self.assertTrue(d["betting_blocked"])

    def test_vig_lowers_odds_vs_fair(self) -> None:
        fair = odds_pair_from_probs(0.6, 0.4)
        vigd = odds_pair_from_probs_vig(0.6, 0.4, "SAFE")
        self.assertFalse(vigd["betting_blocked"])
        self.assertLess(vigd["decimal_odds_red"], fair[2])


if __name__ == "__main__":
    unittest.main()
