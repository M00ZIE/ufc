"""JSON:API → enriquecimento dos resultados do card."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import ufc_event_analysis as uea
from ufc_event_analysis import (
    drupal_event_nid_from_html,
    enrich_fight_rows_from_jsonapi,
    parse_fight_card,
    parse_jsonapi_fights_attrs_by_fmid,
)

_FIXTURE_HTML = Path(__file__).resolve().parent / "fixtures" / "minimal_event_card.html"
_FIXTURE_JSON = Path(__file__).resolve().parent / "fixtures" / "minimal_jsonapi_event.json"


class TestEventResultsJsonApi(unittest.TestCase):
    def test_parse_index_by_fmid(self) -> None:
        doc = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
        idx = parse_jsonapi_fights_attrs_by_fmid(doc)
        self.assertIn("90001", idx)
        self.assertEqual(idx["90001"].get("fight_final_method"), "KO/TKO - Punches")

    def test_enrich_merges_outcome_and_method(self) -> None:
        html = _FIXTURE_HTML.read_text(encoding="utf-8")
        rows = parse_fight_card(html)
        doc = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
        out = enrich_fight_rows_from_jsonapi(rows, doc)
        self.assertEqual(len(out), 1)
        fr = out[0]
        self.assertEqual(fr.result_winner_side, "red")
        self.assertIn("KO", fr.result_method_text or "")
        self.assertEqual(fr.result_round, "2")
        self.assertEqual(fr.result_time, "3:21")

    def test_drupal_nid_from_settings_block(self) -> None:
        html = '<script type="application/json" data-drupal-selector="drupal-settings-json">{"path":{"currentPath":"node/154571"}}</script>'
        self.assertEqual(drupal_event_nid_from_html(html), 154571)

    def test_winner_side_from_fight_final_winner_relationship(self) -> None:
        """Quando Win/Loss no JSON ainda não veio, fight_final_winner define o canto."""
        fight = {
            "relationships": {
                "fight_final_winner": {"data": {"type": "node--athlete", "id": "uuid-blue"}},
                "red_corner": {"data": {"type": "node--athlete", "id": "uuid-red"}},
                "blue_corner": {"data": {"type": "node--athlete", "id": "uuid-blue"}},
            }
        }
        self.assertEqual(uea._jsonapi_winner_side_from_relationships(fight), "blue")
        self.assertEqual(
            uea._jsonapi_winner_side_from_relationships(
                {
                    "relationships": {
                        "fight_final_winner": {"data": {"id": "uuid-red"}},
                        "red_corner": {"data": {"id": "uuid-red"}},
                        "blue_corner": {"data": {"id": "uuid-blue"}},
                    }
                }
            ),
            "red",
        )


if __name__ == "__main__":
    unittest.main()
