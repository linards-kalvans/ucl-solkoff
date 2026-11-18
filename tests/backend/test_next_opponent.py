"""Next confirmed league-phase fixture in live League standings.

Task 1 of ``docs/superpowers/plans/2026-11-18-next-opponent.md``. The lookup
must pick each club's first strictly-future league-phase fixture from stored
schedule rows, then the live standings response publishes opponent PPG and
sample size from the *same* standings snapshot. Archived tables stay unchanged.

Runs against a real temporary DuckDB with the current season pinned; no
lifespan, network, or background work is triggered.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.season_service as season_service
from backend.database import Database
from backend.main import app

CURRENT = 2026
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

# Archived-response shape: the historical builder's row keys, which must not
# gain a next-fixture field.
ARCHIVED_KEYS = {
    "teamId", "teamName", "teamCode", "teamCrest", "position", "played",
    "won", "drawn", "lost", "gf", "ga", "gd", "points",
    "solkoffCoefficient", "strengthScore",
}


@contextmanager
def fixed_current(year=CURRENT):
    """Pin the resolved current season in every module that reads it."""
    with patch.object(season_service, "current_season_year", lambda today=None: year), \
         patch("backend.main.current_season_year", lambda: year):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "next_opponent.db"))
    yield database
    database.close()


# ---------------------------------------------------------------------------
# insertion helpers
# ---------------------------------------------------------------------------

def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", None),
    )


def add_match(db, match_id, home, away, date, status="SCHEDULED",
              stage="LEAGUE_STAGE", competition_id="CL", matchday=1,
              hs=None, as_=None):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, home, away, hs, as_, matchday, date, status, stage,
         None, None, competition_id),
    )


def add_standing(db, team_id, played, points, competition_id="CL",
                 position=None):
    db.execute(
        """
        INSERT INTO standings (
            competition_id, team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (competition_id, team_id, position, played, 0, 0, 0, 0, 0, 0, points,
         "2026-09-01T00:00:00Z"),
    )


def add_solkoff(db, team_id, value, competition_id="CL"):
    db.execute(
        """
        INSERT INTO solkoff_coefficients
            (competition_id, team_id, solkoff_value, calculated_at)
        VALUES (?, ?, ?, ?)
        """,
        (competition_id, team_id, value, "2026-11-01T00:00:00Z"),
    )


def _lookup(db, now=NOW, year=CURRENT, competition_id="CL"):
    """Call the batched lookup. Kept as an attribute access so a missing
    implementation fails with a clear ``AttributeError`` per test."""
    return season_service.next_fixtures(db, year, competition_id, now)


def _api_standings(db, season=None, competition=None, current=CURRENT):
    query = []
    if season is not None:
        query.append(f"season={season}")
    if competition is not None:
        query.append(f"competition={competition}")
    suffix = f"?{'&'.join(query)}" if query else ""
    with fixed_current(current), patch("backend.main.db", db):
        return TestClient(app).get(f"/api/standings{suffix}")


def _by_team(response):
    assert response.status_code == 200, response.text
    return {row["teamId"]: row for row in response.json()}


# ---------------------------------------------------------------------------
# selection: first eligible fixture, both venues, deterministic order
# ---------------------------------------------------------------------------

def test_first_future_fixture_per_team_and_both_venues(db):
    for team_id in range(1, 5):
        add_team(db, team_id, f"Team {team_id}")
    # Team 1 has a later home fixture and an earlier away fixture: the earlier
    # kickoff wins and the venue is described from team 1's perspective.
    add_match(db, 10, 1, 2, "2026-11-20T20:00:00Z")
    add_match(db, 11, 3, 1, "2026-11-19T20:00:00Z")
    db.commit()

    result = _lookup(db)

    assert set(result) == {1, 2, 3}
    assert result[1] == {
        "opponentId": 3,
        "opponentName": "Team 3",
        "venue": "away",
    }
    # The home side of the same match reports venue="home".
    assert result[3]["opponentId"] == 1
    assert result[3]["venue"] == "home"
    assert result[3]["opponentName"] == "Team 1"
    # The later home fixture still gives team 2 an away tie.
    assert result[2]["opponentId"] == 1
    assert result[2]["venue"] == "away"


def test_equal_kickoffs_break_ties_by_match_id(db):
    for team_id in range(1, 4):
        add_team(db, team_id, f"Team {team_id}")
    when = "2026-11-25T20:00:00Z"
    # Insert the higher id first; the lower id must still win the tie.
    add_match(db, 31, 1, 2, when)
    add_match(db, 30, 1, 3, when)
    db.commit()

    assert _lookup(db)[1]["opponentId"] == 3


# ---------------------------------------------------------------------------
# date semantics
# ---------------------------------------------------------------------------

