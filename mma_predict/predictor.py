"""
Saída: probabilidade de confronto (diferenças + logística), Monte Carlo, volatilidade,
tier de risco (SAFE / RISKY / SKIP), EV opcional com odds.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from ufc_event_analysis import FighterProfile

from mma_predict.bayesian import bayesian_posterior_red
from mma_predict.data_collection import fight_pair_rows
from mma_predict.elo import read_matchup_snapshot
from mma_predict.feature_engineering import build_match_components
from mma_predict.learning import (
    detect_drift,
    get_matchup_weights,
    get_monte_carlo_noise_scale,
    get_value_edge_margin,
    get_volatility_risk_multiplier,
)
from mma_predict.matchup_model import run_matchup_probabilities
from mma_predict.model import edge_to_prob_red, run_weighted_model, weighted_edge
from mma_predict import meta_ensemble as me
from mma_predict import phase3_hybrid as p3
from mma_predict.regime_detector import (
    apply_regime_weight_multipliers,
    disagreement_index as fight_disagreement_index,
    detect_fight_regime,
    finish_blend,
    regime_mc_factor,
)
from mma_predict.adversarial_sim import run_adversarial_simulation
from mma_predict import bankroll as brl
from mma_predict.risk_volatility import classify_risk_advanced, compute_volatility, favorite_win_probs
from mma_predict import rl_policy as rlp
from mma_predict.simulation import monte_carlo_prob
from mma_predict.self_play import read_active_model_variant

RiskTier = Literal["SAFE", "RISKY", "SKIP"]


def _implied_two_way(decimal_red: float, decimal_blue: float) -> tuple[float, float]:
    """Remove vig simples: normaliza 1/odds."""
    ir = 1.0 / decimal_red if decimal_red > 1.0 else 0.0
    ib = 1.0 / decimal_blue if decimal_blue > 1.0 else 0.0
    s = ir + ib
    if s <= 0:
        return 0.5, 0.5
    return ir / s, ib / s


def _value_side(
    p_red_model: float,
    p_blue_model: float,
    implied_red: float,
    implied_blue: float,
    margin: float = 0.05,
) -> Optional[dict[str, Any]]:
    if p_red_model > implied_red + margin:
        return {
            "side": "red",
            "model_pct": round(100.0 * p_red_model, 2),
            "implied_pct": round(100.0 * implied_red, 2),
            "edge_pct": round(100.0 * (p_red_model - implied_red), 2),
        }
    if p_blue_model > implied_blue + margin:
        return {
            "side": "blue",
            "model_pct": round(100.0 * p_blue_model, 2),
            "implied_pct": round(100.0 * implied_blue, 2),
            "edge_pct": round(100.0 * (p_blue_model - implied_blue), 2),
        }
    return None


def classify_risk(
    p_red: float,
    *,
    favorite_corner: str,
    red: FighterProfile,
    blue: FighterProfile,
) -> dict[str, Any]:
    """
    Legado (heurísticas antigas). Mantido para testes e comparação; o fluxo principal
    usa ``classify_risk_advanced`` com Monte Carlo e volatilidade.
    """
    reasons: list[str] = []
    max_p = max(p_red, 1.0 - p_red)
    edge_away_from_coin = abs(p_red - 0.5)

    if max_p < 0.60:
        reasons.append("Probabilidade máxima < 60%")
        return {"tier": "SKIP", "reasons": reasons, "max_confidence_pct": round(100.0 * max_p, 2)}

    if edge_away_from_coin < 0.08:
        reasons.append("Confronto equilibrado (margem próxima de 50/50)")
        return {"tier": "SKIP", "reasons": reasons, "max_confidence_pct": round(100.0 * max_p, 2)}

    fav = red if favorite_corner == "red" else blue
    pen = 0.22 * (fav.history.last5_ko_losses if fav.history else 0)
    pen += 0.18 * (fav.history.last5_sub_losses if fav.history else 0)
    if pen >= 0.55:
        reasons.append("Favorito com derrotas recentes por nocaute/finalização")
        return {
            "tier": "RISKY",
            "reasons": reasons,
            "max_confidence_pct": round(100.0 * max_p, 2),
        }

    if max_p < 0.68:
        reasons.append("Confiança moderada (abaixo de ~68%)")
        return {
            "tier": "RISKY",
            "reasons": reasons,
            "max_confidence_pct": round(100.0 * max_p, 2),
        }

    return {"tier": "SAFE", "reasons": [], "max_confidence_pct": round(100.0 * max_p, 2)}


def predict_fight_advanced(
    red: FighterProfile,
    blue: FighterProfile,
    *,
    red_rank_card: Optional[int],
    blue_rank_card: Optional[int],
    odds_red_decimal: Optional[float] = None,
    odds_blue_decimal: Optional[float] = None,
    event_title: Optional[str] = None,
    fight_date: Optional[str] = None,
    red_display_name: str = "",
    blue_display_name: str = "",
    include_dataset_rows: bool = False,
    monte_carlo_simulations: int = 5000,
    ml_prob_red: Optional[float] = None,
    ml_confidence: Optional[float] = None,
    division: Optional[str] = None,
    card_avg_volatility: Optional[float] = None,
    phase7_event_url: Optional[str] = None,
    phase7_fight_index: Optional[int] = None,
    phase7_event_total_fights: Optional[int] = None,
    phase7_peer_final_probs: Optional[list[float]] = None,
    phase7_prior_event_stake_fraction: float = 0.0,
) -> dict[str, Any]:
    weights = get_matchup_weights()
    mm = run_matchup_probabilities(red, blue, weights=weights)
    p_match_red = float(mm["prob_red"])
    p_match_blue = float(mm["prob_blue"])

    wm_legacy = run_weighted_model(red, blue, red_rank_card, blue_rank_card)
    comp = build_match_components(red, blue, red_rank_card, blue_rank_card)
    legacy_edge = weighted_edge(comp)
    p_legacy_prob, _ = edge_to_prob_red(legacy_edge)

    has_ml = ml_prob_red is not None and 0.0 < float(ml_prob_red) < 1.0
    p_ml = float(ml_prob_red) if has_ml else None
    w_ml_conf = float(ml_confidence) if ml_confidence is not None else (0.55 if has_ml else 0.0)

    drift_info = detect_drift()
    drift_score = float(drift_info.get("drift_score", 0.0))

    elo_snap = read_matchup_snapshot(red.slug, blue.slug)
    elo_diff = float(elo_snap["elo_diff"])
    p_elo = float(elo_snap["elo_prob_red"])

    w_prior = max(p_match_red, 1.0 - p_match_red)
    p_bayes, w1b, w2b = bayesian_posterior_red(
        p_match_red,
        p_ml,
        w_prior=w_prior,
        w_likelihood=w_ml_conf if has_ml else 0.0,
    )

    raw = mm["raw_differentials"]
    vol = compute_volatility(
        red,
        blue,
        abs_striking_diff_lpm=abs(raw["striking_diff"]),
    )
    div = (division or "").strip()
    fb = finish_blend(red, blue)
    disag = fight_disagreement_index(p_match_red, p_bayes, p_elo, p_ml, has_ml=has_ml)
    regime = detect_fight_regime(
        vol,
        fb,
        disag,
        div,
        card_avg_volatility=card_avg_volatility,
    )
    ctx_bucket = me.context_bucket(div, red, blue, vol)
    cid = me.composite_key(ctx_bucket, regime)
    w_base = me.fetch_weights_for_prediction(cid, has_ml)
    w_ensemble = apply_regime_weight_multipliers(w_base, regime)
    final_prob = me.ensemble_prob_red(
        p_match_red, p_bayes, p_elo, p_ml, w_ensemble, has_ml=has_ml
    )

    agreement = p3.model_agreement_score(
        p_match_red,
        p_ml,
        p_bayes,
        p_elo,
        has_ml=has_ml,
    )
    uncertainty = p3.uncertainty_index(
        p_match_red,
        p_ml,
        p_bayes,
        p_elo,
        has_ml=has_ml,
        drift_score=drift_score,
        elo_diff=elo_diff,
    )

    roi_adj, dyn_value_thr, roi_err_ctx = me.read_metrics_for_context(ctx_bucket)

    ir, ib = 0.5, 0.5
    edge_max_rl = 0.0
    dec_taken: Optional[float] = None
    if (
        odds_red_decimal is not None
        and odds_blue_decimal is not None
        and odds_red_decimal > 1.0
        and odds_blue_decimal > 1.0
    ):
        ir, ib = _implied_two_way(odds_red_decimal, odds_blue_decimal)
        edge_max_rl = max(float(final_prob) - float(ir), float(1.0 - final_prob) - float(ib))
        dec_taken = float(odds_red_decimal if final_prob >= 0.5 else odds_blue_decimal)

    adv = run_adversarial_simulation(
        final_prob,
        disagreement=disag,
        volatility=vol,
        regime=regime,
        model_agreement=agreement,
    )
    try:
        active_variant = read_active_model_variant()
    except Exception:
        active_variant = "model_v4"

    has_odds_edge = edge_max_rl > 1e-4
    action, _score_hint = rlp.select_action(
        final_prob=final_prob,
        edge_max=edge_max_rl,
        confidence=max(final_prob, 1.0 - final_prob),
        agreement=agreement,
        regime=regime,
        volatility=vol,
        adversarial_risk=float(adv["stress_test_score"]),
        roi_context=ctx_bucket,
        has_odds_edge=has_odds_edge,
        vulnerability_index=float(adv["vulnerability_index"]),
    )
    stake_f = rlp.stake_fraction_kelly_like(
        action,
        edge=edge_max_rl,
        confidence=max(final_prob, 1.0 - final_prob),
        volatility=vol,
        adversarial_risk=float(adv["stress_test_score"]),
        regime=regime,
        vulnerability_index=float(adv["vulnerability_index"]),
    )
    exp_roi = rlp.expected_roi_proxy(
        edge_max_rl,
        max(final_prob, 1.0 - final_prob),
        agreement,
        float(adv["vulnerability_index"]),
        float(adv["stress_test_score"]),
    )
    pr_fac = rlp.policy_risk_factor(action)
    adv_fac = 1.0 + 0.42 * float(adv["stress_test_score"])

    noise_scale = get_monte_carlo_noise_scale()
    mc_base = p3.mc_uncertainty_multiplier(uncertainty, elo_diff, vol)
    rf = regime_mc_factor(regime, disag)
    mc_unc = mc_base * (1.0 + 0.48 * disag) * rf * (1.0 + 0.22 * roi_err_ctx)

    p_mc_red = monte_carlo_prob(
        final_prob,
        simulations=monte_carlo_simulations,
        noise_scale=noise_scale,
        uncertainty_multiplier=mc_unc,
        policy_risk_factor=pr_fac,
        adversarial_factor=adv_fac,
    )
    p_mc_blue = 1.0 - p_mc_red

    p_fav_m, _ = favorite_win_probs(final_prob)
    p_fav_mc, _ = favorite_win_probs(p_mc_red)
    vol_risk_scale = get_volatility_risk_multiplier()
    risk = classify_risk_advanced(
        p_fav_m, p_fav_mc, vol, volatility_scale=vol_risk_scale
    )

    try:
        _eg = float(brl.equity_gradient_signal_readonly())
    except Exception:
        _eg = 0.0
    edge_margin_base = float(get_value_edge_margin())
    edge_margin_adjusted = float(edge_margin_base * (1.0 + 0.14 * max(0.0, -_eg)))

    dec_port = float(dec_taken) if dec_taken is not None else 1.88
    peer_probs = list(phase7_peer_final_probs) if phase7_peer_final_probs else []
    prior_ev_stake = float(phase7_prior_event_stake_fraction or 0.0)
    try:
        p7_full = brl.build_phase7_payload(
            base_stake_rl=stake_f,
            action_rl=action,
            edge=edge_max_rl,
            confidence=max(final_prob, 1.0 - final_prob),
            volatility=vol,
            stress_test_score=float(adv["stress_test_score"]),
            vulnerability_index=float(adv["vulnerability_index"]),
            regime=regime,
            context_bucket=ctx_bucket,
            disagreement=float(disag),
            final_prob_red=float(final_prob),
            decimal_odds=dec_port,
            peer_final_probs=peer_probs,
            prior_event_stake=prior_ev_stake,
            event_url=phase7_event_url,
            fight_index=phase7_fight_index,
            event_total_fights=phase7_event_total_fights,
        )
    except Exception:
        p7_full = {
            "recommended_stake": 0.0,
            "bankroll_exposure": prior_ev_stake,
            "risk_budget_status": "ok",
            "expected_bankroll_impact": 0.0,
            "drawdown_risk": 0.0,
            "equity_gradient_signal": 0.0,
            "fractional_kelly_cap": 0.35,
            "portfolio_mc": {
                "expected_bankroll_growth": 0.0,
                "worst_case_drawdown": 0.0,
                "volatility_of_equity_curve": 0.0,
                "simulations": 0,
                "n_legs": 0,
            },
            "risk_budget_detail": {"reasons": [], "event_usage": prior_ev_stake, "policy_override": None},
            "stake_rl_pre_portfolio": float(stake_f),
        }

    phase7_bankroll: dict[str, Any] = {
        "recommended_stake": float(p7_full["recommended_stake"]),
        "bankroll_exposure": float(p7_full["bankroll_exposure"]),
        "risk_budget_status": str(p7_full["risk_budget_status"]),
        "expected_bankroll_impact": float(p7_full["expected_bankroll_impact"]),
        "drawdown_risk": float(p7_full["drawdown_risk"]),
        "equity_gradient_signal": float(p7_full.get("equity_gradient_signal", 0.0)),
        "portfolio_mc": p7_full.get("portfolio_mc"),
        "fractional_kelly_cap": p7_full.get("fractional_kelly_cap"),
        "risk_budget_detail": p7_full.get("risk_budget_detail"),
        "stake_rl_pre_portfolio": p7_full.get("stake_rl_pre_portfolio"),
    }

    value_bet: Optional[dict[str, Any]] = None
    value_bet_flag = False
    value_edge_max: Optional[float] = None
    if dec_taken is not None:
        vflag, vdet = me.value_bet_v4_decision(
            final_prob_red=final_prob,
            implied_red=ir,
            implied_blue=ib,
            agreement=agreement,
            roi_adjustment=roi_adj,
            dynamic_threshold=dyn_value_thr,
            regime=regime,
        )
        if vflag and vdet:
            value_bet = vdet
            value_bet_flag = True
            value_edge_max = max(final_prob - ir, (1.0 - final_prob) - ib)
        else:
            vb = _value_side(
                p_match_red, p_match_blue, ir, ib, margin=edge_margin_adjusted
            )
            if vb:
                vb["note"] = "Fallback heurístico (value 4.0 não satisfeito)."
                vb["edge_prob"] = round(
                    (p_match_red - ir) if vb["side"] == "red" else (p_match_blue - ib),
                    4,
                )
                vb["edge_margin_used"] = round(edge_margin_adjusted, 4)
                value_bet = vb
                value_bet_flag = True
                value_edge_max = max(
                    p_match_red - ir,
                    p_match_blue - ib,
                )

    fav_corner_wm = "red" if p_mc_red >= 0.5 else "blue"

    out: dict[str, Any] = {
        "favorite_corner_weighted": fav_corner_wm,
        "model_prob": round(p_match_red, 4),
        "monte_carlo_prob": round(p_mc_red, 4),
        "volatility": round(vol, 4),
        "value_bet": value_bet_flag,
        "value_edge_max": round(float(value_edge_max), 4) if value_edge_max is not None else None,
        "weighted_model": {
            "prob_red_pct": round(100.0 * p_mc_red, 2),
            "prob_blue_pct": round(100.0 * p_mc_blue, 2),
            "edge_score": round(float(mm["matchup_linear_score"]), 4),
            "legacy_edge_score": round(legacy_edge, 4),
            "components": wm_legacy["components"],
            "weights": wm_legacy["weights"],
            "matchup_raw": mm["raw_differentials"],
            "matchup_weights_used": mm.get("weights_used"),
            "feature_terms_vector": mm.get("feature_terms_vector"),
            "model_prob_red_pre_mc": round(p_match_red, 4),
            "final_prob_red_pre_mc": round(final_prob, 4),
            "monte_carlo_prob_red": round(p_mc_red, 4),
            "monte_carlo_noise_scale": round(noise_scale, 4),
            "monte_carlo_uncertainty_mult": round(mc_unc, 4),
            "monte_carlo_policy_risk_factor": round(float(pr_fac), 4),
            "monte_carlo_adversarial_factor": round(float(adv_fac), 4),
        },
        "phase3_model": {
            "elo_rating_diff": round(elo_diff, 2),
            "elo_prob": round(p_elo, 5),
            "bayesian_prob": round(p_bayes, 5),
            "final_prob": round(final_prob, 5),
            "model_agreement": round(agreement, 4),
            "uncertainty": round(uncertainty, 4),
            "ml_prob_red": round(float(p_ml), 5) if p_ml is not None else None,
            "legacy_linear_prob": round(float(p_legacy_prob), 5),
            "bayesian_weights": {"w_prior": round(w1b, 4), "w_ml": round(w2b, 4)},
        },
        "phase4_model": {
            "ensemble_weights": {
                "heuristic": round(float(w_ensemble["heuristic"]), 4),
                "bayesian": round(float(w_ensemble["bayesian"]), 4),
                "elo": round(float(w_ensemble["elo"]), 4),
                "ml": round(float(w_ensemble["ml"]), 4),
            },
            "regime": regime,
            "roi_context": ctx_bucket,
            "value_score_dynamic_threshold": round(float(dyn_value_thr), 5),
            "finish_blend": round(float(fb), 4),
            "disagreement_index": round(float(disag), 4),
            "meta_composite_key": cid,
            "roi_adjustment": round(float(roi_adj), 4),
        },
        "phase5_policy": {
            "action": action,
            "expected_roi": round(float(exp_roi), 5),
            "adversarial_risk": round(float(adv["vulnerability_index"]), 4),
            "stake_fraction": round(float(stake_f), 5),
            "stress_test_score": round(float(adv["stress_test_score"]), 4),
            "active_model_variant": str(active_variant),
            "edge_max": round(float(edge_max_rl), 5),
            "roi_context": ctx_bucket,
            "decimal_odds_taken": round(float(dec_taken), 4) if dec_taken is not None else None,
            "bet_side": "red" if final_prob >= 0.5 else "blue",
            "worst_case_roi": float(adv["worst_case_roi"]),
        },
        "phase6_adversarial": {
            "adversarial_hit_rate": float(adv["adversarial_hit_rate"]),
            "worst_case_roi": float(adv["worst_case_roi"]),
            "stress_test_score": float(adv["stress_test_score"]),
            "vulnerability_index": float(adv["vulnerability_index"]),
            "simulations": int(adv["simulations"]),
        },
        "phase7_bankroll": phase7_bankroll,
        "risk": risk,
        "value_bet_detail": value_bet,
        "risk_legacy": classify_risk(
            p_mc_red,
            favorite_corner=fav_corner_wm,
            red=red,
            blue=blue,
        ),
        "note": (
            "model_prob = heurística matchup (inalterada para compatibilidade). "
            "phase3_model.final_prob = ensemble meta-aprendido (fase 4: pesos por contexto+regime+ROI). "
            "monte_carlo_prob = MC sobre final_prob com ruído (discrepância, regime, erro ROI no contexto). "
            "Fase 5: política RL leve (ação + stake). Fase 6: stress adversarial. MC usa fatores de política e adversário. "
            "Fase 7: bankroll + orçamentos de risco (stake recomendado pode ser < RL); curva de equity alimenta margem de value e recompensa RL. "
            "Value bet 4.0: ROI-aware; bloqueado em chaotic_card. Snapshots phase4/phase5 gravados pelo cliente."
        ),
    }
    if include_dataset_rows and red_display_name and blue_display_name:
        rr, br = fight_pair_rows(
            red,
            blue,
            red_name=red_display_name,
            blue_name=blue_display_name,
            event_title=event_title,
            fight_date=fight_date,
        )
        out["dataset_rows"] = {"red": rr, "blue": br}
    return out


def edge_only(red: FighterProfile, blue: FighterProfile, red_rc: Optional[int], blue_rc: Optional[int]) -> float:
    """Utilitário para testes / calibração (legado)."""
    return weighted_edge(build_match_components(red, blue, red_rc, blue_rc))
