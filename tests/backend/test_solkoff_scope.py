"""Regression tests for current-season Solkoff scope.

These tests pin the corrected scope:
  * opponents come only from FINISHED matches in the configured competition,
    stage ``LEAGUE_STAGE``, and the current season window (UTC Aug 1 inclusive
    through the following Aug 1 exclusive), and
  * historical matches are preserved but never counted.

Everything runs against a real temporary DuckDB and a deterministically
patched season window. No lifespan, network, or background work is triggered.
"""
import importlib
from contextlib import ExitStack, contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.solkoff_calculator as solkoff_calculator
from backend.database import Database
from backend.main import app
from backend.solkoff_calculator import SolkoffCalculator


# Season window used for all behaviour tests: 2026/27 season.
FIXED_START = "2026-08-01"
FIXED_END = "2027-08-01"


@contextmanager
def fixed_season(start=FIXED_START, end=FIXED_END):
    """Deterministically pin the current-season window.

    Patches ``get_season_bounds`` wherever it is referenced (calculator and
    API module). If the planned helper does not exist yet, nothing is patched
    so the underlying behaviour fails loudly instead of erroring at import.
    """
    with ExitStack() as stack:
        for module_name in ("backend.solkoff_calculator", "backend.main"):
            module = importlib.import_module(module_name)
            if hasattr(module, "get_season_bounds"):
                stack.enter_context(
                    patch.object(
                        module,
                        "get_season_bounds",
                        lambda today=None: (start, end),
                    )
                )
        yield


@pytest.fixture
def db(tmp_path):
    """Real temporary DuckDB with the production schema."""
    database = Database(db_path=str(tmp_path / "solkoff_scope.db"))
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


def add_match(
    db,
    match_id,
    home_team_id,
    away_team_id,
    home_score,
    away_score,
    date,
    status="FINISHED",
    stage="LEAGUE_STAGE",
    competition_id="CL",
):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            home_team_id,
            away_team_id,
            home_score,
            away_score,
            1,
            date,
            status,
            stage,
            None,
            None,
            competition_id,
        ),
    )


