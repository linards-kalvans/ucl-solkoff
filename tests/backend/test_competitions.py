"""Competition scoping regression tests (Champions/Europa/Conference League).

Covers the shared registry, the optional ``competition`` API parameter, live
standings/coefficient isolation, knockout and season scoping, honest empty
states, the atomic snapshot migration, knockout competition persistence and
scheduler failure isolation.

Everything runs against a real temporary DuckDB (never ``data/*.db``) with a
deterministically patched season window; no lifespan, network or background
work is triggered.
"""
from __future__ import annotations

import importlib
from contextlib import ExitStack, contextmanager
from unittest.mock import Mock, patch

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api_client import APIClient
from backend.competitions import (
    configured_competition,
    competitions_metadata,
    parse_competition_list,
    resolve_competition,
    supported_codes,
)
from backend.data_service import DataService
from backend.database import Database
from backend.main import app
from backend.scheduler import DataScheduler
from backend.solkoff_calculator import SolkoffCalculator

FIXED_SEASON = 2026
FIXED_START = f"{FIXED_SEASON}-08-01"
FIXED_END = f"{FIXED_SEASON + 1}-08-01"


@contextmanager
def fixed_current(year=FIXED_SEASON):
    """Pin the current season everywhere it is read."""
    with ExitStack() as stack:
        for module_name in ("backend.solkoff_calculator", "backend.main"):
            module = importlib.import_module(module_name)
            if hasattr(module, "get_season_bounds"):
                stack.enter_context(
                    patch.object(
                        module, "get_season_bounds",
                        lambda today=None: (FIXED_START, FIXED_END),
                    )
                )
        stack.enter_context(patch("backend.main.current_season_year", lambda: year))
        stack.enter_context(
            patch("backend.season_service.current_season_year", lambda today=None: year)
        )
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "competitions.db"))
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


def add_standing(db, competition_id, team_id, played, points, position=None):
    db.execute(
        """
        INSERT INTO standings (
            competition_id, team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated
        ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?, '2026-09-01T00:00:00Z')
        """,
        (competition_id, team_id, position, played, points),
    )


