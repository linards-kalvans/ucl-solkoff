"""Knockout results rendering: stored leg scores, aggregates and finals.

The suite serves the production ``frontend/`` through the shared conftest
fixtures, intercepting every ``/api/`` call with deterministic in-memory
payloads (no network, no database). Only user-visible behaviour is asserted.

Covered contract (docs/superpowers/plans/2026-10-15-knockout-results.md):

* Full pair cards show each scored leg with home/away labels plus an explicitly
  labelled aggregate when one is available.
* Overview mini previews show a compact aggregate (or single-match result).
* A single-match final shows its own score, never an aggregate.
* Archived seasons keep win probability unavailable while results stay visible.
* Scheduled/unscored fixtures never render fabricated scores, and a drawn
  aggregate never implies a penalty winner.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

ALPHA = (5, "Alpha Strength FC", "https://crest.example/str.png")
GAMMA = (1, "Gamma Pinnacle", None)
DELTA = (2, "Delta Rovers", "https://crest.example/del.png")
EPSILON = (3, "Epsilon City", "https://crest.example/eps.png")
ZETA = (4, "Zeta Wanderers", "https://crest.example/zet.png")
ETA = (6, "Eta Sporting", "https://crest.example/eta.png")

AGGREGATE = re.compile(r"agg(regate)?", re.I)
HOME_LABEL = re.compile(r"\bhome\b", re.I)
AWAY_LABEL = re.compile(r"\baway\b", re.I)


def leg(match_id, home, away, hs, as_, status, date):
    """A backend-contract leg fixture; ``(id, name, crest)`` team tuples."""
    return {
        "matchId": match_id,
        "homeTeamId": home[0],
        "awayTeamId": away[0],
        "homeScore": hs,
        "awayScore": as_,
        "status": status,
        "date": date,
    }


def score_pattern(hs, as_):
    """Tolerant scoreline matcher (dash/en-dash/colon separators, any spacing)."""
    return re.compile(rf"{hs}\s*[-–:]\s*{as_}")


def strip_iso_dates(text):
    """Remove ISO date/time tokens so score regexes cannot match them."""
    return re.sub(r"\d{4}-\d{2}-\d{2}\S*", "", text)


def card(page, container_id):
    return page.locator(f"#{container_id} .playoff-pair-card").first


def _seasons_for(api, path):
    return [season for _method, p, season in api.season_log if p == path]


# ---------------------------------------------------------------------------
# full cards
# ---------------------------------------------------------------------------

def test_full_card_shows_each_scored_leg_and_aggregate(open_app, page, api, build_pair):
    pair = build_pair(1, ALPHA, GAMMA, stage="ROUND_OF_16", status="FINISHED")
    pair["legs"] = [
        leg(101, ALPHA, GAMMA, 2, 0, "FINISHED", "2026-03-04T20:00:00Z"),
        leg(102, GAMMA, ALPHA, 1, 3, "FINISHED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = {"team1": 5, "team2": 1}
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("round-of-16")
    text = card(page, "round-of-16-pairs").inner_text()

    # Each scored leg is visible with its home/away orientation.
    assert HOME_LABEL.search(text), text
    assert AWAY_LABEL.search(text), text
    assert score_pattern(2, 0).search(text), text
    assert score_pattern(1, 3).search(text), text
    # The aggregate is explicitly labelled and oriented by stable teams.
    assert AGGREGATE.search(text), text
    assert score_pattern(5, 1).search(text), text


def test_single_match_final_shows_result_without_aggregate(open_app, page, api,
                                                           build_pair):
    pair = build_pair(2, DELTA, EPSILON, stage="FINAL", status="FINISHED")
    pair["legs"] = [
        leg(201, DELTA, EPSILON, 2, 1, "FINISHED", "2026-05-31T20:00:00Z"),
    ]
    pair["aggregateScore"] = None
    api.set_pairs("FINAL", [pair])

    open_app("final")
    text = card(page, "final-pairs").inner_text()

    assert score_pattern(2, 1).search(text), text
    assert not AGGREGATE.search(text), text


def test_drawn_aggregate_never_implies_penalty_winner(open_app, page, api, build_pair):
    pair = build_pair(3, ZETA, ETA, stage="ROUND_OF_16", status="FINISHED")
    pair["legs"] = [
        leg(301, ZETA, ETA, 1, 1, "FINISHED", "2026-03-04T20:00:00Z"),
        leg(302, ETA, ZETA, 1, 1, "FINISHED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = {"team1": 2, "team2": 2}
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("round-of-16")
    text = card(page, "round-of-16-pairs").inner_text()

    assert score_pattern(2, 2).search(text), text
    # Stored goals cannot encode shootouts: no invented winner/advancement.
    assert not re.search(r"penalt|shootout|\badvanced\b|\bthrough\b", text, re.I), text


# ---------------------------------------------------------------------------
# overview mini previews
# ---------------------------------------------------------------------------

def test_mini_preview_shows_compact_aggregate(open_app, page, api, build_pair):
    pair = build_pair(1, ALPHA, GAMMA, stage="ROUND_OF_16", status="FINISHED")
    pair["legs"] = [
        leg(101, ALPHA, GAMMA, 2, 0, "FINISHED", "2026-03-04T20:00:00Z"),
        leg(102, GAMMA, ALPHA, 1, 3, "FINISHED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = {"team1": 5, "team2": 1}
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("")
    mini = page.locator("#overviewKnockoutCards .mini-match").first
    expect(mini).to_be_visible()
    text = mini.inner_text()

    # Compact aggregate result is visible in the preview.
    assert score_pattern(5, 1).search(text), text


def test_mini_preview_shows_single_result_for_final(open_app, page, api, build_pair):
    # The preview follows the resolved current stage, so a true Final must be
    # served for FINAL — not a Round of 16 payload standing in for it.
    pair = build_pair(2, DELTA, EPSILON, stage="FINAL", status="FINISHED")
    pair["legs"] = [
        leg(201, DELTA, EPSILON, 3, 2, "FINISHED", "2026-05-31T20:00:00Z"),
    ]
    pair["aggregateScore"] = None
    api.set_stage("FINAL")
    api.set_pairs("FINAL", [pair])

    open_app("")
    mini = page.locator("#overviewKnockoutCards .mini-match").first
    expect(mini).to_be_visible()
    text = mini.inner_text()

    assert score_pattern(3, 2).search(text), text


def test_mini_preview_single_leg_orients_to_stable_teams(open_app, page, api,
                                                         build_pair):
    # Single-leg fixture whose payload home side is team2: the preview must
    # orient the score to the pair's stable team1/team2 identity, not flip it.
    pair = build_pair(1, ALPHA, GAMMA, stage="ROUND_OF_16", status="FINISHED")
    pair["legs"] = [
        leg(111, GAMMA, ALPHA, 3, 2, "FINISHED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = None
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("")
    mini = page.locator("#overviewKnockoutCards .mini-match").first
    expect(mini).to_be_visible()
    text = mini.inner_text()

    # team1 = Alpha 2, team2 = Gamma 3 — oriented, not the raw 3-2 home order.
    assert score_pattern(2, 3).search(text), text
    assert not score_pattern(3, 2).search(text), text


def test_mini_preview_partial_tie_shows_no_single_result(open_app, page, api,
                                                         build_pair):
    # A two-leg tie with one finished and one unplayed leg is not a single
    # match: the preview must not present the lone leg as a final result.
    pair = build_pair(3, ZETA, ETA, stage="ROUND_OF_16", status="SCHEDULED")
    pair["legs"] = [
        leg(601, ZETA, ETA, 1, 0, "FINISHED", "2026-03-04T20:00:00Z"),
        leg(602, ETA, ZETA, None, None, "SCHEDULED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = None
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("")
    mini = page.locator("#overviewKnockoutCards .mini-match").first
    expect(mini).to_be_visible()
    text = mini.inner_text()

    scrubbed = strip_iso_dates(text)
    assert not AGGREGATE.search(text), text
    assert not re.search(r"\d+\s*[-–:]\s*\d+", scrubbed), scrubbed


def test_malformed_scores_and_aggregate_are_not_rendered(open_app, page, api,
                                                        build_pair):
    # Malformed values (strings, booleans, negatives, nulls) must be rejected
    # rather than coerced to 0 or interpolated into markup.
    pair = build_pair(4, ALPHA, DELTA, stage="ROUND_OF_16", status="FINISHED")
    pair["legs"] = [
        leg(701, ALPHA, DELTA, "2", 1, "FINISHED", "2026-03-04T20:00:00Z"),
        leg(702, DELTA, ALPHA, True, 3, "FINISHED", "2026-03-11T20:00:00Z"),
        leg(703, ALPHA, DELTA, -1, 0, "FINISHED", "2026-03-18T20:00:00Z"),
    ]
    pair["aggregateScore"] = {"team1": None, "team2": "1"}
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("round-of-16")
    text = card(page, "round-of-16-pairs").inner_text()

    scrubbed = strip_iso_dates(text)
    assert not AGGREGATE.search(text), text
    assert not re.search(r"\d+\s*[-–:]\s*\d+", scrubbed), scrubbed


# ---------------------------------------------------------------------------
# scheduled / unscored ties
# ---------------------------------------------------------------------------

def test_scheduled_tie_shows_no_fabricated_scores(open_app, page, api, build_pair):
    pair = build_pair(4, ALPHA, DELTA, stage="ROUND_OF_16", status="SCHEDULED")
    pair["legs"] = [
        leg(401, ALPHA, DELTA, None, None, "SCHEDULED", "2026-03-04T20:00:00Z"),
        leg(402, DELTA, ALPHA, None, None, "SCHEDULED", "2026-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = None
    api.set_pairs("ROUND_OF_16", [pair])

    open_app("round-of-16")
    text = card(page, "round-of-16-pairs").inner_text()

    scrubbed = strip_iso_dates(text)
    assert not AGGREGATE.search(text), text
    # No scoreline may be invented for a scheduled tie.
    assert not re.search(r"\d+\s*[-–]\s*\d+", scrubbed), scrubbed


# ---------------------------------------------------------------------------
# archived probabilities stay unavailable
# ---------------------------------------------------------------------------

def test_archived_results_keep_probability_unavailable(open_app, page, api,
                                                       build_pair, wait_until):
    pair = build_pair(5, ALPHA, GAMMA, stage="ROUND_OF_16", status="FINISHED",
                      date="2025-03-04T20:00:00Z")
    pair["legs"] = [
        leg(501, ALPHA, GAMMA, 2, 0, "FINISHED", "2025-03-04T20:00:00Z"),
        leg(502, GAMMA, ALPHA, 1, 3, "FINISHED", "2025-03-11T20:00:00Z"),
    ]
    pair["aggregateScore"] = {"team1": 5, "team2": 1}
    pair["winProbability"] = {"team1Win": 0.0, "team2Win": 0.0, "draw": 0.0,
                              "method": "historical_no_data"}
    api.set_pairs("ROUND_OF_16", [pair])
    api.set_pairs_for_season(2024, "ROUND_OF_16", [pair])

    open_app("round-of-16")
    page.select_option("#seasonSelect", "2024")
    wait_until(lambda: "2024" in _seasons_for(api, "/api/knockout-pairs/ROUND_OF_16"))

    text = card(page, "round-of-16-pairs").inner_text()

    # Results remain visible, but no probability is fabricated.
    assert score_pattern(5, 1).search(text), text
    assert re.search(r"unavailable|no data|not available", text, re.I), text
    assert not re.search(r"\d+(\.\d+)?\s*%", text), text
