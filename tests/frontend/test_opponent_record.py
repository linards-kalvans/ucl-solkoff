"""Opponent detail panel: season W/D/L, H2H label and score perspective.

The panel must show the opponent's season record supplied by the response
(never a head-to-head tally), label the head-to-head match count explicitly,
keep match scores from the selected club's perspective, and show a missing
record as unavailable rather than an invented 0W 0D 0L.
"""
from __future__ import annotations

from playwright.sync_api import expect

TEAM_ID = 5  # Alpha Strength FC, present in the standings fixture
TEAM_NAME = "Alpha Strength FC"


def build_details(team_id, season_record=None, matches=None, team_name=TEAM_NAME):
    opp = {
        "teamId": 99,
        "teamName": "Opponent United",
        "teamCrest": None,
        "points": 9,
        "played": 3,
        "pointsPerGame": 3.0,
        "matchesPlayed": len(matches) if matches is not None else 1,
        "matches": matches if matches is not None else [
            {
                "homeScore": 2, "awayScore": 1, "teamScore": 2,
                "opponentScore": 1, "outcome": "win",
                "date": "2025-11-01", "isHome": True,
            }
        ],
    }
    if season_record is not None:
        opp.update(season_record)
    return {
        "teamId": team_id,
        "teamName": team_name,
        "teamCode": "STR",
        "teamCrest": None,
        "solkoffCoefficient": 1.1,
        "opponents": [opp],
        "averageOpponentPPG": 3.0,
        "matchesCount": opp["matchesPlayed"],
        "opponentsCount": 1,
    }


def _expand(page):
    row = page.locator(
        "#standingsBody tr[data-team-id]", has_text=TEAM_NAME).first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator("#standingsBody")).to_contain_text("Opponent United")


def test_season_record_comes_from_response_not_head_to_head(open_app, page, api):
    # Opponent lost nothing season-wise (5W 2D 1L) yet the H2H match was a win.
    api.set_solkoff(TEAM_ID, build_details(
        TEAM_ID, season_record={"won": 5, "draw": 2, "lost": 1}))
    open_app("league")
    _expand(page)

    season = page.locator(".opponent-stats").first
    expect(season).to_contain_text("Season:")
    expect(season).to_have_text("Season: 5W 2D 1L")


def test_real_zeroes_render(open_app, page, api):
    api.set_solkoff(TEAM_ID, build_details(
        TEAM_ID, season_record={"won": 0, "draw": 0, "lost": 0}))
    open_app("league")
    _expand(page)

    expect(page.locator(".opponent-stats").first)\
        .to_have_text("Season: 0W 0D 0L")


def test_missing_season_record_is_unavailable(open_app, page, api):
    api.set_solkoff(TEAM_ID, build_details(TEAM_ID, season_record={}))
    open_app("league")
    _expand(page)

    season = page.locator(".opponent-stats").first
    expect(season).to_have_text("Season: unavailable")
    expect(season).not_to_contain_text("0W 0D 0L")


def test_head_to_head_count_labelled(open_app, page, api):
    api.set_solkoff(TEAM_ID, build_details(
        TEAM_ID, season_record={"won": 5, "draw": 2, "lost": 1}))
    open_app("league")
    _expand(page)

    expect(page.locator(".opponent-matches").first)\
        .to_have_text("1 H2H match")


def test_match_scores_keep_selected_club_perspective(open_app, page, api):
    api.set_solkoff(TEAM_ID, build_details(
        TEAM_ID, season_record={"won": 5, "draw": 2, "lost": 1}))
    open_app("league")
    _expand(page)

    matches = page.locator(".match-results").first
    expect(matches).to_have_attribute(
        "title", f"Scores shown from {TEAM_NAME}'s perspective")
    # Score stays selected-club-first and unchanged.
    expect(matches.locator(".match-result").first).to_contain_text("2-1")