def add_match(db, match_id, home, away, hs, as_, date, competition_id="CL",
              stage="LEAGUE_STAGE", round_name=None, matchday=1):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'FINISHED', ?, ?, NULL, ?)
        """,
        (match_id, home, away, hs, as_, matchday, date, stage, round_name, competition_id),
    )


def build_two_competitions(db):
    """Same club (1) in CL and EL with distinct opponents and coefficients."""
    for team_id, name in [(1, "Alpha"), (2, "Bravo"), (3, "Charlie")]:
        add_team(db, team_id, name)

    # CL: Alpha beat Bravo; Bravo's CL record is 0 points.
    add_match(db, 101, 1, 2, 2, 0, "2026-09-15T18:00:00Z", competition_id="CL")
    add_standing(db, "CL", 1, played=1, points=3)
    add_standing(db, "CL", 2, played=1, points=0)
    # CL knockout pair for the ROUND_OF_16 view.
    add_match(db, 102, 1, 2, 1, 0, "2027-03-04T20:00:00Z", competition_id="CL",
              stage="KNOCKOUT", round_name="Round of 16", matchday=9)

    # EL: Alpha beat Charlie; Charlie's EL record is 3 points.
    add_match(db, 201, 1, 3, 2, 1, "2026-09-16T18:00:00Z", competition_id="EL")
    add_standing(db, "EL", 1, played=1, points=3)
    add_standing(db, "EL", 3, played=1, points=3)
    add_match(db, 202, 1, 3, 1, 0, "2027-03-05T20:00:00Z", competition_id="EL",
              stage="KNOCKOUT", round_name="Round of 16", matchday=9)

    # A 2024/25 (new-format) CL match so CL seasons include an older season.
    add_match(db, 301, 1, 2, 1, 1, "2024-09-17T20:00:00Z", competition_id="CL")

    db.commit()


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_registry_metadata():
    codes = supported_codes()
    assert codes == ["CL", "EL", "UCL"]
    by_code = {c["code"]: c for c in competitions_metadata()}
    assert by_code["CL"]["leagueMatches"] == 8
    assert by_code["EL"]["leagueMatches"] == 8
    assert by_code["UCL"]["leagueMatches"] == 6
    assert by_code["UCL"]["label"] == "Conference League"


def test_resolve_competition_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("COMPETITION_ID", raising=False)
    assert resolve_competition(None) == "CL"
    assert resolve_competition(" el ") == "EL"
    assert configured_competition() == "CL"
    monkeypatch.setenv("COMPETITION_ID", "el")
    assert configured_competition() == "EL"
    monkeypatch.setenv("COMPETITION_ID", "bogus")
    assert configured_competition() == "CL"
    with pytest.raises(ValueError):
        resolve_competition("XX")


def test_parse_competition_list():
    assert parse_competition_list(None, fallback=["CL"]) == ["CL"]
    assert parse_competition_list("cl,EL,el", fallback=["CL"]) == ["CL", "EL"]
    with pytest.raises(ValueError):
        parse_competition_list("CL,NOPE")


# ---------------------------------------------------------------------------
# API: metadata + scoping
# ---------------------------------------------------------------------------

def test_competitions_endpoint(db):
    build_two_competitions(db)
    with fixed_current(), patch("backend.main.db", db):
        body = TestClient(app).get("/api/competitions").json()

    assert body["default"] == "CL"
    by_code = {c["code"]: c for c in body["competitions"]}
    assert set(by_code) == {"CL", "EL", "UCL"}
    assert by_code["CL"]["hasCurrentData"] is True
    assert by_code["UCL"]["hasCurrentData"] is False


def test_standings_are_competition_scoped(db):
    build_two_competitions(db)
    with fixed_current():
        SolkoffCalculator(db, "CL").calculate_all()
        SolkoffCalculator(db, "EL").calculate_all()
        with patch("backend.main.db", db):
            client = TestClient(app)
            cl = {t["teamId"]: t for t in client.get("/api/standings?competition=CL").json()}
            el = {t["teamId"]: t for t in client.get("/api/standings?competition=EL").json()}

    # Same club 1, distinct coefficient per competition (0.0 vs 3.0).
    assert cl[1]["solkoffCoefficient"] == 0.0
    assert el[1]["solkoffCoefficient"] == 3.0
    # EL standings must not contain CL-only opponent 2.
    assert 2 in cl and 2 not in el
    assert 3 in el and 3 not in cl


def test_el_solkoff_never_reads_cl_standings(db):
    """EL Solkoff must never fall back to CL standings, even with shared team IDs.

    EL has a finished league match for club 1 against club 2, but standings
    exist only for CL, keyed by the SAME team IDs (3.0 PPG). Missing EL rows
    must yield zero, never a cross-competition read.
    """
    add_team(db, 1, "Alpha")
    add_team(db, 2, "Bravo")

    # Finished EL league match: Alpha (1) vs Bravo (2).
    add_match(db, 401, 1, 2, 2, 0, "2026-09-15T18:00:00Z", competition_id="EL")

    # Standings exist ONLY for CL, same team IDs, non-zero (3.0 PPG).
    add_standing(db, "CL", 1, played=1, points=3)
    add_standing(db, "CL", 2, played=1, points=3)
    db.commit()

    with fixed_current():
        el = SolkoffCalculator(db, "EL")
        assert el.get_opponents(1) == {2}
        # No EL rows: zero, not CL's 3.0 fallback.
        assert el.calculate_solkoff(1) == 0.0

        # EL's own row wins over the CL row for the same team ID.
        add_standing(db, "EL", 2, played=1, points=1)
        db.commit()
        assert el.calculate_solkoff(1) == 1.0


def test_default_request_preserves_configured_competition(db, monkeypatch):
    build_two_competitions(db)
    with fixed_current():
        SolkoffCalculator(db, "EL").calculate_all()
        with patch("backend.main.db", db):
            client = TestClient(app)
            monkeypatch.setenv("COMPETITION_ID", "EL")
            default = {t["teamId"]: t for t in client.get("/api/standings").json()}
            monkeypatch.delenv("COMPETITION_ID", raising=False)
            cl = {t["teamId"]: t for t in client.get("/api/standings").json()}

    assert 3 in default  # EL rows
    assert 2 in cl       # CL rows


def test_empty_competition_returns_empty_not_default(db):
    build_two_competitions(db)
    with fixed_current(), patch("backend.main.db", db):
        client = TestClient(app)
        cl = client.get("/api/standings").json()
        el = client.get("/api/standings?competition=UCL").json()

    assert len(cl) > 0
    # UCL has no stored rows: honest empty, never the default competition's rows.
    assert el == []


def test_seasons_are_competition_scoped(db):
    build_two_competitions(db)
    with fixed_current(), patch("backend.main.db", db):
        client = TestClient(app)
        cl = client.get("/api/seasons?competition=CL").json()
        el = client.get("/api/seasons?competition=EL").json()

    assert [s["year"] for s in cl["seasons"]] == [2026, 2024]
    # EL only has the current season (always listed).
    assert [s["year"] for s in el["seasons"]] == [2026]


def test_details_are_competition_scoped(db):
    build_two_competitions(db)
    with fixed_current():
        SolkoffCalculator(db, "CL").calculate_all()
        SolkoffCalculator(db, "EL").calculate_all()
        with patch("backend.main.db", db):
            client = TestClient(app)
            cl = client.get("/api/teams/1/solkoff-details?competition=CL").json()
            el = client.get("/api/teams/1/solkoff-details?competition=EL").json()

    assert {o["teamId"] for o in cl["opponents"]} == {2}
    assert cl["averageOpponentPPG"] == 0.0
    assert {o["teamId"] for o in el["opponents"]} == {3}
    assert el["averageOpponentPPG"] == 3.0
    # EL opponent record comes from EL standings, not CL.
    assert el["opponents"][0]["points"] == 3


def test_invalid_competition_returns_400(db, monkeypatch):
    monkeypatch.setenv("EXTERNAL_API_KEY", "test-key")
    with fixed_current(), patch("backend.main.db", db):
        client = TestClient(app)
        for path in (
            "/api/seasons?competition=XX",
            "/api/standings?competition=XX",
            "/api/teams/1/solkoff-details?competition=XX",
            "/api/tournament/current-stage?competition=XX",
            "/api/playoff-pairs?competition=XX",
            "/api/knockout-pairs/ROUND_OF_16?competition=XX",
            "/api/playoff-pairs/1/2/analysis?competition=XX",
        ):
            assert client.get(path).status_code == 400, path


def test_knockout_pairs_are_competition_scoped(db, monkeypatch):
    build_two_competitions(db)
    monkeypatch.setenv("EXTERNAL_API_KEY", "test-key")
    with fixed_current(), patch("backend.main.db", db):
        client = TestClient(app)
        cl = client.get("/api/knockout-pairs/ROUND_OF_16?competition=CL").json()
        el = client.get("/api/knockout-pairs/ROUND_OF_16?competition=EL").json()

    assert {cl[0]["team1"]["id"], cl[0]["team2"]["id"]} == {1, 2}
    assert {el[0]["team1"]["id"], el[0]["team2"]["id"]} == {1, 3}


def test_archived_details_exclude_future_competition_data(db):
    build_two_competitions(db)
    # Current EL club data must not leak into the archived CL 2024/25 view.
    with fixed_current(), patch("backend.main.db", db):
        client = TestClient(app)
        archived = client.get("/api/teams/1/solkoff-details?season=2024&competition=CL").json()

    assert {o["teamId"] for o in archived["opponents"]} == {2}
    assert archived["opponents"][0]["matches"][0]["date"].startswith("2024-09-17")


# ---------------------------------------------------------------------------
# snapshot migration
# ---------------------------------------------------------------------------

def _legacy_db(db_path):
    """Create a pre-competition-shaped DB (old single-competition schema)."""
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT, crest TEXT)"
    )
    con.execute(
        """CREATE TABLE standings (team_id INTEGER PRIMARY KEY, position INTEGER,
        played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER, goals_for INTEGER,
        goals_against INTEGER, goal_difference INTEGER, points INTEGER,
        last_updated TEXT, FOREIGN KEY (team_id) REFERENCES teams(id))"""
    )
    con.execute(
        """CREATE TABLE solkoff_coefficients (team_id INTEGER PRIMARY KEY,
        solkoff_value INTEGER NOT NULL, calculated_at TEXT NOT NULL,
        FOREIGN KEY (team_id) REFERENCES teams(id))"""
    )
    con.execute("INSERT INTO teams VALUES (1,'Alpha','ALP',NULL),(2,'Bravo','BRA',NULL)")
    con.execute("INSERT INTO standings VALUES (1,1,5,3,1,1,10,5,5,10,'t'),(2,2,5,1,1,3,4,8,-4,4,'t')")
    con.execute("INSERT INTO solkoff_coefficients VALUES (1,3,'t'),(2,2,'t')")
    con.close()


def test_legacy_snapshot_migrates_preserving_values_and_keys(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)

    db = Database(db_path=str(db_path))
    try:
        standing = db.fetchall(
            "SELECT competition_id, team_id, points FROM standings ORDER BY team_id"
        )
        assert standing == [("CL", 1, 10), ("CL", 2, 4)]
        assert db._primary_key_columns("standings") == {"competition_id", "team_id"}
        assert db._primary_key_columns("solkoff_coefficients") == {"competition_id", "team_id"}
        # INTEGER solkoff was upgraded to REAL.
        assert db.fetchone("SELECT typeof(solkoff_value) FROM solkoff_coefficients")[0] == "FLOAT"
        # Foreign key to teams is preserved.
        constraints = db.fetchall(
            "SELECT constraint_type, referenced_table FROM duckdb_constraints() "
            "WHERE table_name = 'standings'"
        )
        assert ("FOREIGN KEY", "teams") in constraints
        # Legacy inserts without competition_id still work and default to CL.
        db.execute(
            "INSERT INTO teams (id, name) VALUES (3, 'Charlie')"
        )
        db.execute(
            """INSERT INTO standings (team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated)
            VALUES (3, 3, 0, 0, 0, 0, 0, 0, 0, 0, 't')"""
        )
        assert db.fetchone("SELECT competition_id FROM standings WHERE team_id = 3")[0] == "CL"
        # Two competitions can coexist for the same club.
        db.execute("UPDATE standings SET points = 9 WHERE competition_id = 'EL' AND team_id = 1")
        db.execute(
            """INSERT INTO standings (competition_id, team_id, position, played, won, drawn,
            lost, goals_for, goals_against, goal_difference, points, last_updated)
            VALUES ('EL', 1, 9, 3, 1, 0, 1, 4, 2, 2, 9, 't')"""
        )
    finally:
        db.close()

    # Reopening is a no-op (idempotent).
    reopened = Database(db_path=str(db_path))
    try:
        assert reopened._primary_key_columns("standings") == {"competition_id", "team_id"}
        assert reopened.fetchone("SELECT COUNT(*) FROM standings")[0] == 4
        assert reopened.fetchone("SELECT COUNT(*) FROM solkoff_coefficients")[0] == 2
    finally:
        reopened.close()


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.db"
    _legacy_db(db_path)

    def boom(self, default_competition):
        raise RuntimeError("migration failure")

    monkeypatch.setattr(Database, "_rebuild_solkoff", boom)

    with pytest.raises(RuntimeError, match="migration failure"):
        Database(db_path=str(db_path))

    # The legacy schema must be intact after a failed migration.
    con = duckdb.connect(str(db_path))
    try:
        columns = {
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'standings'"
            ).fetchall()
        }
        assert "competition_id" not in columns
        assert con.execute("SELECT COUNT(*) FROM standings").fetchone()[0] == 2
    finally:
        con.close()


# ---------------------------------------------------------------------------
# data service + scheduler
# ---------------------------------------------------------------------------

def test_sync_knockout_persists_competition_id(tmp_path):
    db = Database(db_path=str(tmp_path / "knockout.db"))
    try:
        db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1, 'A', NULL, NULL)")
        db.execute("INSERT INTO teams (id, name, code, crest) VALUES (2, 'B', NULL, NULL)")
        db.commit()
        api = Mock(spec=APIClient)
        api.get_competition_matches_by_stage.return_value = {
            "matches": [{
                "id": 55,
                "homeTeam": {"id": 1},
                "awayTeam": {"id": 2},
                "stage": "KNOCKOUT",
                "round": "Round of 16",
                "score": {"fullTime": {"home": 1, "away": 0}},
                "matchday": 9,
                "utcDate": "2027-03-04T20:00:00Z",
                "status": "FINISHED",
            }]
        }
        api.get_competition_matches.return_value = {"matches": []}

        DataService(db, api).sync_knockout_matches("EL")

        row = db.fetchone(
            "SELECT competition_id, home_team_id, away_team_id, home_score, away_score "
            "FROM matches WHERE id = 55"
        )
        assert row == ("EL", 1, 2, 1, 0)
    finally:
        db.close()


def test_scheduler_isolates_competition_failures(db, monkeypatch):
    monkeypatch.delenv("COMPETITIONS", raising=False)
    with patch("backend.scheduler.APIClient"), \
         patch("backend.scheduler.DataService"), \
         patch("backend.scheduler.SolkoffCalculator") as calculator_cls, \
         patch("backend.scheduler.EloCalculator"):
        # Distinct calculator instance per construction call.
        calculator_cls.side_effect = lambda *args, **kwargs: Mock()
        scheduler = DataScheduler(db, "CL", competitions=["CL", "EL"])
        scheduler.data_service = Mock()
        scheduler.elo_calculator = Mock()

        def sync_all(code):
            if code == "EL":
                raise httpx.HTTPStatusError("boom", request=Mock(), response=Mock(status_code=429))

        scheduler.data_service.sync_all.side_effect = sync_all
        scheduler.update_data()

    assert [c.args[0] for c in scheduler.data_service.sync_all.call_args_list] == ["CL", "EL"]
    # CL succeeded and was recalculated; EL failed but did not stop the loop.
    scheduler.calculators["CL"].calculate_all.assert_called_once()
    scheduler.calculators["EL"].calculate_all.assert_not_called()
    # Elo is replayed once after the loop.
    scheduler.elo_calculator.calculate_all.assert_called_once()
