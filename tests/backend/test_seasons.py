"""Season-switching regression tests.

Covers new-format season discovery (2024/25 onward), validation, historical
league-table/Solkoff reconstruction, per-season scoping of details and knockout
ties, and that selecting a season performs no writes. Runs against a real
temporary DuckDB with a deterministically patched current season; no lifespan,
network, or background work is triggered.
"""
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.season_service as season_service
from backend.database import Database
from backend.main import app
from backend.playoff_analyzer import PlayoffAnalyzer
from backend.season_service import (
    build_breakdown,
    build_standings,
    current_season_year,
    discover_seasons,
    is_supported_year,
    season_bounds,
    season_label,
)

FIXED_CURRENT = 2026


@contextmanager
def fixed_current(year=FIXED_CURRENT):
    """Pin the resolved current season in every module that reads it."""
    with patch.object(season_service, "current_season_year", lambda today=None: year), \
         patch("backend.main.current_season_year", lambda: year):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "seasons.db"))
    yield database
    database.close()


# ---------------------------------------------------------------------------
# insertion helpers
# ---------------------------------------------------------------------------

def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", f"https://crest.example/{team_id}.png"),
    )


def add_match(db, match_id, home, away, hs, as_, date, status="FINISHED",
              stage="LEAGUE_STAGE", round_name=None, matchday=1, competition_id="CL"):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, home, away, hs, as_, matchday, date, status, stage,
         round_name, None, competition_id),
    )


