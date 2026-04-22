"""
Features por confronto: diferenças (mandante vermelho − azul) normalizadas ~[−1, 1].
Fonte única: estatísticas já extraídas do UFC (evita duplicar Sherdog/UFC Stats).
"""

from __future__ import annotations

from typing import Any, Optional

from ufc_event_analysis import FighterProfile, rank_score, win_rate


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def striking_diff(red: FighterProfile, blue: FighterProfile) -> float:
    rl = red.sig_str_lpm if red.sig_str_lpm is not None else 4.0
    bl = blue.sig_str_lpm if blue.sig_str_lpm is not None else 4.0
    if red.sig_str_lpm is None and blue.sig_str_lpm is None:
        d1 = 0.0
    else:
        d1 = _clip((rl - bl) / 2.5)
    rd = red.str_def_pct if red.str_def_pct is not None else 50.0
    bd = blue.str_def_pct if blue.str_def_pct is not None else 50.0
    if red.str_def_pct is None and blue.str_def_pct is None:
        d2 = 0.0
    else:
        d2 = _clip((rd - bd) / 30.0)
    return _clip(0.65 * d1 + 0.35 * d2)


def grappling_diff(red: FighterProfile, blue: FighterProfile) -> float:
    td_r = red.td_avg if red.td_avg is not None else 0.0
    td_b = blue.td_avg if blue.td_avg is not None else 0.0
    su_r = red.sub_per_15 if red.sub_per_15 is not None else 0.0
    su_b = blue.sub_per_15 if blue.sub_per_15 is not None else 0.0
    tdd_r = red.td_def_pct if red.td_def_pct is not None else 50.0
    tdd_b = blue.td_def_pct if blue.td_def_pct is not None else 50.0
    d_td = _clip((td_r - td_b) / 2.0)
    d_su = _clip((su_r - su_b) / 0.85)
    d_tdd = _clip((tdd_r - tdd_b) / 25.0)
    return _clip((d_td + d_su + d_tdd) / 3.0)


def recent_form_diff(red: FighterProfile, blue: FighterProfile) -> float:
    wrd = win_rate(red.wins, red.losses, red.draws) - win_rate(
        blue.wins, blue.losses, blue.draws
    )
    d_wr = _clip(wrd * 2.8)
    d5 = 0.0
    if red.history and blue.history:
        d5 = 0.2 * (
            (red.history.last5_w - red.history.last5_l)
            - (blue.history.last5_w - blue.history.last5_l)
        )
        d5 = _clip(d5)
    elif red.history:
        d5 = _clip(0.2 * (red.history.last5_w - red.history.last5_l))
    elif blue.history:
        d5 = _clip(-0.2 * (blue.history.last5_w - blue.history.last5_l))
    return _clip(0.55 * d_wr + 0.45 * d5)


def physical_rank_diff(
    red: FighterProfile,
    blue: FighterProfile,
    red_rank_card: Optional[int],
    blue_rank_card: Optional[int],
) -> float:
    ra = rank_score(red_rank_card, red.ufc_rank)
    rb = rank_score(blue_rank_card, blue.ufc_rank)
    return _clip((rb - ra) / 12.0)


def _inconsistency_penalty(fp: FighterProfile) -> float:
    """Maior = mais ‘volátil’ ou vulnerável nos últimos combates."""
    p = 0.0
    if fp.history:
        p += 0.22 * min(3, fp.history.last5_ko_losses)
        p += 0.18 * min(3, fp.history.last5_sub_losses)
    total = fp.wins + fp.losses + fp.draws
    if total >= 6:
        lr = fp.losses / total
        if lr > 0.42:
            p += 0.2 * _clip((lr - 0.42) / 0.35)
    return float(min(1.0, max(0.0, p)))


def consistency_diff(red: FighterProfile, blue: FighterProfile) -> float:
    """Positivo favorece o vermelho se o azul for mais ‘instável’."""
    pr = _inconsistency_penalty(red)
    pb = _inconsistency_penalty(blue)
    return _clip((pb - pr) / 1.2)


def build_match_components(
    red: FighterProfile,
    blue: FighterProfile,
    red_rank_card: Optional[int],
    blue_rank_card: Optional[int],
) -> dict[str, float]:
    return {
        "striking": striking_diff(red, blue),
        "grappling": grappling_diff(red, blue),
        "recent_form": recent_form_diff(red, blue),
        "physical": physical_rank_diff(red, blue, red_rank_card, blue_rank_card),
        "consistency": consistency_diff(red, blue),
    }


def components_to_public_dict(comp: dict[str, float]) -> dict[str, Any]:
    """Nomes amigáveis para JSON (mesmos pesos do modelo)."""
    return {
        "striking_diff": round(comp["striking"], 4),
        "grappling_diff": round(comp["grappling"], 4),
        "recent_form_diff": round(comp["recent_form"], 4),
        "physical_rank_diff": round(comp["physical"], 4),
        "consistency_diff": round(comp["consistency"], 4),
    }
