"""Testes do parser do card (HTML fixture)."""

from __future__ import annotations

import unittest
from pathlib import Path

from ufc_event_analysis import parse_fight_card

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "minimal_event_card.html"


class TestParseFightCard(unittest.TestCase):
    def setUp(self) -> None:
        self.html = _FIXTURE.read_text(encoding="utf-8")

    def test_parses_one_fight_names_slugs_division(self) -> None:
        fights = parse_fight_card(self.html)
        self.assertEqual(len(fights), 1)
        fr = fights[0]
        self.assertEqual(fr.fmid, "90001")
        self.assertEqual(fr.division, "Peso-médio")
        self.assertEqual(fr.red_name, "Jon Jones")
        self.assertEqual(fr.blue_name, "Stipe Miocic")
        self.assertEqual(fr.red_slug, "jon-jones")
        self.assertEqual(fr.blue_slug, "stipe-miocic")
        self.assertEqual(fr.red_rank_card, 5)
        self.assertEqual(fr.blue_rank_card, 3)

    def test_photos_absolute_url(self) -> None:
        fights = parse_fight_card(self.html)
        fr = fights[0]
        self.assertEqual(fr.red_photo_url, "https://ufc.com/images/red.png?itok=abc")
        self.assertEqual(fr.blue_photo_url, "https://cdn.ufc.com/images/blue.png")

    def test_empty_html_returns_empty(self) -> None:
        self.assertEqual(parse_fight_card("<html></html>"), [])


if __name__ == "__main__":
    unittest.main()