def add_standing(db, team_id, played, points, position=None):
    db.execute(
        """
        INSERT INTO standings (
            team_id, position, played, won, drawn, lost,
            goals_for, goals_against, goal_difference, points, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (team_id, position, played, 0, 0, 0, 0, 0, 0, points, "2026-09-01T00:00:00Z"),
    )


def build_two_seasons(db):
    """Two new-format seasons sharing teams, plus old-format/other-comp noise."""
    for team_id, name in [(1, "Alpha"), (2, "Bravo"), (3, "Charlie"),
                          (4, "Delta"), (5, "Echo")]:
        add_team(db, team_id, name)

    # 2024/25 league results.
    add_match(db, 101, 1, 2, 2, 0, "2024-09-17T20:00:00Z")
    add_match(db, 102, 3, 4, 1, 1, "2024-09-18T20:00:00Z")
    # 2024/25 knockout tie (old date: must survive the age filter for the season).
    add_match(db, 103, 1, 2, 1, 0, "2025-03-04T20:00:00Z",
              stage="KNOCKOUT", round_name="Round of 16", matchday=9)
    # Other competition in the same window (never counted for CL).
    add_match(db, 104, 1, 5, 1, 0, "2024-09-20T20:00:00Z", competition_id="EL")

    # 2025/26 league results.
    add_match(db, 201, 1, 2, 0, 1, "2025-09-17T20:00:00Z")
    add_match(db, 202, 3, 4, 3, 0, "2025-09-18T20:00:00Z")
    add_match(db, 203, 3, 4, 2, 0, "2026-03-04T20:00:00Z",
              stage="KNOCKOUT", round_name="Round of 16", matchday=9)

    # Pre-2024 (old-format) match: never discovered nor counted.
    add_match(db, 301, 1, 2, 1, 0, "2023-09-17T20:00:00Z")

    db.commit()


# ---------------------------------------------------------------------------
# current-season resolution and helpers
# ---------------------------------------------------------------------------

def test_current_season_year_august_boundary():
    from datetime import datetime

    assert current_season_year(datetime(2024, 8, 1)) == 2024
    assert current_season_year(datetime(2025, 7, 31)) == 2024
    assert current_season_year(datetime(2026, 8, 1)) == 2026


def test_season_helpers():
    assert season_bounds(2024) == ("2024-08-01", "2025-08-01")
    assert season_label(2024) == "2024/25"
    assert season_label(2025) == "2025/26"


def test_supported_year_bounds():
    with fixed_current(2026):
        assert is_supported_year(2023) is False
        assert is_supported_year(2024) is True
        assert is_supported_year(2026) is True
        assert is_supported_year(2027) is False
        assert is_supported_year(None) is False
        assert is_supported_year("2024") is False


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def test_discovery_newest_first_and_includes_empty_current(db):
    build_two_seasons(db)

    with fixed_current(2026):
        seasons = discover_seasons(db, "CL")

    years = [s["year"] for s in seasons]
    assert years == [2026, 2025, 2024]  # current first even with no matches
    assert seasons[0]["label"] == "2026/27"
    assert 2023 not in years  # pre-new-format excluded


def test_discovery_is_competition_scoped(db):
    build_two_seasons(db)

    with fixed_current(2026):
        cl = [s["year"] for s in discover_seasons(db, "CL")]
        el = [s["year"] for s in discover_seasons(db, "EL")]

    assert cl == [2026, 2025, 2024]
    assert el == [2026, 2024]  # only 2024 has an EL match; 2025 absent


# ---------------------------------------------------------------------------
# historical reconstruction
# ---------------------------------------------------------------------------

def test_historical_standings_use_results_and_points(db):
    build_two_seasons(db)

    table = build_standings(db, 2024, "CL")
    by_id = {t["teamId"]: t for t in table}

    assert [t["teamName"] for t in table] == ["Alpha", "Charlie", "Delta", "Bravo"]
    assert by_id[1]["points"] == 3 and by_id[1]["played"] == 1
    assert by_id[1]["gd"] == 2 and by_id[1]["gf"] == 2
    assert by_id[2]["points"] == 0
    # Unique-opponent mean PPG: Alpha beat Bravo (0 PPG) -> 0.0.
    assert by_id[1]["solkoffCoefficient"] == 0.0
    # Bravo's only opponent is Alpha (3 PPG).
    assert by_id[2]["solkoffCoefficient"] == 3.0
    assert by_id[3]["solkoffCoefficient"] == 1.0
    assert all(t["position"] for t in table)


def test_two_seasons_sharing_teams_are_distinct(db):
    build_two_seasons(db)

    t2024 = {t["teamId"]: t for t in build_standings(db, 2024, "CL")}
    t2025 = {t["teamId"]: t for t in build_standings(db, 2025, "CL")}

    assert t2024[1]["points"] == 3 and t2024[1]["solkoffCoefficient"] == 0.0
    assert t2025[1]["points"] == 0 and t2025[1]["solkoffCoefficient"] == 3.0
    assert t2024[3]["solkoffCoefficient"] != t2025[3]["solkoffCoefficient"]


def test_breakdown_is_season_scoped(db):
    build_two_seasons(db)

    b2024 = build_breakdown(db, 1, 2024, "CL")
    b2025 = build_breakdown(db, 1, 2025, "CL")

    assert {o["teamId"] for o in b2024["opponents"]} == {2}
    assert b2024["solkoffCoefficient"] == 0.0
    assert b2024["matchesCount"] == 1
    assert b2024["opponents"][0]["matches"][0]["date"].startswith("2024-09-17")

    assert {o["teamId"] for o in b2025["opponents"]} == {2}
    assert b2025["solkoffCoefficient"] == 3.0
    assert b2025["opponents"][0]["matches"][0]["date"].startswith("2025-09-17")


def test_legacy_league_rows_reconstructed_and_knockout_excluded(db):
    build_legacy_season(db)

    table = build_standings(db, 2024, "CL")
    by_id = {t["teamId"]: t for t in table}

    # Legacy NULL-tag league rows are counted; play-off/knockout results are not.
    assert set(by_id) == {1, 2, 3, 4}
    assert by_id[1]["played"] == 1 and by_id[1]["points"] == 3
    assert by_id[3]["played"] == 1 and by_id[3]["points"] == 1
    # Echo/Foxtrot only played each other in the play-off tie, never the league.
    assert 5 not in by_id and 6 not in by_id


def test_legacy_breakdown_scoped_to_league(db):
    build_legacy_season(db)

    details = build_breakdown(db, 3, 2024, "CL")
    assert {o["teamId"] for o in details["opponents"]} == {4}
    assert details["matchesCount"] == 1
    assert details["opponents"][0]["matches"][0]["date"].startswith("2024-09-18")


def test_legacy_playoff_tie_reachable_under_playoff_stage(db):
    build_legacy_season(db)

    with fixed_current(2026):
        analyzer = PlayoffAnalyzer(db, None, competition_id="CL", season_start_year=2024)
        playoff = analyzer.get_pairs_by_stage("KNOCKOUT_PLAYOFF", "CL")
        r16 = analyzer.get_pairs_by_stage("ROUND_OF_16", "CL")
        all_pairs = analyzer.get_playoff_pairs("CL")

    assert len(playoff) == 1
    assert {playoff[0]["team1"]["id"], playoff[0]["team2"]["id"]} == {5, 6}
    assert playoff[0]["date"] is not None
    # The legacy league pair (Alpha vs Bravo) is not mislabelled as a play-off.
    assert {playoff[0]["team1"]["id"], playoff[0]["team2"]["id"]} != {1, 2}

    assert len(r16) == 1
    assert {r16[0]["team1"]["id"], r16[0]["team2"]["id"]} == {1, 2}

    # No 500 / no wrong tabs from NULL-matchday legacy rows.
    assert {p["stage"] for p in all_pairs} == {"Knockout Round Play-off", "Round of 16"}


def test_legacy_api_standings_and_details_not_empty(db):
    build_legacy_season(db)

    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        standings = client.get("/api/standings?season=2024").json()
        details = client.get("/api/teams/1/solkoff-details?season=2024").json()

    assert {t["teamId"] for t in standings} == {1, 2, 3, 4}
    assert next(t for t in standings if t["teamId"] == 1)["played"] == 1
    assert {o["teamId"] for o in details["opponents"]} == {2}
    assert details["matchesCount"] == 1


def test_empty_valid_season_returns_empty_state(db):
    build_two_seasons(db)

    assert build_standings(db, 2026, "CL") == []
    empty = build_breakdown(db, 1, 2026, "CL")
    assert empty is not None
    assert empty["opponents"] == []
    assert empty["opponentsCount"] == 0


# ---------------------------------------------------------------------------
# historical knockout scoping (via analyzer)
# ---------------------------------------------------------------------------

def test_historical_knockout_keeps_old_dates_and_excludes_other_seasons(db):
    build_two_seasons(db)

    with fixed_current(2026):
        a2024 = PlayoffAnalyzer(db, None, competition_id="CL", season_start_year=2024)
        pairs2024 = a2024.get_pairs_by_stage("ROUND_OF_16", "CL")
        a2025 = PlayoffAnalyzer(db, None, competition_id="CL", season_start_year=2025)
        pairs2025 = a2025.get_pairs_by_stage("ROUND_OF_16", "CL")

    assert len(pairs2024) == 1
    assert {pairs2024[0]["team1"]["id"], pairs2024[0]["team2"]["id"]} == {1, 2}
    assert pairs2024[0]["date"] is not None  # old date survives the age filter
    assert pairs2024[0]["winProbability"]["method"] == "historical_no_data"

    assert len(pairs2025) == 1
    assert {pairs2025[0]["team1"]["id"], pairs2025[0]["team2"]["id"]} == {3, 4}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_api_seasons_endpoint(db):
    build_two_seasons(db)

    with fixed_current(2026), patch("backend.main.db", db):
        response = TestClient(app).get("/api/seasons")

    assert response.status_code == 200
    body = response.json()
    assert body["currentSeason"] == 2026
    assert [s["year"] for s in body["seasons"]] == [2026, 2025, 2024]


def test_api_standings_default_current_vs_selected_season(db):
    build_two_seasons(db)
    add_standing(db, 1, played=2, points=6, position=1)
    db.commit()

    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        default = client.get("/api/standings").json()
        s2024 = client.get("/api/standings?season=2024").json()
        s2025 = client.get("/api/standings?season=2025").json()

    # Default still uses the stored current-season standings table.
    assert {t["teamId"] for t in default} == {1}
    assert default[0]["points"] == 6

    by24 = {t["teamId"]: t for t in s2024}
    by25 = {t["teamId"]: t for t in s2025}
    assert by24[1]["points"] == 3
    assert by25[1]["points"] == 0
    assert by24[1]["points"] != by25[1]["points"]


def test_api_details_season_scoped(db):
    build_two_seasons(db)

    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        d2024 = client.get("/api/teams/1/solkoff-details?season=2024").json()
        d2025 = client.get("/api/teams/1/solkoff-details?season=2025").json()

    assert {o["teamId"] for o in d2024["opponents"]} == {2}
    assert d2024["solkoffCoefficient"] == 0.0
    assert d2025["solkoffCoefficient"] == 3.0


def test_api_unsupported_season_rejected(db, monkeypatch):
    build_two_seasons(db)
    monkeypatch.setenv("EXTERNAL_API_KEY", "test-key")
    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        assert client.get("/api/standings?season=2023").status_code == 400
        assert client.get("/api/standings?season=2027").status_code == 400
        assert client.get("/api/teams/1/solkoff-details?season=1999").status_code == 400
        assert client.get("/api/knockout-pairs/ROUND_OF_16?season=2023").status_code == 400


def test_api_historical_knockout_old_dates(db, monkeypatch):
    build_two_seasons(db)
    monkeypatch.setenv("EXTERNAL_API_KEY", "test-key")
    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        r2024 = client.get("/api/knockout-pairs/ROUND_OF_16?season=2024")
        r2025 = client.get("/api/knockout-pairs/ROUND_OF_16?season=2025")

    assert r2024.status_code == 200
    p2024 = r2024.json()
    assert len(p2024) == 1
    assert {p2024[0]["team1"]["id"], p2024[0]["team2"]["id"]} == {1, 2}
    assert p2024[0]["date"] is not None

    assert r2025.status_code == 200
    p2025 = r2025.json()
    assert {p2025[0]["team1"]["id"], p2025[0]["team2"]["id"]} == {3, 4}


def test_parser_tags_new_format_league_and_knockout():
    from backend.football_txt_parser import FootballTxtParser

    content = (
        "= UEFA Champions League 2024/25\n"
        "\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    18:45  BSC Young Boys (SUI)  v Aston Villa FC (ENG)  0-3 (0-2)\n"
        "\n"
        "▪ Playoffs, Matchday 1\n"
        "  Tue Feb 11\n"
        "    21:00  Manchester City FC (ENG) v Real Madrid CF (ESP)  2-3 (1-0)\n"
        "\n"
        "▪ Finals, Round of 16\n"
        "  Tue Mar 4\n"
        "    21:00  Real Madrid CF (ESP) v Club Atletico de Madrid (ESP)  2-1 (1-1)\n"
    )
    parsed = FootballTxtParser().parse(content, competition="CL", season="2024-25")
    stages = {m["stage"] for m in parsed["matches"]}

    # New-format headers must map to real league/playoff stage tags, otherwise
    # historical league reconstruction would find no matches.
    assert "LEAGUE_STAGE" in stages
    assert "KNOCKOUT_PLAYOFF" in stages
    assert "KNOCKOUT" in stages
    league = [m for m in parsed["matches"] if m["stage"] == "LEAGUE_STAGE"][0]
    assert league["date"] == "2024-09-17"


def build_legacy_season(db):
    """A 2024/25 season stored in the pre-parser-fix shape.

    League-phase and play-off ties both have NULL stage/round/group/matchday;
    only the date (Sep/Jan vs Feb) separates them. Knockout rounds carry the
    old parser's stage/round/matchday tags.
    """
    for team_id, name in [(1, "Alpha"), (2, "Bravo"), (3, "Charlie"),
                          (4, "Delta"), (5, "Echo"), (6, "Foxtrot")]:
        add_team(db, team_id, name)

    # Legacy league phase (NULL tags), before the February boundary.
    add_legacy_match(db, 101, 1, 2, 2, 0, "2024-09-17T20:00:00Z")
    add_legacy_match(db, 102, 3, 4, 1, 1, "2024-09-18T20:00:00Z")
    # Legacy play-off tie (also NULL tags) at/after the February boundary.
    add_legacy_match(db, 201, 5, 6, 1, 0, "2025-02-11T20:00:00Z")
    add_legacy_match(db, 202, 6, 5, 0, 0, "2025-02-18T20:00:00Z")
    # Knockout rounds keep the old parser tags.
    add_match(db, 301, 1, 2, 1, 0, "2025-03-04T20:00:00Z",
              stage="KNOCKOUT", round_name="Round of 16", matchday=7)
    db.commit()


def add_legacy_match(db, match_id, home, away, hs, as_, date, competition_id="CL"):
    """Insert a row in the legacy stored shape: NULL stage/round/group/matchday,
    exactly as new-format matches were imported before the parser fix."""
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, 'FINISHED', NULL, NULL, NULL, ?)
        """,
        (match_id, home, away, hs, as_, date, competition_id),
    )