def test_times_are_compared_as_instants_not_text(db):
    for team_id in range(1, 8):
        add_team(db, team_id, f"Team {team_id}")

    # 14:00+02:00 == 12:00Z, exactly "now": not strictly future.
    add_match(db, 40, 1, 2, "2026-11-18T14:00:00+02:00")
    # Past instant written with an offset.
    add_match(db, 41, 1, 3, "2026-11-18T13:00:00+02:00")
    # Malformed stored values must be skipped, never crash.
    add_match(db, 42, 1, 4, "not-a-date")
    add_match(db, 43, 1, 5, "")
    # Date-only values are eligible only on later UTC days.
    add_match(db, 44, 1, 6, "2026-11-18")
    add_match(db, 45, 1, 7, "2026-11-19")
    db.commit()

    assert _lookup(db)[1]["opponentId"] == 7


def test_naive_stored_time_is_treated_as_utc(db):
    for team_id in range(1, 4):
        add_team(db, team_id, f"Team {team_id}")
    # No timezone suffix: interpreted as 13:00 UTC, one hour after NOW.
    add_match(db, 50, 1, 2, "2026-11-18T13:00:00")
    # Lexicographically larger, but actually earlier than the naive fixture.
    add_match(db, 51, 1, 3, "2026-11-18T14:00:00+02:00")  # 12:00Z
    db.commit()

    assert _lookup(db)[1]["opponentId"] == 2


# ---------------------------------------------------------------------------
# scope: status, stage, competition, season, identity
# ---------------------------------------------------------------------------

def test_only_future_scheduled_or_timed_league_fixtures_leak_free(db):
    add_team(db, 1, "Subject")
    add_team(db, 2, "Eligible")
    add_team(db, 3, "Decoy")

    add_match(db, 60, 1, 3, "2026-11-20T20:00:00Z", status="FINISHED")
    add_match(db, 61, 1, 3, "2026-11-21T20:00:00Z", status="POSTPONED")
    add_match(db, 62, 1, 3, "2026-11-22T20:00:00Z", status="IN_PLAY")
    add_match(db, 63, 1, 3, "2026-11-23T20:00:00Z", stage="KNOCKOUT")
    add_match(db, 64, 1, 3, "2026-11-24T20:00:00Z",
              stage="KNOCKOUT_PLAYOFF")
    add_match(db, 65, 1, 3, "2026-11-25T20:00:00Z", stage="QUALIFIER")
    add_match(db, 66, 1, 3, "2026-11-26T20:00:00Z", competition_id="EL")
    # Cross-season decoys: after the exclusive 2027-08-01 end bound.
    add_match(db, 67, 1, 3, "2027-08-05T20:00:00Z")
    add_match(db, 68, 1, 3, "2027-08-01T00:00:00Z")
    # A club cannot be its own opponent.
    add_match(db, 69, 1, 1, "2026-11-27T20:00:00Z")
    # The only eligible row is TIMED, which must be accepted.
    add_match(db, 70, 1, 2, "2026-11-28T20:00:00Z", status="TIMED")
    db.commit()

    result = _lookup(db)
    assert result[1]["opponentId"] == 2
    assert result[1]["venue"] == "home"


def test_blank_team_identity_never_becomes_an_opponent(db):
    # DuckDB enforces the teams FK, so a truly absent identity cannot be
    # inserted; blank/whitespace names are the observable "unresolved" shape.
    add_team(db, 1, "Subject")
    add_team(db, 2, "Real Opponent")
    add_team(db, 3, "")      # present row, empty name
    add_team(db, 4, "   ")   # present row, whitespace-only name

    add_match(db, 80, 1, 4, "2026-11-19T20:00:00Z")
    add_match(db, 81, 1, 3, "2026-11-20T20:00:00Z")
    add_match(db, 82, 1, 2, "2026-11-21T20:00:00Z")
    db.commit()

    result = _lookup(db)
    assert result[1]["opponentId"] == 2
    assert result[1]["opponentName"] == "Real Opponent"


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------

