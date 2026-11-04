"""Identity-edge coverage for the football-data writers in ``DataService``.

When a live payload still carries a retired provider team id, the writer must
canonicalize it to the reviewed active root while leaving the root's surviving
metadata and the retained retired row exactly as stored. These tests run
against a real temporary DuckDB database and only mock the external API client.
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


def _seed(db, teams, redirects=()):
    """Insert ``(id, name, code, crest)`` teams and ``(retired, root)`` redirects."""
    for team_id, name, code, crest in teams:
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
            (team_id, name, code, crest),
        )
    for retired, canonical in redirects:
        db.execute(
            "INSERT INTO team_redirects (retired_team_id, canonical_team_id) "
            "VALUES (?, ?)",
            (retired, canonical),
        )
    db.commit()


def test_retired_provider_id_reuses_root_and_old_row_untouched(db, api_client):
    """A raw retired provider id updates the root; the retired row is untouched."""
    _seed(
        db,
        [
            (100, "Real Madrid", "RMA", "root.png"),
            (900, "Real Madrid CF", "RMC", "old.png"),
        ],
        [(900, 100)],
    )
    api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {
                        "id": 900,
                        "name": "Real Madrid CF",
                        "tla": "RMC",
                        "crest": "new.png",
                    },
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
    api_client.get_competition_matches.return_value = {"matches": []}

    DataService(db, api_client).sync_teams("CL")

    # Raw retired provider id resolved to the root; established metadata survives
    # the new payload unchanged.
    assert db.fetchone("SELECT name, code, crest FROM teams WHERE id = 100") == (
        "Real Madrid", "RMA", "root.png"
    )
    # The retained retired row keeps its own metadata (never reactivated/rewritten).
    assert db.fetchone("SELECT name, code, crest FROM teams WHERE id = 900") == (
        "Real Madrid CF", "RMC", "old.png"
    )
    # No twin row is created for the retired payload identity.
    assert db.fetchone(
        "SELECT COUNT(*) FROM teams WHERE name = 'Real Madrid CF'"
    )[0] == 1


def test_root_missing_metadata_backfilled_from_raw_retired_payload(db, api_client):
    """Only the root's missing metadata is filled from a retired-id payload."""
    _seed(
        db,
        [
            (100, "Real Madrid", None, None),
            (900, "Retired Alias", "OLD", "old.png"),
        ],
        [(900, 100)],
    )
    api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {
                        "id": 900,
                        "name": "Real Madrid",
                        "tla": "RMA",
                        "crest": "new.png",
                    },
                    "position": 1,
                    "playedGames": 1,
                    "won": 0,
                    "draw": 0,
                    "lost": 0,
                    "goalsFor": 0,
                    "goalsAgainst": 0,
                    "goalDifference": 0,
                    "points": 0,
                }
            ]
        }]
    }
    api_client.get_competition_matches.return_value = {"matches": []}

    DataService(db, api_client).sync_teams("CL")

    # Existing name survives; the root's missing code/crest are backfilled.
    assert db.fetchone("SELECT name, code, crest FROM teams WHERE id = 100") == (
        "Real Madrid", "RMA", "new.png"
    )
    # The retired row's metadata is never rewritten by the incoming payload.
    assert db.fetchone("SELECT code, crest FROM teams WHERE id = 900") == (
        "OLD", "old.png"
    )