def test_parser_quarterfinal_semifinal_headings_case_insensitive():
    from backend.football_txt_parser import FootballTxtParser

    # One-word case variants must map to the right round; otherwise the previous
    # round (e.g. "Round of 16") is carried over and ties land on the wrong tab.
    for heading, expected in [
        ("▪ Finals, Quarterfinals", "Quarter-finals"),
        ("quarterfinals", "Quarter-finals"),
        ("SEMIFINALS", "Semi-finals"),
        ("▪ Finals, Semi-finals", "Semi-finals"),
        ("FINALS - FINAL", "Final"),
    ]:
        parser = FootballTxtParser()
        parser.current_stage = None
        parser.current_round = "Round of 16"  # stale round from earlier heading
        assert parser._is_knockout_heading(heading), heading
        assert parser._extract_round_name(heading) == expected, heading


def test_get_current_stage_legacy_league_only_is_league(db):
    add_team(db, 1, "Alpha")
    add_team(db, 2, "Bravo")
    add_legacy_match(db, 101, 1, 2, 2, 0, "2024-09-17T20:00:00Z")
    add_legacy_match(db, 102, 2, 1, 1, 1, "2025-01-29T20:00:00Z")
    db.commit()

    with fixed_current(2026):
        stage = PlayoffAnalyzer(db, None, competition_id="CL",
                                season_start_year=2024).get_current_stage("CL")

    assert stage == "LEAGUE"


