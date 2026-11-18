"""Hermetic integration tests for the FastAPI app.

Each test runs the real application in-process against a seeded temporary
DuckDB database. No live server, scheduler, provider or draw scraper is used,
so the default suite stays offline and deterministic.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.database import Database
from backend.main import app

TEAM_COUNT = 24
REQUIRED_STANDING_FIELDS = [
    "teamId", "teamName", "teamCode", "teamCrest", "position",
    "played", "won", "drawn", "lost", "gf", "ga", "gd", "points",
    "solkoffCoefficient", "strengthScore", "nextFixture",
]


def _season_year():
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def _expected_standings():
    """Deterministic seeded standings keyed by team id.

    Teams 1-3 share 15 points so the endpoint's goal-difference and
    goals-for tie-breaks are exercised. The remaining teams have unique
    points, goal differences and goals-for values.
    """
    rows = {}
    for team_id in range(1, TEAM_COUNT + 1):
        if team_id == 1:
            points, gd, gf = 15, 6, 10
        elif team_id == 2:
            points, gd, gf = 15, 5, 9
        elif team_id == 3:
            points, gd, gf = 15, 4, 9
        else:
            points = max(0, 18 - team_id)
            gd = 30 - team_id
            gf = gd + 10

        played = 10
        won = points // 3
        drawn = points % 3
        lost = played - won - drawn
        ga = gf - gd
        solkoff = round(1.0 + (team_id % 5) * 0.25, 3)
        rows[team_id] = {
            "position": team_id,
            "played": played,
            "won": won,
            "drawn": drawn,
            "lost": lost,
            "gf": gf,
            "ga": ga,
            "gd": gd,
            "points": points,
            "solkoff": solkoff,
            "strengthScore": points * solkoff / (played * 3),
        }
    return rows


def _seed(db):
    expected = _expected_standings()
    season_year = _season_year()

    for team_id, row in expected.items():
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
            (
                team_id,
                f"Team {team_id:02d}",
                f"T{team_id:02d}",
                f"https://example.com/{team_id}.png",
            ),
        )
        db.execute(
            """
            INSERT INTO standings (
                competition_id, team_id, position, played, won, drawn, lost,
                goals_for, goals_against, goal_difference, points, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "CL", team_id, row["position"], row["played"], row["won"],
                row["drawn"], row["lost"], row["gf"], row["ga"], row["gd"],
                row["points"], "2026-11-01T00:00:00Z",
            ),
        )
        db.execute(
            """
            INSERT INTO solkoff_coefficients
                (competition_id, team_id, solkoff_value, calculated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("CL", team_id, row["solkoff"], "2026-11-01T00:00:00Z"),
        )

    # One finished league fixture and one scheduled knockout play-off tie.
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            100, 1, 2, 2, 0, 1, f"{season_year}-09-15T20:00:00Z",
            "FINISHED", "LEAGUE_STAGE", None, None, "CL",
        ),
    )
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            200, 1, 2, None, None, 7, f"{season_year + 1}-02-17T20:00:00Z",
            "SCHEDULED", "KNOCKOUT_PLAYOFF", "Play-off Round", None, "CL",
        ),
    )
    db.commit()
    return expected


@pytest.fixture
def seeded_db(tmp_path):
    database = Database(str(tmp_path / "integration.db"))
    expected = _seed(database)
    try:
        yield database, expected
    finally:
        database.close()


@pytest.fixture
def db(seeded_db):
    return seeded_db[0]


@pytest.fixture
def expected(seeded_db):
    return seeded_db[1]


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("COMPETITION_ID", "CL")
    """In-process app bound to the seeded DB, with no external dependencies."""
    monkeypatch.setattr("backend.main.db", db)
    monkeypatch.setattr("backend.main.scheduler", None)
    # Empty stages would otherwise try the live UEFA draw scraper.
    monkeypatch.setattr(
        "backend.uefa_draws_scraper.UEFADrawsScraper.get_knockout_draws",
        lambda self: [],
    )
    return TestClient(app)


def test_standings_endpoint_returns_data(client, expected):
    """Standings expose the full live response shape and seeded values."""
    response = client.get("/api/standings")

    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == TEAM_COUNT

    first = data[0]
    for field in REQUIRED_STANDING_FIELDS:
        assert field in first, f"Missing field: {field}"

    assert first["teamId"] == 1
    assert first["teamName"] == "Team 01"
    assert first["teamCode"] == "T01"
    assert first["teamCrest"] == "https://example.com/1.png"
    assert first["points"] == expected[1]["points"]
    assert first["solkoffCoefficient"] == pytest.approx(expected[1]["solkoff"])
    assert first["strengthScore"] == pytest.approx(expected[1]["strengthScore"])
    assert first["nextFixture"] is None

    assert isinstance(first["teamId"], int)
    assert isinstance(first["teamName"], str)
    assert isinstance(first["points"], int)
    assert isinstance(first["solkoffCoefficient"], float)
    assert isinstance(first["strengthScore"], float)


def test_standings_endpoint_data_quality(client, expected):
    """Rows are ordered by points, goal difference and goals for."""
    response = client.get("/api/standings")
    assert response.status_code == 200, response.text

    data = response.json()

    keys = [(row["points"], row["gd"], row["gf"]) for row in data]
    assert keys == sorted(keys, reverse=True)

    for row in data:
        exp = expected[row["teamId"]]
        assert (
            row["played"], row["won"], row["drawn"], row["lost"],
            row["gf"], row["ga"], row["gd"], row["points"],
        ) == (
            exp["played"], exp["won"], exp["drawn"], exp["lost"],
            exp["gf"], exp["ga"], exp["gd"], exp["points"],
        )
        assert row["solkoffCoefficient"] == pytest.approx(exp["solkoff"])
        assert row["strengthScore"] == pytest.approx(exp["strengthScore"])


def test_standings_endpoint_has_all_teams(client, expected):
    """Every seeded club appears exactly once."""
    response = client.get("/api/standings")
    assert response.status_code == 200, response.text

    api_team_ids = {team["teamId"] for team in response.json()}

    assert len(api_team_ids) == TEAM_COUNT
    assert api_team_ids == set(expected)


def test_health_endpoint(client):
    """Health reports the real patched database state."""
    response = client.get("/api/health")
    assert response.status_code == 200

    assert response.json() == {"status": "healthy", "database": True}


def test_current_stage_endpoint(client):
    """The seeded knockout tie determines the current stage."""
    response = client.get("/api/tournament/current-stage")
    assert response.status_code == 200, response.text

    assert response.json() == {"stage": "KNOCKOUT_PLAYOFF"}


def test_knockout_pairs_endpoints(client):
    """The seeded play-off pair is returned; other stages are empty."""
    stages = ["KNOCKOUT_PLAYOFF", "ROUND_OF_16", "QUARTER_FINAL", "SEMI_FINAL", "FINAL"]

    for stage in stages:
        response = client.get(f"/api/knockout-pairs/{stage}")
        assert response.status_code == 200, f"{stage}: {response.text}"

        data = response.json()
        assert isinstance(data, list), f"{stage}: expected list"

        if stage == "KNOCKOUT_PLAYOFF":
            assert len(data) == 1
            pair = data[0]
            assert pair["stage"] == "Knockout Round Play-off"
            assert {pair["team1"]["id"], pair["team2"]["id"]} == {1, 2}
            assert pair["team1"]["name"] == "Team 01"
            assert pair["team2"]["name"] == "Team 02"
        else:
            assert data == []
