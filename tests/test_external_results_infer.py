"""Heurísticas de vencedor a partir de títulos (imprensa)."""

from ufc_external_context import Mention, infer_winner_for_pair_from_mentions


def test_defeats_red_wins():
    m = [
        Mention(
            "https://x.com/a",
            "Smith defeats Jones at UFC 999",
            "X",
            "x",
        )
    ]
    out = infer_winner_for_pair_from_mentions(m, "John Smith", "Mike Jones")
    assert out is not None and out[0] == "red"


def test_vence_red_wins_pt():
    m = [
        Mention(
            "https://news.google.com/a",
            "Silva vence Santos na luta principal",
            "Google News",
            "google_news",
        )
    ]
    out = infer_winner_for_pair_from_mentions(m, "Anderson Silva", "Thiago Santos")
    assert out is not None and out[0] == "red"


def test_freire_over_pico_english():
    m = [
        Mention(
            "https://example.com/x",
            "Freire earns gritty decision over Pico at UFC 999",
            "Google News (EN)",
            "en",
        )
    ]
    out = infer_winner_for_pair_from_mentions(
        m,
        "Patricio Pitbull",
        "Aaron Pico",
        red_slug="patricio-pitbull-freire",
        blue_slug="aaron-pico",
    )
    assert out is not None and out[0] == "red"


def test_pt_nocauteou():
    m = [
        Mention(
            "https://br.example/x",
            "UFC 999: Freire nocauteou Pico no primeiro round",
            "Google News",
            "br",
        )
    ]
    out = infer_winner_for_pair_from_mentions(
        m,
        "Patricio Pitbull",
        "Aaron Pico",
        red_slug="patricio-pitbull-freire",
        blue_slug="aaron-pico",
    )
    assert out is not None and out[0] == "red"


def test_freire_in_title_when_display_is_pitbull():
    """Imprensa usa sobrenome (Freire); card mostra apelido (Pitbull)."""
    m = [
        Mention(
            "https://mmajunkie.com/x",
            "Freire defeats Pico via decision at UFC 999",
            "RSS",
            "mmajunkie",
        )
    ]
    out = infer_winner_for_pair_from_mentions(
        m,
        "Patricio Pitbull",
        "Aaron Pico",
        red_slug="patricio-pitbull-freire",
        blue_slug="aaron-pico",
    )
    assert out is not None and out[0] == "red"


def test_loses_to_blue_wins():
    m = [
        Mention(
            "https://reddit.com/r/MMA/x",
            "Ngannou loses to Gane in decision shocker",
            "Reddit r/MMA",
            "reddit",
        )
    ]
    out = infer_winner_for_pair_from_mentions(m, "Francis Ngannou", "Ciryl Gane")
    assert out is not None and out[0] == "blue"