def test_get_current_stage_legacy_playoff_only_is_knockout_playoff(db):
    add_team(db, 5, "Echo")
    add_team(db, 6, "Foxtrot")
    add_legacy_match(db, 201, 5, 6, 1, 0, "2025-02-11T20:00:00Z")
    add_legacy_match(db, 202, 6, 5, 0, 0, "2025-02-18T20:00:00Z")
    db.commit()

    # Fully NULL-tagged legacy rows must not raise (NULL matchday) and must be
    # placed by the February boundary.
    with fixed_current(2026):
        stage = PlayoffAnalyzer(db, None, competition_id="CL",
                                season_start_year=2024).get_current_stage("CL")

    assert stage == "KNOCKOUT_PLAYOFF"


def test_get_current_stage_explicit_latest_round_wins(db):
    add_team(db, 1, "Alpha")
    add_team(db, 2, "Bravo")
    add_legacy_match(db, 101, 1, 2, 2, 0, "2024-09-17T20:00:00Z")
    add_match(db, 301, 1, 2, 1, 0, "2025-03-04T20:00:00Z",
              stage="KNOCKOUT", round_name="Round of 16", matchday=7)
    add_match(db, 302, 1, 2, 2, 1, "2025-05-31T20:00:00Z",
              stage="KNOCKOUT", round_name="Final", matchday=10)
    db.commit()

    with fixed_current(2026):
        stage = PlayoffAnalyzer(db, None, competition_id="CL",
                                season_start_year=2024).get_current_stage("CL")

    assert stage == "FINAL"


