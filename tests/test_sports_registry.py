"""Registro de esportes e campo `sport` na resposta."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sports import get_analyzer, list_sport_ids
from sports.protocol import EventAnalyzer
from sports.registry import register_analyzer


class _DummyPlugin(EventAnalyzer):
    @property
    def sport_id(self) -> str:
        return "plugin_test"

    def validate_event_url(self, url: str) -> bool:
        return url.startswith("https://example.test/")

    def analyze(self, event_url: str, *, cache_dir=None, cache_hours=24.0, refresh=False):
        return {"ok": True, "event_url": event_url, "matches": []}


class TestRegistry(unittest.TestCase):
    def tearDown(self) -> None:
        import sports.registry as reg

        reg._ANALYZERS.pop("plugin_test", None)

    def test_lists_ufc(self) -> None:
        ids = list_sport_ids()
        self.assertIn("ufc", ids)
        self.assertEqual(ids, sorted(ids))

    def test_unknown_sport_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_analyzer("curling")

    def test_register_and_retrieve(self) -> None:
        register_analyzer("plugin_test", _DummyPlugin())
        self.assertIn("plugin_test", list_sport_ids())
        a = get_analyzer("plugin_test")
        self.assertEqual(a.sport_id, "plugin_test")
        self.assertTrue(a.validate_event_url("https://example.test/match/1"))


class TestUfcAnalyzerAddsSport(unittest.TestCase):
    @patch("sports.ufc_analyzer.analyze_event_json")
    def test_response_includes_sport_key(self, mock_analyze: object) -> None:
        mock_analyze.return_value = {
            "ok": True,
            "event_url": "https://www.ufc.com.br/event/x",
            "event_title": "Test",
            "fights": [],
            "errors": [],
        }
        a = get_analyzer("ufc")
        out = a.analyze(
            "https://www.ufc.com.br/event/x",
            cache_dir=None,
            cache_hours=24.0,
            refresh=False,
        )
        self.assertIn("sport", out)
        self.assertEqual(out["sport"], "ufc")
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
