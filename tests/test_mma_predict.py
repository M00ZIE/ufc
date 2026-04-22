"""Testes do modelo ponderado MMA (sem rede)."""

from __future__ import annotations

import unittest

from ufc_event_analysis import FighterProfile, HistoryBrief

from mma_predict.feature_engineering import build_match_components
from mma_predict.model import edge_to_prob_red, weighted_edge
from mma_predict.predictor import classify_risk, predict_fight_advanced


def _fighter(
    *,
    name: str,
    slpm: float | None = 4.0,
    sdef: float | None = 50.0,
    td: float | None = 0.0,
    sub: float | None = 0.0,
    tdd: float | None = 50.0,
    w: int = 10,
    l: int = 2,
    rank: int | None = 5,
    last5_w: int = 3,
    last5_l: int = 2,
    ko_loss: int = 0,
    sub_loss: int = 0,
) -> FighterProfile:
    h = HistoryBrief(
        total_in_page=10,
        last5_w=last5_w,
        last5_l=last5_l,
        last5_ko_losses=ko_loss,
        last5_sub_losses=sub_loss,
        sequence="W-W-W-L-L",
        line_detail="—",
    )
    return FighterProfile(
        slug=name.lower().replace(" ", "-"),
        name=name,
        wins=w,
        losses=l,
        draws=0,
        ufc_rank=rank,
        sig_str_lpm=slpm,
        sig_str_abs_lpm=4.0,
        str_def_pct=sdef,
        td_avg=td,
        sub_per_15=sub,
        td_def_pct=tdd,
        history=h,
    )


class TestWeightedModel(unittest.TestCase):
    def test_probs_sum_one(self) -> None:
        red = _fighter(name="A", slpm=6.0, sdef=60.0, rank=2)
        blue = _fighter(name="B", slpm=3.0, sdef=45.0, rank=10)
        comp = build_match_components(red, blue, 2, 10)
        edge = weighted_edge(comp)
        pr, pb = edge_to_prob_red(edge)
        self.assertGreater(pr, 0.55)
        self.assertAlmostEqual(pr + pb, 1.0, places=6)

    def test_logit_monotone(self) -> None:
        p0, _ = edge_to_prob_red(0.0)
        p1, _ = edge_to_prob_red(0.5)
        self.assertLess(abs(p0 - 0.5), 0.01)
        self.assertGreater(p1, p0)

    def test_predict_includes_risk(self) -> None:
        red = _fighter(name="Dom", slpm=5.5, rank=1, last5_w=5, last5_l=0)
        blue = _fighter(name="Sub", slpm=3.0, rank=14, last5_w=1, last5_l=4)
        out = predict_fight_advanced(
            red,
            blue,
            red_rank_card=1,
            blue_rank_card=14,
            red_display_name="Dom",
            blue_display_name="Sub",
        )
        self.assertIn("weighted_model", out)
        self.assertIn("risk", out)
        self.assertIn(out["risk"]["tier"], ("SAFE", "RISKY", "SKIP"))
        self.assertIn("model_prob", out)
        self.assertIn("monte_carlo_prob", out)
        self.assertIn("volatility", out)
        self.assertIsInstance(out["value_bet"], bool)
        pm = out.get("phase3_model") or {}
        for k in (
            "elo_rating_diff",
            "elo_prob",
            "bayesian_prob",
            "final_prob",
            "model_agreement",
            "uncertainty",
        ):
            self.assertIn(k, pm)
        p4 = out.get("phase4_model") or {}
        for k in ("ensemble_weights", "regime", "roi_context", "value_score_dynamic_threshold"):
            self.assertIn(k, p4)
        p7 = out.get("phase7_bankroll") or {}
        for k in (
            "recommended_stake",
            "bankroll_exposure",
            "risk_budget_status",
            "expected_bankroll_impact",
            "drawdown_risk",
        ):
            self.assertIn(k, p7)

    def test_skip_when_close(self) -> None:
        red = _fighter(name="X", slpm=4.0, sdef=50.0, rank=5, w=8, l=8, last5_w=2, last5_l=2)
        blue = _fighter(name="Y", slpm=4.0, sdef=50.0, rank=5, w=8, l=8, last5_w=2, last5_l=2)
        comp = build_match_components(red, blue, 5, 5)
        edge = weighted_edge(comp)
        pr, _ = edge_to_prob_red(edge)
        self.assertLess(abs(pr - 0.5), 0.06, msg=f"edge={edge} pr={pr}")
        r = classify_risk(pr, favorite_corner="red", red=red, blue=blue)
        self.assertEqual(r["tier"], "SKIP")


if __name__ == "__main__":
    unittest.main()