def _count_queries(db, monkeypatch):
    calls = {"n": 0}

    def wrap(name):
        real = getattr(db, name)

        def counted(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(db, name, counted)

    for name in ("execute", "fetchall", "fetchone"):
        wrap(name)
    return calls


def _build_many(db, teams):
    for team_id in range(1, teams + 1):
        add_team(db, team_id, f"Team {team_id}")
        add_standing(db, team_id, played=2, points=3)
    for team_id in range(1, teams, 2):
        add_match(db, 1000 + team_id, team_id, team_id + 1,
                  "2026-11-20T20:00:00Z")
    db.commit()


def test_lookup_does_not_query_once_per_team(tmp_path, monkeypatch):
    small = Database(db_path=str(tmp_path / "small.db"))
    large = Database(db_path=str(tmp_path / "large.db"))
    try:
        _build_many(small, 4)
        _build_many(large, 16)

        small_calls = _count_queries(small, monkeypatch)
        _lookup(small)
        small_count = small_calls["n"]

        large_calls = _count_queries(large, monkeypatch)
        _lookup(large)
        large_count = large_calls["n"]
    finally:
        small.close()
        large.close()

    assert small_count == large_count


# ---------------------------------------------------------------------------
# live response: nextFixture + opponent PPG/sample
# ---------------------------------------------------------------------------

def _build_live_fixtures(db):
    """Twelve clubs and six relative-UTC fixtures covering every stat case."""
    for team_id in range(1, 13):
        add_team(db, team_id, f"Team {team_id}")

    add_standing(db, 1, played=2, points=6, position=1)
    add_standing(db, 2, played=5, points=10, position=2)   # 2.00 PPG
    add_standing(db, 3, played=3, points=0, position=3)    # real 0.00 PPG
    add_standing(db, 4, played=4, points=7, position=4)
    add_standing(db, 5, played=2, points=3, position=5)
    # Team 6 intentionally has no standings row.
    add_standing(db, 7, played=2, points=1, position=7)
    add_standing(db, 8, played=0, points=0, position=8)    # no games
    add_standing(db, 9, played=2, points=4, position=9)
    add_standing(db, 10, played=2, points=2, position=10)
    add_standing(db, 11, played=2, points=2, position=11)
    add_standing(db, 12, played=2, points=None, position=12)  # missing points
    add_solkoff(db, 1, 1.5)

    now = datetime.now(timezone.utc)
    add_match(db, 1001, 1, 2, _iso(now + timedelta(hours=2)))
    add_match(db, 1002, 3, 4, _iso(now + timedelta(hours=3)), status="TIMED")
    add_match(db, 1003, 5, 6, _iso(now + timedelta(hours=4)))
    add_match(db, 1004, 7, 8, _iso(now + timedelta(hours=5)))
    # Stale scheduled row: kickoff already passed, so it must not win.
    add_match(db, 1005, 9, 10, _iso(now - timedelta(hours=1)))
    add_match(db, 1006, 11, 12, _iso(now + timedelta(hours=6)))
    db.commit()


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_live_standings_attach_next_fixture_and_opponent_ppg(db):
    _build_live_fixtures(db)
    rows = _by_team(_api_standings(db))

    team1 = rows[1]["nextFixture"]
    assert team1 == {
        "opponentId": 2,
        "opponentName": "Team 2",
        "venue": "home",
        "opponentPpg": 2.0,
        "opponentPlayed": 5,
    }
    # Same fixture from the opponent's side, and a real 0.00 PPG is kept.
    assert rows[2]["nextFixture"]["venue"] == "away"
    team4 = rows[4]["nextFixture"]
    assert team4["opponentId"] == 3
    assert team4["venue"] == "away"
    assert team4["opponentPpg"] == 0.0
    assert team4["opponentPlayed"] == 3


def test_live_standings_distinguish_missing_stats_from_zero_ppg(db):
    _build_live_fixtures(db)
    rows = _by_team(_api_standings(db))

    missing_row = rows[5]["nextFixture"]
    assert missing_row["opponentId"] == 6
    assert missing_row["opponentPpg"] is None
    assert missing_row["opponentPlayed"] is None

    zero_games = rows[7]["nextFixture"]
    assert zero_games["opponentId"] == 8
    assert zero_games["opponentPpg"] is None
    assert zero_games["opponentPlayed"] is None

    # Played games but NULL points still publish no PPG.
    missing_points = rows[11]["nextFixture"]
    assert missing_points["opponentId"] == 12
    assert missing_points["opponentPpg"] is None


def test_live_standings_keep_fixture_and_publish_null_without_eligible_match(db):
    _build_live_fixtures(db)
    rows = _by_team(_api_standings(db))

    # Stale scheduled rows are ignored, but the explicit null field remains.
    assert "nextFixture" in rows[9]
    assert rows[9]["nextFixture"] is None
    assert rows[10]["nextFixture"] is None

    fixture = rows[1]["nextFixture"]
    assert set(fixture) == {
        "opponentId", "opponentName", "venue", "opponentPpg", "opponentPlayed",
    }


def test_live_fixture_enrichment_leaves_rank_and_strength_values_unchanged(db):
    _build_live_fixtures(db)
    response = _api_standings(db)
    rows = _by_team(response)

    # Ranking still follows points, goal difference, goals for.
    ordered_ids = [row["teamId"] for row in response.json()]
    assert ordered_ids[0] == 2  # 10 points
    assert ordered_ids[1] == 4  # 7 points
    assert rows[1]["points"] == 6
    assert rows[1]["position"] == 1
    # (6 * 100 / (2 * 3)) * 1.5 / 100 == 1.5
    assert rows[1]["solkoffCoefficient"] == 1.5
    assert rows[1]["strengthScore"] == 1.5


def test_archived_standings_never_include_next_fixture(db):
    add_team(db, 1, "Archived One")
    add_team(db, 2, "Archived Two")
    add_match(db, 2001, 1, 2, f"{CURRENT}-09-15T20:00:00Z",
              status="FINISHED", hs=2, as_=0)
    # A future stored fixture in the same (archived) season must not surface.
    add_match(db, 2002, 1, 2, _iso(datetime.now(timezone.utc)
                                  + timedelta(hours=2)))
    db.commit()

    # Treat the season as archived by pinning a later current season.
    response = _api_standings(db, season=CURRENT, current=CURRENT + 1)
    rows = {row["teamId"]: row for row in response.json()}

    assert rows[1]["played"] == 1
    for row in response.json():
        assert set(row) == ARCHIVED_KEYS
        assert "nextFixture" not in row
