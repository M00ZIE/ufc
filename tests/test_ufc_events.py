"""Testes: datas em URLs de eventos e seleção do próximo evento futuro."""

from __future__ import annotations

import time
import unittest
from datetime import date
from unittest.mock import patch

import requests

import ufc_events
from ufc_events import (
    _drop_past_events_by_hero_time,
    list_future_events_ordered,
    parse_date_from_event_url,
    select_next_future_event,
)


class TestParseDateFromEventUrl(unittest.TestCase):
    def test_parses_slug_with_trailing_slash(self) -> None:
        u = "https://www.ufc.com.br/event/ufc-fight-night-march-28-2026/"
        self.assertEqual(parse_date_from_event_url(u), date(2026, 3, 28))

    def test_parses_slug_without_trailing_slash(self) -> None:
        u = "https://www.ufc.com.br/event/ufc-fight-night-march-28-2026"
        self.assertEqual(parse_date_from_event_url(u), date(2026, 3, 28))

    def test_no_date_in_numeric_only_slug(self) -> None:
        u = "https://www.ufc.com.br/event/ufc-327"
        self.assertIsNone(parse_date_from_event_url(u))


class TestListFutureEventsOrdered(unittest.TestCase):
    def test_excludes_all_past_dated(self) -> None:
        today = date(2026, 6, 1)
        events = [
            {"title": "Old", "url": "https://www.ufc.com.br/event/x-january-10-2026"},
            {"title": "Also old", "url": "https://www.ufc.com.br/event/y-march-15-2026"},
        ]
        self.assertEqual(list_future_events_ordered(events, today=today), [])

    def test_includes_yesterday_dated_slug(self) -> None:
        """Após meia-noite o slug com data de ontem ainda entra (noite de lutas)."""
        today = date(2026, 4, 12)
        events = [
            {"title": "Ontem", "url": "https://www.ufc.com.br/event/x-april-11-2026"},
            {"title": "Velho", "url": "https://www.ufc.com.br/event/y-march-01-2026"},
        ]
        out = list_future_events_ordered(events, today=today)
        self.assertEqual(len(out), 1)
        self.assertIn("april-11", out[0]["url"])

    def test_orders_dated_future_before_undated(self) -> None:
        today = date(2026, 3, 1)
        events = [
            {"title": "No slug date", "url": "https://www.ufc.com.br/event/ufc-327"},
            {"title": "April", "url": "https://www.ufc.com.br/event/x-april-15-2026"},
            {"title": "Past", "url": "https://www.ufc.com.br/event/y-january-10-2026"},
        ]
        out = list_future_events_ordered(events, today=today)
        urls = [e["url"] for e in out]
        self.assertEqual(len(out), 2)
        self.assertIn("april-15", urls[0])
        self.assertIn("ufc-327", urls[1])


class TestSelectNextFutureEvent(unittest.TestCase):
    def test_picks_earliest_future_by_date(self) -> None:
        today = date(2026, 3, 1)
        events = [
            {"title": "Past", "url": "https://www.ufc.com.br/event/x-january-15-2026"},
            {"title": "Soon", "url": "https://www.ufc.com.br/event/y-march-15-2026"},
            {"title": "Later", "url": "https://www.ufc.com.br/event/z-april-01-2026"},
        ]
        nxt = select_next_future_event(events, today=today)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertIn("march-15", nxt["url"])

    def test_undated_event_is_candidate(self) -> None:
        today = date(2026, 3, 1)
        events = [
            {"title": "No date", "url": "https://www.ufc.com.br/event/ufc-300"},
        ]
        nxt = select_next_future_event(events, today=today)
        self.assertEqual(nxt, events[0])


class TestDropPastEventsByHeroTime(unittest.TestCase):
    def test_drops_undated_when_hero_timestamp_in_past(self) -> None:
        past_ts = int(time.time()) - 86400
        html = f'<div class="c-hero__headline-suffix" data-timestamp="{past_ts}"></div>'
        session = requests.Session()
        with patch("ufc_event_analysis.fetch_html", return_value=html):
            out = _drop_past_events_by_hero_time(
                [{"title": "UFC 326", "url": "https://www.ufc.com.br/event/ufc-326"}],
                session,
                cache_dir=None,
                ttl=None,
            )
        self.assertEqual(out, [])

    def test_keeps_undated_when_hero_timestamp_future(self) -> None:
        future_ts = int(time.time()) + 86400 * 7
        html = f'<div class="c-hero__headline-suffix" data-timestamp="{future_ts}"></div>'
        ev = {"title": "UFC 999", "url": "https://www.ufc.com.br/event/ufc-999"}
        session = requests.Session()
        with patch("ufc_event_analysis.fetch_html", return_value=html):
            out = _drop_past_events_by_hero_time([ev], session, cache_dir=None, ttl=None)
        self.assertEqual(out, [ev])

    def test_keeps_when_hero_day_was_yesterday_within_grace(self) -> None:
        """Meia-noite já passou mas o PPV começou ontem e ainda está na janela de ~22h."""
        fixed_today = date(2026, 4, 12)
        hero_ts = int(time.mktime((2026, 4, 11, 22, 0, 0, 0, 0, -1)))
        now_after_midnight = time.mktime((2026, 4, 12, 3, 0, 0, 0, 0, -1))
        html = f'<div class="c-hero__headline-suffix" data-timestamp="{hero_ts}"></div>'
        ev = {"title": "UFC", "url": "https://www.ufc.com.br/event/ufc-327"}
        session = requests.Session()

        class _DateStub:
            @staticmethod
            def today() -> date:
                return fixed_today

            fromtimestamp = date.fromtimestamp

        with patch("ufc_event_analysis.fetch_html", return_value=html):
            with patch("ufc_events.time.time", return_value=now_after_midnight):
                with patch.object(ufc_events, "date", _DateStub):
                    out = _drop_past_events_by_hero_time([ev], session, cache_dir=None, ttl=None)
        self.assertEqual(out, [ev])

    def test_keeps_undated_when_main_card_started_but_same_calendar_day(self) -> None:
        """Após o horário do hero, o evento de hoje não some do carrossel."""
        fixed_today = date(2026, 4, 11)
        hero_ts = int(time.mktime((2026, 4, 11, 14, 0, 0, 0, 0, -1)))
        now_late = time.mktime((2026, 4, 11, 23, 0, 0, 0, 0, -1))
        html = f'<div class="c-hero__headline-suffix" data-timestamp="{hero_ts}"></div>'
        ev = {"title": "UFC Hoje", "url": "https://www.ufc.com.br/event/ufc-327"}
        session = requests.Session()

        class _DateStub:
            @staticmethod
            def today() -> date:
                return fixed_today

            fromtimestamp = date.fromtimestamp

        with patch("ufc_event_analysis.fetch_html", return_value=html):
            with patch("ufc_events.time.time", return_value=now_late):
                with patch.object(ufc_events, "date", _DateStub):
                    out = _drop_past_events_by_hero_time([ev], session, cache_dir=None, ttl=None)
        self.assertEqual(out, [ev])

    def test_keeps_on_fetch_error(self) -> None:
        ev = {"title": "X", "url": "https://www.ufc.com.br/event/ufc-326"}
        session = requests.Session()
        with patch("ufc_event_analysis.fetch_html", side_effect=OSError("network")):
            out = _drop_past_events_by_hero_time([ev], session, cache_dir=None, ttl=None)
        self.assertEqual(out, [ev])


if __name__ == "__main__":
    unittest.main()
