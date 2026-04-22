"""Testes do allowlist de URLs (evento e proxy de imagem)."""

from __future__ import annotations

import unittest

from sports.ufc_urls import allowed_ufc_event_url, allowed_ufc_image_url, allowed_ufc_hostname


class TestAllowedUfcHostname(unittest.TestCase):
    def test_apex_and_www(self) -> None:
        self.assertTrue(allowed_ufc_hostname("ufc.com"))
        self.assertTrue(allowed_ufc_hostname("www.ufc.com"))
        self.assertTrue(allowed_ufc_hostname("ufc.com.br"))
        self.assertTrue(allowed_ufc_hostname("www.ufc.com.br"))

    def test_subdomains(self) -> None:
        self.assertTrue(allowed_ufc_hostname("cdn.ufc.com"))
        self.assertTrue(allowed_ufc_hostname("images.ufc.com.br"))

    def test_rejects_typosquat(self) -> None:
        self.assertFalse(allowed_ufc_hostname("evilufc.com"))
        self.assertFalse(allowed_ufc_hostname("notufc.com.br"))
        self.assertFalse(allowed_ufc_hostname("example.com"))

    def test_empty(self) -> None:
        self.assertFalse(allowed_ufc_hostname(""))


class TestAllowedEventUrl(unittest.TestCase):
    def test_accepts_official_event_pages(self) -> None:
        self.assertTrue(
            allowed_ufc_event_url("https://www.ufc.com.br/event/ufc-300"),
        )
        self.assertTrue(allowed_ufc_event_url("https://ufc.com/event/foo"))
        self.assertTrue(allowed_ufc_event_url("https://www.ufc.com/event/foo"))

    def test_rejects_typosquat(self) -> None:
        self.assertFalse(allowed_ufc_event_url("https://evilufc.com/event/x"))
        self.assertFalse(allowed_ufc_event_url("https://fake-ufc.com/event/x"))

    def test_rejects_non_http(self) -> None:
        self.assertFalse(allowed_ufc_event_url("ftp://ufc.com/event/x"))
        self.assertFalse(allowed_ufc_event_url(""))


class TestAllowedImageUrl(unittest.TestCase):
    def test_subdomain_cdn(self) -> None:
        self.assertTrue(
            allowed_ufc_image_url("https://cdn.ufc.com/images/x.png"),
        )

    def test_same_rules_as_event_host(self) -> None:
        self.assertFalse(allowed_ufc_image_url("https://evilufc.com/img.png"))


if __name__ == "__main__":
    unittest.main()
