"""Overview summary cards, rankings and previews (UCL Strength Lab)."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

# (id, name, crest)
ALPHA = (5, "Alpha Strength FC", "https://crest.example/str.png")
GAMMA = (1, "Gamma Pinnacle", None)
DELTA = (2, "Delta Rovers", "https://crest.example/del.png")
EPSILON = (3, "Epsilon City", "https://crest.example/eps.png")
BETA = (8, "Beta Schedule FC", "https://crest.example/sch.png")
ZETA = (4, "Zeta Wanderers", "https://crest.example/zet.png")
ETA = (6, "Eta Sporting", "https://crest.example/eta.png")
IOTA = (7, "Iota Athletic", "https://crest.example/iot.png")


def _pair_with(build_pair, match_id, team1, team2, prob, **kwargs):
    pair = build_pair(match_id, team1, team2, **kwargs)
    if prob is not None:
        pair["winProbability"] = prob
    else:
        pair.pop("winProbability", None)
    return pair


@pytest.fixture
def knockout_fixture(api, build_pair, build_analysis):
    """Four Round-of-16 ties with contrasting probability quality."""
    pairs = [
        _pair_with(build_pair, 1, ALPHA, GAMMA,
                   {"team1Win": 0.70, "team2Win": 0.30, "draw": 0.0, "method": "elo",
                    "team1EloRating": 1850, "team2EloRating": 1700,
                    "team1MatchesPlayed": 40, "team2MatchesPlayed": 40}),
        _pair_with(build_pair, 2, DELTA, EPSILON,
                   {"team1Win": 0.52, "team2Win": 0.48, "draw": 0.0,
                    "method": "combined_strength", "team1MainStrength": 1.0,
                    "team2MainStrength": 0.9, "team1HistoricalStrength": 1.0,
                    "team2HistoricalStrength": 0.9}),
        _pair_with(build_pair, 3, ETA, IOTA, None),           # probability absent
        _pair_with(build_pair, 4, BETA, ZETA,
                   {"team1Win": 0.5, "team2Win": 0.5, "draw": 0.0, "method": "no_data"}),
    ]
    api.set_pairs("ROUND_OF_16", pairs)
    for team1, team2 in ((ALPHA, GAMMA), (DELTA, EPSILON), (ETA, IOTA), (BETA, ZETA)):
        api.set_analysis(team1[0], team2[0], build_analysis(team1, team2))
    return api


def _card(page, label):
    return page.locator(".metric-card", has_text=re.compile(label, re.I)).first


def test_strongest_team_card_uses_actual_strength(knockout_fixture, open_app, page):
    open_app("")
    card = _card(page, "strongest team")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Alpha Strength FC")
    expect(card).to_contain_text("99.9")


def test_toughest_schedule_card_uses_actual_solkoff(knockout_fixture, open_app, page):
    open_app("")
    card = _card(page, "toughest schedule")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Beta Schedule FC")
    expect(card).to_contain_text("1.90")


def test_most_balanced_tie_uses_meaningful_predictions(knockout_fixture, open_app, page):
    open_app("")
    card = _card(page, "most balanced tie")
    expect(card).to_be_visible()
    # Delta/Epsilon (52/48) is closer than Alpha/Gamma (70/30) AND must win over
    # the excluded no-data tie whose displayed split would be a perfect 50/50.
    expect(card).to_contain_text("Delta Rovers")
    expect(card).to_contain_text("Epsilon City")
    expect(card).not_to_contain_text("Beta Schedule FC")


def test_most_balanced_tie_excludes_default_methods_and_zero_total(api, build_pair,
                                                                  open_app, page):
    pairs = [
        _pair_with(build_pair, 1, ALPHA, GAMMA,
                   {"team1Win": 0.60, "team2Win": 0.40, "draw": 0.0,
                    "method": "combined_strength"}),
        # Perfectly balanced but a default/no-signal method -> excluded.
        _pair_with(build_pair, 2, DELTA, EPSILON,
                   {"team1Win": 0.5, "team2Win": 0.5, "draw": 0.0,
                    "method": "equal_strength"}),
        # Zero win total -> must be treated as unavailable, not 50/50.
        _pair_with(build_pair, 3, ETA, IOTA,
                   {"team1Win": 0.0, "team2Win": 0.0, "draw": 0.0,
                    "method": "combined_strength"}),
    ]
    api.set_pairs("ROUND_OF_16", pairs)
    open_app("")
    card = _card(page, "most balanced tie")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Alpha Strength FC")
    expect(card).to_contain_text("Gamma Pinnacle")
    expect(card).not_to_contain_text("Delta Rovers")
    expect(card).not_to_contain_text("Eta Sporting")


def test_most_balanced_tie_excludes_no_common_opponents_and_error(api, build_pair,
                                                                  open_app, page):
    pairs = [
        _pair_with(build_pair, 1, ALPHA, GAMMA,
                   {"team1Win": 0.58, "team2Win": 0.42, "draw": 0.0,
                    "method": "combined_strength"}),
        # Backend fallbacks: perfectly balanced placeholders that are not real
        # predictions and must never win the balanced-tie selection.
        _pair_with(build_pair, 2, DELTA, EPSILON,
                   {"team1Win": 0.5, "team2Win": 0.5, "draw": 0.0,
                    "method": "no_common_opponents"}),
        _pair_with(build_pair, 3, ETA, IOTA,
                   {"team1Win": 0.5, "team2Win": 0.5, "draw": 0.0,
                    "method": "error"}),
    ]
    api.set_pairs("ROUND_OF_16", pairs)
    open_app("")
    card = _card(page, "most balanced tie")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Alpha Strength FC")
    expect(card).to_contain_text("Gamma Pinnacle")
    expect(card).not_to_contain_text("Delta Rovers")
    expect(card).not_to_contain_text("Eta Sporting")


def test_top_ten_strength_ranking(knockout_fixture, open_app, page):
    open_app("")
    rows = page.locator("#strengthRanking .rank-row")
    expect(rows).to_have_count(10)
    expect(rows.first).to_be_visible()
    first = rows.first.inner_text()
    assert "Alpha Strength FC" in first
    assert "99.9" in first
    # Second row must be Omega Playoff (90.0) despite its low official position,
    # proving the ranking uses the strength metric (not official position order).
    assert "Omega Playoff" in rows.nth(1).inner_text()


def test_top_ten_schedule_ranking(knockout_fixture, open_app, page):
    open_app("")
    rows = page.locator("#scheduleRanking .rank-row")
    expect(rows).to_have_count(10)
    expect(rows.first).to_be_visible()
    first = rows.first.inner_text()
    assert "Beta Schedule FC" in first
    assert "1.90" in first


def test_bars_normalized_against_complete_dataset(knockout_fixture, open_app, page,
                                                  bar_ratio):
    open_app("")
    leader = page.locator("#strengthRanking .rank-row").first
    leader_ratio = bar_ratio(leader.locator(".bar-fill"))
    assert abs(leader_ratio - 1.0) < 0.03, leader_ratio

    beta_row = page.locator("#strengthRanking .rank-row",
                            has_text="Beta Schedule FC").first
    expect(beta_row).to_be_visible()
    beta_ratio = bar_ratio(beta_row.locator(".bar-fill"))
    # 40.0 / 99.9 == 0.4004 against the full-dataset maximum.
    assert abs(beta_ratio - (40.0 / 99.9)) < 0.04, beta_ratio


def test_knockout_ties_labelled_and_in_api_order(knockout_fixture, open_app, page):
    open_app("")
    overview = page.locator("#overview")
    expect(overview).to_contain_text(re.compile(r"knockout ties", re.I))
    expect(overview).not_to_contain_text(re.compile(r"\bupcoming\b", re.I))

    cards = page.locator("#overviewKnockoutCards > *")
    expect(cards).to_have_count(3)
    first = cards.nth(0).inner_text()
    second = cards.nth(1).inner_text()
    third = cards.nth(2).inner_text()
    assert "Alpha Strength FC" in first
    assert "Delta Rovers" in second
    assert "Eta Sporting" in third
    assert "Beta Schedule FC" not in (first + second + third)


def test_missing_probability_is_unavailable_not_fake_50_50(knockout_fixture, open_app,
                                                           page):
    open_app("")
    cards = page.locator("#overviewKnockoutCards > *")
    eta_card = cards.filter(has_text="Eta Sporting").first
    expect(eta_card).to_be_visible()
    expect(eta_card).to_contain_text(
        re.compile(r"unavailable|no data|insufficient|not available", re.I))
    expect(eta_card).not_to_contain_text(re.compile(r"\b50(\.0)?\s*%"))


def test_win_probability_labelled_excluding_draws(knockout_fixture, open_app, page):
    open_app("")
    overview = page.locator("#overview")
    expect(overview).to_contain_text(re.compile(r"win probability", re.I))
    expect(overview).to_contain_text(re.compile(r"excluding draws", re.I))


def test_knockout_preview_opens_stage_and_pair(knockout_fixture, open_app, stage_tab,
                                               page):
    open_app("")
    cards = page.locator("#overviewKnockoutCards > *")
    cards.filter(has_text="Alpha Strength FC").first.click()
    expect(page.locator("#knockout")).to_be_visible()
    round16 = stage_tab(page, r"round of 16")
    expect(round16).to_be_visible()
    panel = page.locator("#knockout")
    expect(panel).to_contain_text("Alpha Strength FC")
    expect(panel).to_contain_text("Gamma Pinnacle")


def test_methodology_describes_ppg_and_formula(knockout_fixture, open_app, page):
    open_app("")
    overview = page.locator("#overview")
    expect(overview).to_contain_text(
        re.compile(r"average\s+(opponent\s+)?(points\s+per\s+game|ppg)", re.I))
    expect(overview).to_contain_text(re.compile(r"points\s*%", re.I))
    expect(overview).to_contain_text(re.compile(r"solkoff", re.I))


def test_standings_loaded_at_browser_fetch_time(knockout_fixture, open_app, page):
    open_app("")
    expect(page.locator("body")).to_contain_text(re.compile(r"standings loaded", re.I))


def test_no_hardcoded_season_or_trend(knockout_fixture, open_app, page):
    open_app("")
    expect(page.locator("#overview")).to_be_visible()
    expect(page.locator("#strengthRanking .rank-row").first).to_be_visible()
    body = page.locator("body").inner_text()
    assert "2024/25" not in body
    # No fabricated trend deltas from the static prototype.
    assert not re.search(r"[▲▼]\s*\+\d", body)


def test_empty_standings_shows_explicit_state(api, open_app, page):
    api.set_standings([])
    open_app("")
    overview = page.locator("#overview")
    expect(overview).to_be_visible()
    expect(overview).to_contain_text(
        re.compile(r"no standings|not available|unavailable|no data", re.I))


def test_empty_pairs_shows_explicit_state(api, open_app, page):
    api.set_pairs("ROUND_OF_16", [])
    open_app("")
    overview = page.locator("#overview")
    expect(overview).to_be_visible()
    expect(overview).to_contain_text(
        re.compile(r"no .*(ties|pairs|knockout)|not available|unavailable|no data", re.I))
