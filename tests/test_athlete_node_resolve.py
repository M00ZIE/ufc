"""Resolução de links Drupal /node/N para slug /athlete/...."""

from unittest.mock import patch

import requests

from ufc_event_analysis import parse_fight_card, resolve_athlete_href_to_slug


class _FakeResp:
    def __init__(self, final_url: str, html: str = "") -> None:
        self.url = final_url
        self.text = html

    def raise_for_status(self) -> None:
        pass


@patch("ufc_event_analysis._session_get_resilient")
def test_resolve_node_redirects_to_athlete(mock_resilient) -> None:
    mock_resilient.return_value = _FakeResp("https://www.ufc.com.br/athlete/chris-weidman")
    s = requests.Session()
    slug = resolve_athlete_href_to_slug("https://www.ufc.com.br/node/149875", s, base="https://www.ufc.com.br")
    assert slug == "chris-weidman"
    mock_resilient.assert_called_once()


@patch("ufc_event_analysis._session_get_resilient")
def test_resolve_node_synthetic_when_profile_on_node_url(mock_resilient) -> None:
    """Canonical continua em /node/N mas o HTML é página de perfil."""
    mock_resilient.return_value = _FakeResp(
        "https://www.ufc.com.br/node/149875",
        "<html><head><link rel='canonical' href='https://www.ufc.com.br/node/149875'/></head>"
        "<body><h1 class='hero-profile__name'>John Yannis</h1><div class='hero-profile'>x</div></body></html>",
    )
    s = requests.Session()
    slug = resolve_athlete_href_to_slug("/node/149875", s, base="https://www.ufc.com.br")
    assert slug == "node-149875"


@patch("ufc_event_analysis._session_get_resilient")
def test_resolve_node_via_canonical(mock_resilient) -> None:
    mock_resilient.return_value = _FakeResp(
        "https://www.ufc.com.br/node/149875",
        '<link rel="canonical" href="https://www.ufc.com.br/athlete/gilbert-burns" />',
    )
    s = requests.Session()
    slug = resolve_athlete_href_to_slug("/node/149875", s, base="https://www.ufc.com.br")
    assert slug == "gilbert-burns"


@patch("ufc_event_analysis._session_get_resilient")
def test_parse_fight_card_node_links(mock_resilient) -> None:
    mock_resilient.side_effect = [
        _FakeResp("https://www.ufc.com.br/athlete/red-slug"),
        _FakeResp("https://www.ufc.com.br/athlete/blue-slug"),
    ]
    html = """
    <div class="c-listing-fight" data-fmid="99" data-status="">
      <div class="c-listing-fight__class--mobile"><span class="c-listing-fight__class-text">WW</span></div>
      <div class="c-listing-fight__corner-name--red">
        <a href="/node/111">Red Name</a>
      </div>
      <div class="c-listing-fight__corner-name--blue">
        <a href="/node/222">Blue Name</a>
      </div>
    </div>
    """
    s = requests.Session()
    rows = parse_fight_card(html, session=s, base="https://www.ufc.com.br")
    assert len(rows) == 1
    assert rows[0].red_slug == "red-slug"
    assert rows[0].blue_slug == "blue-slug"
