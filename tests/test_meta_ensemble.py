"""Meta-ensemble fase 4 (pesos + contexto)."""

from __future__ import annotations

import unittest

from mma_predict.meta_ensemble import (
    composite_key,
    context_bucket,
    ensemble_prob_red,
    fetch_weights_for_prediction,
)


class TestMetaEnsemble(unittest.TestCase):
    def test_composite_key(self) -> None:
        self.assertIn("@@", composite_key("a|b", "stable_card"))

    def test_ensemble_prob_sum_sane(self) -> None:
        w = fetch_weights_for_prediction(composite_key("general", "stable_card"), has_ml=False)
        p = ensemble_prob_red(0.55, 0.52, 0.48, None, w, has_ml=False)
        self.assertGreater(p, 0.4)
        self.assertLess(p, 0.65)

    def test_context_bucket_string(self) -> None:
        from ufc_event_analysis import FighterProfile, HistoryBrief

        h = HistoryBrief(10, 3, 2, 0, 0, "W-L-W", "—")
        r = FighterProfile("a", "A", 3, 5, 0, None, history=h)
        b = FighterProfile("b", "B", 3, 5, 0, None, history=h)
        s = context_bucket("", r, b, 4.0)
        self.assertIsInstance(s, str)


if __name__ == "__main__":
    unittest.main()
