"""Metadados da página de evento (hero / og)."""

from __future__ import annotations

import unittest

from ufc_event_analysis import extract_event_hero_timestamp_unix, extract_event_page_meta

# ufc.com.br: horário em data-timestamp (hero), sem JSON-LD startDate
_SNIPPET_BR_HERO_TS = """
<html><head><meta property="og:title" content="UFC 327 | UFC" /></head>
<body>
<div class="c-hero__headline-suffix tz-change-inner"
     data-locale="pt-br"
     data-timestamp="1775955600"
     data-format="d.m.y / H:i T">
  11.04.26 / 22:00 -03
</div>
</body></html>
"""

_SNIPPET_NO_OG = """
<html><head>
<meta property="og:title" content="UFC 327 | UFC" />
</head><body>
<picture>
<source srcset="https://ufc.com/images/styles/background_image_xl/s3/2026-03/x-EVENT-ART.jpg?h=1&amp;itok=ab 1x" />
<img src="https://ufc.com/images/styles/background_image_sm/s3/2026-03/x-EVENT-ART.jpg?h=1&amp;itok=cd" alt="x" />
</picture>
</body></html>
"""


class TestEventPageMeta(unittest.TestCase):
    def test_extract_event_hero_timestamp_unix(self) -> None:
        self.assertEqual(extract_event_hero_timestamp_unix(_SNIPPET_BR_HERO_TS), 1775955600)

    def test_ufc_com_br_hero_data_timestamp(self) -> None:
        m = extract_event_page_meta(_SNIPPET_BR_HERO_TS)
        self.assertEqual(m.get("event_starts_at"), "2026-04-12T01:00:00Z")

    def test_event_art_when_og_image_missing(self) -> None:
        m = extract_event_page_meta(_SNIPPET_NO_OG)
        self.assertIsNotNone(m.get("hero_image_url"))
        assert m["hero_image_url"] is not None
        self.assertIn("EVENT-ART", m["hero_image_url"])
        self.assertIn("background_image_xl", m["hero_image_url"])
        self.assertNotIn("&amp;", m["hero_image_url"])
        self.assertEqual(m.get("og_title"), "UFC 327 | UFC")

    def test_og_image_wins_over_event_art(self) -> None:
        html = """
        <meta property="og:image" content="https://ufc.com/og-only.png" />
        <img src="https://ufc.com/images/styles/background_image_xl/s3/a-EVENT-ART.jpg" />
        """
        m = extract_event_page_meta(html)
        self.assertEqual(m.get("hero_image_url"), "https://ufc.com/og-only.png")


if __name__ == "__main__":
    unittest.main()