def test_api_current_stage_legacy_season(db, monkeypatch):
    build_legacy_season(db)
    monkeypatch.setenv("EXTERNAL_API_KEY", "test-key")

    with fixed_current(2026), patch("backend.main.db", db):
        response = TestClient(app).get("/api/tournament/current-stage?season=2024")

    assert response.status_code == 200
    # build_legacy_season has a legacy play-off tie (Feb) and an explicit
    # Round of 16 (Mar); the latest explicit stage wins.
    assert response.json()["stage"] == "ROUND_OF_16"


def test_season_selection_performs_no_writes_or_network(db):
    build_two_seasons(db)
    add_standing(db, 1, played=2, points=6, position=1)
    db.commit()

    matches_before = db.fetchall(
        'SELECT id, home_score, away_score, status, stage, "date", competition_id '
        "FROM matches ORDER BY id"
    )
    standings_before = db.fetchall("SELECT * FROM standings ORDER BY team_id")

    with fixed_current(2026), patch("backend.main.db", db):
        client = TestClient(app)
        client.get("/api/standings?season=2024")
        client.get("/api/standings?season=2025")
        client.get("/api/teams/1/solkoff-details?season=2024")
        client.get("/api/seasons")

    matches_after = db.fetchall(
        'SELECT id, home_score, away_score, status, stage, "date", competition_id '
        "FROM matches ORDER BY id"
    )
    standings_after = db.fetchall("SELECT * FROM standings ORDER BY team_id")
    assert matches_after == matches_before
    assert standings_after == standings_before
