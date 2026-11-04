"""Tests for the reviewed ``QUALIFYING`` stage scope.

A qualifying tie is pre-main-stage: it must be excluded precisely from knockout
pair/stage detection and from the ESPN legacy-takeover guards, while a
NULL/unknown stage stays guarded and qualifying results remain in the pooled Elo
corpus and are ignored by the league-scoped Solkoff calculation.

Everything runs against real temporary DuckDB databases; no network, production
data or scheduler is touched.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.database import Database
from backend.elo_calculator import EloCalculator
from backend.espn_service import ESPNDataService
from backend.playoff_analyzer import PlayoffAnalyzer
from backend.solkoff_calculator import SolkoffCalculator

QUALIFYING = (41, 42)
R16 = (1, 2)

STAGES = ["KNOCKOUT_PLAYOFF", "ROUND_OF_16", "QUARTER_FINAL", "SEMI_FINAL", "FINAL"]


class MockAPI:
    """No-network stand-in for ``APIClient``."""

    def __getattr__(self, name):
        def _explode(*args, **kwargs):
            raise AssertionError(f"unexpected API access: {name}")

        return _explode


@contextmanager
def historical_current(year=2026):
    with patch("backend.season_service.current_season_year", lambda today=None: year):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "qualifying_scope.db"))
    yield database
    database.close()


def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", None),
    )


def add_match(
    db,
    match_id,
    home,
    away,
    date,
    *,
    stage="KNOCKOUT",
    round_name=None,
    matchday=None,
    competition_id="CL",
    status="FINISHED",
):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (match_id, home, away, 1, 0, matchday, date, status, stage, round_name,
         competition_id),
    )


def add_pair(db, match_id, pair, date, **kwargs):
    add_team(db, pair[0], f"Team {pair[0]}")
    add_team(db, pair[1], f"Team {pair[1]}")
    add_match(db, match_id, pair[0], pair[1], date, **kwargs)


def _analyzer(db, season_start_year=2024):
    return PlayoffAnalyzer(
        db, MockAPI(), competition_id="CL", season_start_year=season_start_year
    )


def pairs(db, stage):
    with historical_current():
        return _analyzer(db).get_pairs_by_stage(stage, "CL")


def all_pairs(db):
    with historical_current():
        return _analyzer(db).get_playoff_pairs("CL")


def current_stage(db):
    with historical_current():
        return _analyzer(db).get_current_stage("CL")


def ids(result):
    return {frozenset((p["team1"]["id"], p["team2"]["id"])) for p in result}


def seed_qualifying_and_r16(db):
    add_pair(db, 900, QUALIFYING, "2024-08-06T20:00:00Z",
             stage="QUALIFYING", round_name="Play-off Round", matchday=14)
    add_pair(db, 901, R16, "2025-03-04T20:00:00Z",
             round_name="Round of 16", matchday=7)
    db.commit()


# ---------------------------------------------------------------------------
# pairs / stage exclusion
# ---------------------------------------------------------------------------

def test_qualifying_excluded_from_pairs_and_stage(db):
    seed_qualifying_and_r16(db)
    qual = frozenset(QUALIFYING)
    real = frozenset(R16)

    assert qual not in ids(all_pairs(db))
    assert real in ids(all_pairs(db))

    assert ids(pairs(db, "KNOCKOUT_PLAYOFF")) == set()
    assert ids(pairs(db, "ROUND_OF_16")) == {real}
    # A high qualifying matchday must not surface as a later main stage.
    for stage in STAGES:
        assert qual not in ids(pairs(db, stage)), f"qualifier leaked into {stage}"


def test_qualifying_does_not_change_current_stage(db):
    seed_qualifying_and_r16(db)
    # Without the exclusion the round "Play-off Round" / matchday 14 would push
    # the detected stage to a knockout round.
    assert current_stage(db) == "ROUND_OF_16"


def test_null_stage_heuristic_still_guarded(db):
    add_team(db, 51, "Team 51")
    add_team(db, 52, "Team 52")
    add_match(db, 950, 51, 52, "2025-02-11T20:00:00Z",
              stage=None, round_name=None, matchday=9)
    db.commit()

    assert ids(pairs(db, "KNOCKOUT_PLAYOFF")) == {frozenset({51, 52})}


def test_debug_knockout_query_excludes_qualifying(db):
    add_pair(db, 960, QUALIFYING, "2024-08-06T20:00:00Z",
             stage="QUALIFYING", round_name="Play-off Round", matchday=9)
    add_pair(db, 961, R16, "2025-03-04T20:00:00Z",
             round_name="Round of 16", matchday=7)
    db.commit()

    from backend.main import app

    with patch("backend.main.db", db):
        response = TestClient(app).get("/api/debug/knockout-matches")

    assert response.status_code == 200
    stages = {m["stage"] for m in response.json()["matches"]}
    assert "QUALIFYING" not in stages
    assert "KNOCKOUT" in stages


# ---------------------------------------------------------------------------
# ESPN legacy-takeover guard exclusion
# ---------------------------------------------------------------------------

def test_espn_guard_excludes_qualifying_but_keeps_null(db):
    add_team(db, 1, "Team 1")
    add_team(db, 2, "Team 2")
    svc = ESPNDataService(db)

    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (-1, 1, 2, NULL, NULL, NULL, '2024-08-06', 'SCHEDULED',
                  'QUALIFYING', 'Qualifying', NULL, 'EL')
        """
    )
    db.commit()
    # An evidenced qualifying row is not an unclaimed main-stage duplicate.
    assert svc._unmanaged_legacy_present("EL") is False
    assert svc._unmanaged_season_rows("EL", 2024) == []

    # A NULL-stage unmanaged row is still guarded.
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (-2, 1, 2, NULL, NULL, NULL, '2024-09-19', 'SCHEDULED',
                  NULL, NULL, NULL, 'EL')
        """
    )
    db.commit()
    assert svc._unmanaged_legacy_present("EL") is True
    assert [int(r[0]) for r in svc._unmanaged_season_rows("EL", 2024)] == [-2]


# ---------------------------------------------------------------------------
# qualifiers retained in Elo, ignored by league-scoped Solkoff
# ---------------------------------------------------------------------------

def test_qualifying_retained_in_pooled_elo(db):
    add_team(db, 1, "Team 1")
    add_team(db, 2, "Team 2")
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (1, 1, 2, 2, 0, NULL, '2024-08-06', 'FINISHED',
                  'QUALIFYING', 'Play-off Round', NULL, 'EL')
        """
    )
    db.commit()

    EloCalculator(db).calculate_all()

    assert db.fetchone("SELECT COUNT(*) FROM elo_ratings")[0] == 2


def test_qualifying_not_counted_as_league_opponents(db):
    add_team(db, 1, "Team 1")
    add_team(db, 2, "Team 2")
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (1, 1, 2, 2, 0, NULL, '2024-08-06', 'FINISHED',
                  'QUALIFYING', 'Play-off Round', NULL, 'EL')
        """
    )
    db.commit()

    with patch("backend.solkoff_calculator.get_season_bounds",
               lambda today=None: ("2024-08-01", "2025-08-01")):
        calculator = SolkoffCalculator(db, competition_id="EL")
        assert calculator.get_opponents(1) == set()
