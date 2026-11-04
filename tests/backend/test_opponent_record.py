"""Opponent season W/D/L in Solkoff details (live + historical).

The opponent record must come from that opponent's own league-phase standings
row, never from the head-to-head result, and a missing standings row must
surface as ``None`` instead of a fabricated zero. Runs against a real temporary
DuckDB with a deterministically patched season window; no lifespan or network.
"""
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.season_service as season_service
from backend.database import Database
from backend.main import app
from backend.season_service import build_breakdown

CURRENT = 2026
WINDOW = ("2026-08-01", "2027-08-01")


@contextmanager
def fixed_season(current=CURRENT, window=WINDOW):
    with patch.object(season_service, "current_season_year", lambda today=None: current), \
         patch("backend.main.current_season_year", lambda: current), \
         patch("backend.main.get_season_bounds", lambda today=None: window):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "opponent_record.db"))
    yield database
    database.close()


def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", None),
    )


def add_match(db, match_id, home, away, hs, as_, date, status="FINISHED",
              stage="LEAGUE_STAGE", competition_id="CL"):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, home, away, hs, as_, 1, date, status, stage, None, None,
         competition_id),
    )


def add_standings(db, team_id, played, points, won=0, drawn=0, lost=0):
    db.execute(
        """
        INSERT INTO standings (
            team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (team_id, None, played, won, drawn, lost, 0, 0, 0, points,
         "2026-09-01T00:00:00Z"),
    )


# ---------------------------------------------------------------------------
# live current-season path
# ---------------------------------------------------------------------------

def build_live_subject(db):
    """Subject (1) plays one home and one away league match.

    Home: 1 beat 2 (2-0). Away: 3 beat 1 (1-0). The opponents' own standings
    records deliberately invert the head-to-head results.
    """
    add_team(db, 1, "Subject")
    add_team(db, 2, "Home Opponent")
    add_team(db, 3, "Away Opponent")
    add_match(db, 101, 1, 2, 2, 0, "2026-09-15T18:00:00Z")
    add_match(db, 102, 3, 1, 1, 0, "2026-09-16T18:00:00Z")
    add_standings(db, 1, played=2, points=3, won=1, lost=1)
    add_standings(db, 2, played=1, points=0, lost=1)
    add_standings(db, 3, played=1, points=3, won=1)
    db.commit()


def _details(db, team_id):
    with fixed_season(), patch("backend.main.db", db):
        response = TestClient(app).get(f"/api/teams/{team_id}/solkoff-details")
    assert response.status_code == 200
    return response.json()


def test_live_home_and_away_opponents_report_season_record(db):
    build_live_subject(db)
    details = _details(db, 1)
    by_id = {opp["teamId"]: opp for opp in details["opponents"]}

    # Home opponent: lost its league match -> 0W 0D 1L, not the H2H win.
    assert (by_id[2]["won"], by_id[2]["draw"], by_id[2]["lost"]) == (0, 0, 1)
    assert by_id[2]["matches"][0]["outcome"] == "win"
    # Away opponent: won its league match -> 1W 0D 0L, not the H2H loss.
    assert (by_id[3]["won"], by_id[3]["draw"], by_id[3]["lost"]) == (1, 0, 0)
    assert by_id[3]["matches"][0]["outcome"] == "loss"


def test_live_opponent_without_standings_row_is_unavailable(db):
    add_team(db, 1, "Subject")
    add_team(db, 2, "Unranked Opponent")
    add_match(db, 101, 1, 2, 2, 0, "2026-09-15T18:00:00Z")
    add_standings(db, 1, played=1, points=3, won=1)
    db.commit()

    details = _details(db, 1)
    assert details["opponentsCount"] == 1
    opp = details["opponents"][0]
    assert (opp["won"], opp["draw"], opp["lost"]) == (None, None, None)


# ---------------------------------------------------------------------------
# historical reconstruction path
# ---------------------------------------------------------------------------

def build_historical(db):
    add_team(db, 1, "Alpha")
    add_team(db, 2, "Bravo")
    add_match(db, 101, 1, 2, 2, 0, "2024-09-17T20:00:00Z")
    db.commit()


def test_historical_opponent_reports_reconstructed_record(db):
    build_historical(db)
    breakdown = build_breakdown(db, 1, 2024, "CL")

    opp = breakdown["opponents"][0]
    assert opp["teamId"] == 2
    # Bravo lost its only league match -> 0W 0D 1L, despite Alpha's H2H win.
    assert (opp["won"], opp["draw"], opp["lost"]) == (0, 0, 1)
    assert opp["matches"][0]["outcome"] == "win"


def test_historical_opponent_keeps_real_zeroes(db):
    # Bravo draws its only match: a real 0W 1D 0L record, not unavailable.
    add_team(db, 1, "Alpha")
    add_team(db, 2, "Bravo")
    add_match(db, 101, 1, 2, 1, 1, "2024-09-17T20:00:00Z")
    db.commit()

    opp = build_breakdown(db, 1, 2024, "CL")["opponents"][0]
    assert (opp["won"], opp["draw"], opp["lost"]) == (0, 1, 0)
