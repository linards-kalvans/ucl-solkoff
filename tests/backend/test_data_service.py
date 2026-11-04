"""Tests for the football-data data service (``backend.data_service``).

Every test runs against a real temporary DuckDB database; only the external
football-data.org client is mocked. Assertions target persisted rows (teams,
matches, standings), provider routing and identity resolution rather than
global SQL statement counts, so the real DuckDB writes and the retired-team
resolver are actually exercised.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from backend.api_client import APIClient
from backend.data_service import DataService
from backend.database import Database


@pytest.fixture
def db(tmp_path):
    """Real temporary DuckDB database with the production schema."""
    database = Database(str(tmp_path / "ucl.db"))
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def api_client():
    """Mocked external football-data client (the only external dependency)."""
    return Mock(spec=APIClient)


@pytest.fixture
def data_service(db, api_client):
    """Data service bound to the real database and mocked external client."""
    return DataService(db, api_client)


def _seed_teams(db, *teams):
    """Insert ``(id, name, code, crest)`` team rows."""
    for team_id, name, code, crest in teams:
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
            (team_id, name, code, crest),
        )
    db.commit()


def test_data_service_initialization(data_service, db, api_client):
    """Data service wires the real database and the external client."""
    assert data_service.db is db
    assert data_service.api_client is api_client


def test_sync_teams_from_standings_persists_metadata(data_service, db, api_client):
    """Standings teams persist with their name, TLA code and crest."""
    api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {"id": 1, "name": "Team A", "tla": "TEA", "crest": "url1"},
                    "position": 1,
                },
                {
                    "team": {"id": 2, "name": "Team B", "tla": "TEB", "crest": "url2"},
                    "position": 2,
                },
            ]
        }]
    }
    api_client.get_competition_matches.return_value = {"matches": []}

    data_service.sync_teams("CL")

    rows = {
        row[0]: row[1:]
        for row in db.fetchall("SELECT id, name, code, crest FROM teams")
    }
    assert rows == {
        1: ("Team A", "TEA", "url1"),
        2: ("Team B", "TEB", "url2"),
    }


def test_sync_teams_from_matches_persists_shortname_fallback(data_service, db, api_client):
    """Teams found only in matches persist, using shortName when tla is absent."""
    api_client.get_competition_standings.return_value = {"standings": []}
    api_client.get_competition_matches.return_value = {
        "matches": [
            {
                "id": 10,
                "homeTeam": {"id": 1, "name": "Home", "shortName": "HOM", "crest": "h.png"},
                "awayTeam": {"id": 2, "name": "Away", "shortName": "AWY", "crest": "a.png"},
            }
        ]
    }

    data_service.sync_teams("CL")

    rows = {
        row[0]: row[1:]
        for row in db.fetchall("SELECT id, name, code, crest FROM teams")
    }
    assert rows == {
        1: ("Home", "HOM", "h.png"),
        2: ("Away", "AWY", "a.png"),
    }


def test_sync_matches_persists_valid_rows_and_skips_missing_ids(data_service, db, api_client):
    """Completed CL matches persist fully; entries with missing ids are skipped."""
    _seed_teams(db, (1, "Home", "HOM", None), (2, "Away", "AWY", None))
    api_client.get_competition_matches.return_value = {
        "matches": [
            {
                "id": 1,
                "homeTeam": {"id": 1, "name": "Home"},
                "awayTeam": {"id": 2, "name": "Away"},
                "score": {"fullTime": {"home": 2, "away": 1}},
                "matchday": 1,
                "utcDate": "2024-01-01T00:00:00Z",
                "status": "FINISHED",
                "stage": "LEAGUE_STAGE",
                "round": {"name": "Matchday 1"},
                "group": {"name": "Group A"},
            },
            # Missing match id -> skipped.
            {
                "id": None,
                "homeTeam": {"id": 1, "name": "Home"},
                "awayTeam": {"id": 2, "name": "Away"},
                "matchday": 2,
                "utcDate": "2024-01-02T00:00:00Z",
            },
            # Missing home team id -> skipped.
            {
                "id": 3,
                "homeTeam": {"name": "Home"},
                "awayTeam": {"id": 2, "name": "Away"},
                "matchday": 3,
                "utcDate": "2024-01-03T00:00:00Z",
            },
        ]
    }

    data_service.sync_matches("CL")

    assert db.fetchone("SELECT COUNT(*) FROM matches")[0] == 1
    row = db.fetchone(
        "SELECT home_team_id, away_team_id, home_score, away_score, matchday, "
        '"date", status, stage, "round", group_name, competition_id '
        "FROM matches WHERE id = 1"
    )
    assert row == (
        1, 2, 2, 1, 1,
        "2024-01-01T00:00:00Z", "FINISHED", "LEAGUE_STAGE",
        "Matchday 1", "Group A", "CL",
    )


def test_sync_standings_persists_snapshot_values(data_service, db, api_client):
    """Standings persist as a competition-keyed snapshot with all values."""
    _seed_teams(db, (1, "Team A", "TEA", "url1"))
    api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {"id": 1, "name": "Team A"},
                    "position": 1,
                    "playedGames": 5,
                    "won": 3,
                    "draw": 1,
                    "lost": 1,
                    "goalsFor": 10,
                    "goalsAgainst": 5,
                    "goalDifference": 5,
                    "points": 10,
                }
            ]
        }]
    }

    data_service.sync_standings("CL")

    row = db.fetchone(
        "SELECT team_id, position, played, won, drawn, lost, goals_for, "
        "goals_against, goal_difference, points, competition_id, last_updated "
        "FROM standings WHERE competition_id = 'CL'"
    )
    assert row[:11] == (1, 1, 5, 3, 1, 1, 10, 5, 5, 10, "CL")
    assert row[11], "last_updated snapshot timestamp must be populated"


def test_sync_all_includes_knockout_without_network(data_service, db, api_client):
    """sync_all stores league + knockout matches and stays on football-data routing."""
    api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {"id": 10, "name": "Alpha", "tla": "ALP", "crest": "a.png"},
                    "position": 1,
                    "playedGames": 1,
                    "won": 1,
                    "draw": 0,
                    "lost": 0,
                    "goalsFor": 2,
                    "goalsAgainst": 0,
                    "goalDifference": 2,
                    "points": 3,
                }
            ]
        }]
    }
    api_client.get_competition_matches.return_value = {
        "matches": [
            {
                "id": 1000,
                "homeTeam": {"id": 10, "name": "Alpha"},
                "awayTeam": {"id": 20, "name": "Beta"},
                "score": {"fullTime": {"home": 1, "away": 0}},
                "matchday": 1,
                "utcDate": "2026-09-15T20:00:00Z",
                "status": "FINISHED",
                "stage": "LEAGUE_STAGE",
                "round": "Matchday 1",
            }
        ]
    }
    api_client.get_competition_matches_by_stage.return_value = {
        "matches": [
            {
                "id": 2000,
                "homeTeam": {"id": 10, "name": "Alpha"},
                "awayTeam": {"id": 20, "name": "Beta"},
                "score": {"fullTime": {"home": 2, "away": 2}},
                "matchday": 9,
                "utcDate": "2027-02-10T20:00:00Z",
                "status": "FINISHED",
                "stage": "KNOCKOUT_OUT",
                "round": {"name": "Round of 16"},
                "group": None,
            }
        ]
    }

    data_service.sync_all("CL")

    api_client.get_competition_matches_by_stage.assert_called_once_with("CL", "KNOCKOUT_OUT")
    assert db.fetchone("SELECT COUNT(*) FROM matches")[0] == 2
    assert db.fetchone("SELECT stage, competition_id FROM matches WHERE id = 2000") == (
        "KNOCKOUT_OUT", "CL"
    )
    assert db.fetchone("SELECT stage FROM matches WHERE id = 1000")[0] == "LEAGUE_STAGE"
    assert db.fetchone(
        "SELECT points FROM standings WHERE competition_id = 'CL' AND team_id = 10"
    ) == (3,)
    # CL must not touch the ESPN/network path.
    assert data_service._espn_service is None