def add_standings(db, team_id, played, points, won=0, drawn=0, lost=0):
    db.execute(
        """
        INSERT INTO standings (
            team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (team_id, None, played, won, drawn, lost, 0, 0, 0, points, "2026-09-01T00:00:00Z"),
    )


def build_one_round(db):
    """One current-season league round plus every contaminating match type.

    Core result after one round (played = 1):
      Winner (1) beat Loser (2) 2-0  -> Winner opponent PPG 0, Loser 3
      Draw A (3) drew Draw B (4) 1-1 -> each opponent PPG 1
      Idle (5) played nobody         -> 0
    """
    add_team(db, 1, "Winner")
    add_team(db, 2, "Loser")
    add_team(db, 3, "Draw A")
    add_team(db, 4, "Draw B")
    add_team(db, 5, "Idle")

    add_match(db, 101, 1, 2, 2, 0, "2026-09-15T18:00:00Z")
    add_match(db, 102, 3, 4, 1, 1, "2026-09-16T18:00:00Z")

    # Idle has no current-season match, only a prior-season opponent, so its
    # coefficient must be 0 (historical-only opponents never count).
    add_team(db, 30, "Idle Prior Opponent")
    add_match(db, 213, 30, 5, 3, 0, "2025-10-01T18:00:00Z")

    # Contaminant opponents get a non-trivial PPG so inclusion is visible.
    for team_id in range(10, 22):
        add_team(db, team_id, f"Contaminant {team_id}")
        add_standings(db, team_id, played=1, points=3, won=1)

    # prior season, home path then away path
    add_match(db, 201, 1, 10, 1, 0, "2025-09-15T18:00:00Z")
    add_match(db, 202, 11, 1, 0, 1, "2025-09-16T18:00:00Z")
    # future season, home path then away path
    add_match(db, 203, 1, 12, 1, 0, "2027-09-15T18:00:00Z")
    add_match(db, 204, 13, 1, 0, 1, "2027-09-16T18:00:00Z")
    # other competition, home path then away path
    add_match(db, 205, 1, 14, 1, 0, "2026-09-20T18:00:00Z", competition_id="EL")
    add_match(db, 206, 15, 1, 0, 1, "2026-09-21T18:00:00Z", competition_id="EL")
    # knockout stage, home path then away path
    add_match(db, 207, 1, 16, 1, 0, "2026-09-22T18:00:00Z", stage="KNOCKOUT_PLAYOFF")
    add_match(db, 208, 17, 1, 0, 1, "2026-09-23T18:00:00Z", stage="KNOCKOUT_PLAYOFF")
    # unfinished, home path then away path
    add_match(db, 209, 1, 18, None, None, "2026-09-24T18:00:00Z", status="SCHEDULED")
    add_match(db, 210, 19, 1, None, None, "2026-09-25T18:00:00Z", status="SCHEDULED")
    # null stage, home path then away path
    add_match(db, 211, 1, 20, 1, 0, "2026-09-26T18:00:00Z", stage=None)
    add_match(db, 212, 21, 1, 0, 1, "2026-09-27T18:00:00Z", stage=None)

    add_standings(db, 1, played=1, points=3, won=1)
    add_standings(db, 2, played=1, points=0, lost=1)
    add_standings(db, 3, played=1, points=1, drawn=1)
    add_standings(db, 4, played=1, points=1, drawn=1)
    add_standings(db, 5, played=0, points=0)
    add_standings(db, 30, played=1, points=3, won=1)

    db.commit()


def build_single_valid_match(db):
    add_team(db, 1, "Winner")
    add_team(db, 2, "Loser")
    add_match(db, 101, 1, 2, 2, 0, "2026-09-15T18:00:00Z")
    add_standings(db, 1, played=1, points=3, won=1)
    add_standings(db, 2, played=1, points=0, lost=1)
    db.commit()


def build_two_season_opponents(db):
    add_team(db, 1, "Rover")
    add_team(db, 2, "Season One Opponent")
    add_team(db, 3, "Season Two Opponent")
    add_match(db, 401, 1, 2, 1, 0, "2026-09-15T18:00:00Z")
    add_match(db, 402, 1, 3, 1, 0, "2027-09-15T18:00:00Z")
    add_standings(db, 1, played=1, points=3, won=1)
    add_standings(db, 2, played=1, points=0, lost=1)
    add_standings(db, 3, played=1, points=0, lost=1)
    db.commit()


def build_away_side_contamination(db):
    add_team(db, 1, "Away Subject")
    add_team(db, 2, "Away Valid Opponent")
    add_team(db, 3, "Home Contaminant")
    add_team(db, 4, "Away Contaminant")
    # Subject is away, so its only real opponent comes from the away path.
    add_match(db, 501, 2, 1, 0, 1, "2026-09-15T18:00:00Z")
    # Contaminant on the subject's home path (prior season).
    add_match(db, 502, 1, 3, 1, 0, "2025-09-15T18:00:00Z")
    # Contaminant on the subject's away path (other competition).
    add_match(db, 503, 4, 1, 0, 1, "2026-09-20T18:00:00Z", competition_id="EL")
    add_standings(db, 1, played=1, points=3, won=1)
    add_standings(db, 2, played=2, points=6, won=2)
    add_standings(db, 3, played=1, points=3, won=1)
    add_standings(db, 4, played=1, points=3, won=1)
    db.commit()


def build_two_competition_subject(db):
    add_team(db, 1, "Subject")
    add_team(db, 2, "CL Opponent")
    add_team(db, 3, "EL Opponent")
    add_match(db, 601, 1, 2, 1, 0, "2026-09-15T18:00:00Z", competition_id="CL")
    add_match(db, 602, 1, 3, 1, 0, "2026-09-16T18:00:00Z", competition_id="EL")
    add_standings(db, 1, played=1, points=3, won=1)
    add_standings(db, 2, played=1, points=3, won=1)
    add_standings(db, 3, played=1, points=3, won=1)
    db.commit()


# ---------------------------------------------------------------------------
# season bounds helper
# ---------------------------------------------------------------------------

def test_get_season_bounds_august_is_inclusive_boundary():
    helper = solkoff_calculator.get_season_bounds

    assert helper(datetime(2026, 8, 1)) == ("2026-08-01", "2027-08-01")


def test_get_season_bounds_july_is_previous_season():
    helper = solkoff_calculator.get_season_bounds

    assert helper(datetime(2026, 7, 31, 23, 59, 59)) == ("2025-08-01", "2026-08-01")


@pytest.mark.parametrize(
    "today,expected",
    [
        (datetime(2025, 8, 1), ("2025-08-01", "2026-08-01")),
        (datetime(2026, 1, 15), ("2025-08-01", "2026-08-01")),
        (datetime(2026, 7, 31), ("2025-08-01", "2026-08-01")),
        (datetime(2026, 8, 1), ("2026-08-01", "2027-08-01")),
        (datetime(2026, 12, 31), ("2026-08-01", "2027-08-01")),
        (datetime(2027, 7, 31), ("2026-08-01", "2027-08-01")),
        (datetime(2027, 8, 1), ("2027-08-01", "2028-08-01")),
    ],
)
def test_get_season_bounds_window(today, expected):
    helper = solkoff_calculator.get_season_bounds

    bounds = helper(today)

    assert bounds == expected
    assert all(isinstance(value, str) for value in bounds)


# ---------------------------------------------------------------------------
# opponent selection scope
# ---------------------------------------------------------------------------

def test_get_opponents_excludes_all_contaminating_matches(db):
    build_one_round(db)

    with fixed_season():
        opponents = SolkoffCalculator(db).get_opponents(1)

    assert opponents == {2}


@pytest.mark.parametrize(
    "side,status,stage,competition_id,date,label",
    [
        ("home", "FINISHED", "LEAGUE_STAGE", "CL", "2025-09-15T18:00:00Z", "prior-season-home"),
        ("away", "FINISHED", "LEAGUE_STAGE", "CL", "2025-09-15T18:00:00Z", "prior-season-away"),
        ("home", "FINISHED", "LEAGUE_STAGE", "CL", "2027-09-15T18:00:00Z", "future-season-home"),
        ("away", "FINISHED", "LEAGUE_STAGE", "CL", "2027-09-15T18:00:00Z", "future-season-away"),
        ("home", "FINISHED", "LEAGUE_STAGE", "EL", "2026-09-20T18:00:00Z", "other-competition-home"),
        ("away", "FINISHED", "LEAGUE_STAGE", "EL", "2026-09-20T18:00:00Z", "other-competition-away"),
        ("home", "FINISHED", "KNOCKOUT_PLAYOFF", "CL", "2026-09-22T18:00:00Z", "knockout-home"),
        ("away", "FINISHED", "KNOCKOUT_PLAYOFF", "CL", "2026-09-22T18:00:00Z", "knockout-away"),
        ("home", "SCHEDULED", "LEAGUE_STAGE", "CL", "2026-09-24T18:00:00Z", "unfinished-home"),
        ("away", "SCHEDULED", "LEAGUE_STAGE", "CL", "2026-09-24T18:00:00Z", "unfinished-away"),
        ("home", "FINISHED", None, "CL", "2026-09-26T18:00:00Z", "null-stage-home"),
        ("away", "FINISHED", None, "CL", "2026-09-26T18:00:00Z", "null-stage-away"),
    ],
)
def test_contaminating_match_excluded(db, side, status, stage, competition_id, date, label):
    build_single_valid_match(db)
    add_team(db, 99, "Contaminant")
    add_standings(db, 99, played=1, points=3, won=1)
    if side == "home":
        add_match(db, 900, 1, 99, 1, 0, date, status=status, stage=stage, competition_id=competition_id)
    else:
        add_match(db, 900, 99, 1, 0, 1, date, status=status, stage=stage, competition_id=competition_id)
    db.commit()

    with fixed_season():
        opponents = SolkoffCalculator(db).get_opponents(1)

    assert opponents == {2}, f"contaminant {label} leaked into opponents"


def test_away_path_valid_opponent_found(db):
    build_one_round(db)

    with fixed_season():
        opponents = SolkoffCalculator(db).get_opponents(2)

    assert opponents == {1}


def test_season_boundary_iso_timestamps(db):
    add_team(db, 1, "Boundary")
    add_team(db, 50, "JustBefore")
    add_team(db, 51, "ExactlyStart")
    add_team(db, 52, "ExactlyEnd")
    add_team(db, 53, "PlainStartDate")
    add_standings(db, 1, played=1, points=1, drawn=1)
    for team_id in (50, 51, 52, 53):
        add_standings(db, team_id, played=1, points=3, won=1)

    add_match(db, 301, 50, 1, 1, 0, "2026-07-31T23:59:59Z")  # before start -> excluded
    add_match(db, 302, 1, 51, 1, 0, "2026-08-01T00:00:00Z")  # exactly start -> included
    add_match(db, 304, 1, 53, 1, 0, "2026-08-01")            # plain start date -> included
    add_match(db, 303, 1, 52, 1, 0, "2027-08-01T00:00:00Z")  # exactly end -> excluded
    db.commit()

    with fixed_season():
        opponents = SolkoffCalculator(db).get_opponents(1)

    assert opponents == {51, 53}


def test_calculator_respects_competition_id(db):
    build_one_round(db)

    # build_one_round seeds the CL standings only; the EL opponents 14/15 need
    # their own EL rows so the strictly competition-scoped EL lookup has a real
    # PPG to average (otherwise isolation correctly yields 0.0, not a CL leak).
    for team_id in (14, 15):
        db.execute(
            """
            INSERT INTO standings (
                competition_id, team_id, position, played, won, drawn, lost,
                goals_for, goals_against, goal_difference, points, last_updated
            ) VALUES ('EL', ?, NULL, 1, 1, 0, 0, 0, 0, 0, 3, '2026-09-01T00:00:00Z')
            """,
            (team_id,),
        )
    db.commit()

    with fixed_season():
        el_calculator = SolkoffCalculator(db, competition_id="EL")
        opponents = el_calculator.get_opponents(1)
        value = el_calculator.calculate_solkoff(1)

    assert opponents == {14, 15}
    assert value == 3.0


def test_same_instance_uses_season_at_call_time(db):
    build_two_season_opponents(db)
    calculator = SolkoffCalculator(db)

    with fixed_season("2026-08-01", "2027-08-01"):
        first_season = calculator.get_opponents(1)
    with fixed_season("2027-08-01", "2028-08-01"):
        second_season = calculator.get_opponents(1)

    assert first_season == {2}
    assert second_season == {3}
    assert first_season != second_season


# ---------------------------------------------------------------------------
# coefficient arithmetic
# ---------------------------------------------------------------------------

def test_one_round_win_loss_draw_arithmetic(db):
    build_one_round(db)

    with fixed_season():
        calculator = SolkoffCalculator(db)
        assert calculator.calculate_solkoff(1) == 0.0  # winner -> opponent 0 pts
        assert calculator.calculate_solkoff(2) == 3.0  # loser  -> opponent 3 pts
        assert calculator.calculate_solkoff(3) == 1.0  # draw   -> opponent 1 pt
        assert calculator.calculate_solkoff(4) == 1.0


def test_no_current_matches_returns_zero(db):
    build_one_round(db)

    with fixed_season():
        calculator = SolkoffCalculator(db)
        assert calculator.get_opponents(5) == set()  # prior-season opponent ignored
        assert calculator.calculate_solkoff(5) == 0.0


def test_calculate_all_stores_current_season_values(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()

    stored = dict(db.fetchall("SELECT team_id, solkoff_value FROM solkoff_coefficients"))
    assert stored[1] == 0.0
    assert stored[2] == 3.0
    assert stored[3] == 1.0
    assert stored[4] == 1.0
    assert stored[5] == 0.0


# ---------------------------------------------------------------------------
# API standings + breakdown agreement
# ---------------------------------------------------------------------------

def test_api_standings_match_stored_values(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        with patch("backend.main.db", db):
            response = TestClient(app).get("/api/standings")

    assert response.status_code == 200
    standings = {row["teamId"]: row for row in response.json()}
    assert standings[1]["solkoffCoefficient"] == 0.0
    assert standings[2]["solkoffCoefficient"] == 3.0
    assert standings[3]["solkoffCoefficient"] == 1.0
    assert standings[4]["solkoffCoefficient"] == 1.0
    assert standings[5]["solkoffCoefficient"] == 0.0


def test_breakdown_historical_only_team_has_no_current_opponents(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        with patch("backend.main.db", db):
            response = TestClient(app).get("/api/teams/5/solkoff-details")

    assert response.status_code == 200
    details = response.json()
    assert details["solkoffCoefficient"] == 0.0
    assert details["averageOpponentPPG"] == 0.0
    assert details["opponents"] == []
    assert details["opponentsCount"] == 0
    assert details["matchesCount"] == 0


def test_breakdown_matches_stored_value_and_excludes_contamination(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        with patch("backend.main.db", db):
            response = TestClient(app).get("/api/teams/1/solkoff-details")

    assert response.status_code == 200
    details = response.json()
    assert details["solkoffCoefficient"] == 0.0
    assert details["averageOpponentPPG"] == 0.0
    assert details["opponentsCount"] == 1
    assert details["matchesCount"] == 1
    assert {opp["teamId"] for opp in details["opponents"]} == {2}


def test_breakdown_away_path_matches_stored_value(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        with patch("backend.main.db", db):
            response = TestClient(app).get("/api/teams/2/solkoff-details")

    assert response.status_code == 200
    details = response.json()
    assert details["solkoffCoefficient"] == 3.0
    assert details["averageOpponentPPG"] == 3.0
    assert details["opponentsCount"] == 1
    assert details["matchesCount"] == 1
    assert {opp["teamId"] for opp in details["opponents"]} == {1}


def test_breakdown_away_side_excludes_contamination(db):
    build_away_side_contamination(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        with patch("backend.main.db", db):
            response = TestClient(app).get("/api/teams/1/solkoff-details")

    assert response.status_code == 200
    details = response.json()
    assert details["solkoffCoefficient"] == 3.0
    assert details["averageOpponentPPG"] == 3.0
    assert details["opponentsCount"] == 1
    assert details["matchesCount"] == 1
    assert {opp["teamId"] for opp in details["opponents"]} == {2}
    assert details["opponents"][0]["matches"][0]["isHome"] is False


def test_breakdown_respects_competition_id_env(db, monkeypatch):
    build_two_competition_subject(db)

    with fixed_season():
        SolkoffCalculator(db, competition_id="EL").calculate_all()
        with patch("backend.main.db", db):
            client = TestClient(app)
            monkeypatch.setenv("COMPETITION_ID", "EL")
            el_details = client.get("/api/teams/1/solkoff-details").json()
            monkeypatch.delenv("COMPETITION_ID", raising=False)
            cl_details = client.get("/api/teams/1/solkoff-details").json()

    assert el_details["opponentsCount"] == 1
    assert {opp["teamId"] for opp in el_details["opponents"]} == {3}
    assert cl_details["opponentsCount"] == 1
    assert {opp["teamId"] for opp in cl_details["opponents"]} == {2}


def test_stored_standings_and_breakdown_agree(db):
    build_one_round(db)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()
        stored = dict(db.fetchall("SELECT team_id, solkoff_value FROM solkoff_coefficients"))
        with patch("backend.main.db", db):
            client = TestClient(app)
            standings = {row["teamId"]: row for row in client.get("/api/standings").json()}
            breakdown = client.get("/api/teams/1/solkoff-details").json()

    assert stored[1] == standings[1]["solkoffCoefficient"] == breakdown["averageOpponentPPG"] == 0.0
    assert stored[2] == standings[2]["solkoffCoefficient"] == 3.0
    assert breakdown["solkoffCoefficient"] == breakdown["averageOpponentPPG"] == stored[1]


# ---------------------------------------------------------------------------
# historical preservation
# ---------------------------------------------------------------------------

def test_historical_rows_unchanged_after_calculate_all(db):
    build_one_round(db)
    projection = (
        "SELECT id, home_team_id, away_team_id, home_score, away_score, status, "
        "stage, competition_id, \"date\" FROM matches ORDER BY id"
    )
    before = db.fetchall(projection)

    with fixed_season():
        SolkoffCalculator(db).calculate_all()

    after = db.fetchall(projection)
    assert after == before
    assert db.fetchone("SELECT COUNT(*) FROM matches WHERE \"date\" < '2026-08-01'")[0] == 3
    assert db.fetchone("SELECT COUNT(*) FROM matches WHERE \"date\" >= '2027-08-01'")[0] == 2


# ---------------------------------------------------------------------------
# scheduler wiring
# ---------------------------------------------------------------------------

def test_scheduler_passes_competition_to_calculator(db):
    with patch("backend.scheduler.APIClient"), \
         patch("backend.scheduler.DataService"), \
         patch("backend.scheduler.SolkoffCalculator") as mock_calculator_class, \
         patch("backend.scheduler.EloCalculator"):
        from backend.scheduler import DataScheduler

        DataScheduler(db, "UCL")

        args, kwargs = mock_calculator_class.call_args
        assert "UCL" in args or kwargs.get("competition_id") == "UCL"
