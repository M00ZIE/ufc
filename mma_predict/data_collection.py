"""
Adaptação de dados: perfis já obtidos do site UFC → linhas tabulares para dataset futuro.

Não duplica scraping; use com FighterProfile do ufc_event_analysis.
"""

from __future__ import annotations

from typing import Any, Optional

from ufc_event_analysis import FighterProfile


def profile_to_row(
    fp: FighterProfile,
    *,
    corner: str,
    opponent_name: str,
    event_title: Optional[str] = None,
    fight_date: Optional[str] = None,
) -> dict[str, Any]:
    """Uma linha descritiva do lutador (para CSV/Parquet). Resultado da luta não incluído."""
    h = fp.history
    return {
        "corner": corner,
        "name": fp.name,
        "slug": fp.slug,
        "opponent": opponent_name,
        "event_title": event_title,
        "fight_date": fight_date,
        "record_wld": f"{fp.wins}-{fp.losses}-{fp.draws}",
        "ufc_rank_profile": fp.ufc_rank,
        "sig_str_lpm": fp.sig_str_lpm,
        "sig_str_abs_lpm": fp.sig_str_abs_lpm,
        "str_def_pct": fp.str_def_pct,
        "td_avg": fp.td_avg,
        "sub_per_15": fp.sub_per_15,
        "td_def_pct": fp.td_def_pct,
        "kd_avg": fp.kd_avg,
        "avg_fight_minutes": fp.avg_fight_minutes,
        "ko_wins": fp.ko_wins,
        "dec_wins": fp.dec_wins,
        "sub_wins": fp.sub_wins,
        "first_round_finishes": fp.first_round_finishes,
        "last5_sequence": h.sequence if h else None,
        "last5_w": h.last5_w if h else None,
        "last5_l": h.last5_l if h else None,
        "last5_ko_losses": h.last5_ko_losses if h else None,
        "last5_sub_losses": h.last5_sub_losses if h else None,
    }


def fight_pair_rows(
    red: FighterProfile,
    blue: FighterProfile,
    *,
    red_name: str,
    blue_name: str,
    event_title: Optional[str] = None,
    fight_date: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        profile_to_row(
            red,
            corner="red",
            opponent_name=blue_name,
            event_title=event_title,
            fight_date=fight_date,
        ),
        profile_to_row(
            blue,
            corner="blue",
            opponent_name=red_name,
            event_title=event_title,
            fight_date=fight_date,
        ),
    )
