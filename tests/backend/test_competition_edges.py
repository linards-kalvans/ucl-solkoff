"""Multi-competition edge-case regressions.

Focused, independent coverage for the historical/multi-competition path that
the main ``test_competitions.py`` suite does not exercise:

1. Real openfootball EL/UCL ``▪ League phase`` headers (no explicit matchday)
   must parse as ``LEAGUE_STAGE``.
2. A GitHub season that parses matches but inserts none must report failure so
   the caller can fall back to the bounded API season fetch (never silent
   success).
3. A legacy ``matches`` table without ``competition_id`` keeps its rows as
   ``NULL`` on migration; unknown provenance is never guessed as ``CL``.
4. Startup historical presence is per competition: EL/UCL are not skipped just
   because CL historical data already exists.

Everything runs against a real temporary DuckDB, with the GitHub fetcher, the
API-season fallback and the scheduler mocked. No network, no lifespan traffic
beyond the patched startup path, no production database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import Mock, patch

import duckdb
import pytest

from backend import main as main_module
from backend.api_client import APIClient
from backend.competitions import competitions_metadata
from backend.data_service import DataService
from backend.database import Database
from backend.football_txt_parser import FootballTxtParser


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "competition_edges.db"))
    yield database
    database.close()


def _season_start(today=None) -> int:
    today = today or datetime.now()
    return today.year if today.month >= 8 else today.year - 1


# ---------------------------------------------------------------------------
# 1. plain "League phase" headers (real openfootball EL/UCL format)
# ---------------------------------------------------------------------------

# Slice of openfootball/champions-league 2024-25/conf.txt, verbatim header shape.
CONF_LEAGUE_PHASE = (
    "= UEFA Conference League 2024/25\n"
    "\n"
    "# Date       Wed Oct 2 2024 - Wed May 28 2025 (238d)\n"
    "# Teams      36\n"
    "\n"
    "\n"
    "▪ League phase\n"
    "  Wed Oct 2 2024\n"
    "    16:30  İstanbul Başakşehir (TUR) v Rapid Wien (AUT)         1-2 (1-1)\n"
    "           Vitória Guimarães (POR) v NK Celje (SVN)           3-1 (2-0)\n"
    "  Thu Oct 3\n"
    "    18:45  Chelsea FC (ENG)        v KAA Gent (BEL)           4-2 (1-0)\n"
    "\n"
    "\n"
    "▪ Playoffs\n"
    "  Thu Feb 13 2025\n"
    "    18:45  TSC Bačka Topola (SRB)  v Jagiellonia Białystok (POL)  1-3 (1-1)\n"
)

# Slice of openfootball/champions-league 2024-25/el.txt.
EL_LEAGUE_PHASE = (
    "= UEFA Europa League 2024/25\n"
    "\n"
    "# Teams      36\n"
    "\n"
    "▪ League phase\n"
    "  Wed Sep 25 2024\n"
    "    18:45  AZ Alkmaar (NED)        v IF Elfsborg (SWE)        3-2 (1-1)\n"
    "    21:00  Manchester United (ENG) v FC Twente (NED)          1-1 (1-0)\n"
    "  Thu Sep 26\n"
    "    18:45  Fenerbahçe (TUR)        v Union Saint-Gilloise (BEL)  2-1 (1-0)\n"
)


def test_conference_league_plain_league_phase_header_is_league_stage():
    parsed = FootballTxtParser().parse(CONF_LEAGUE_PHASE, competition="UCL", season="2024-25")
    stages = [m["stage"] for m in parsed["matches"]]

    assert stages[:3] == ["LEAGUE_STAGE"] * 3, (
        "plain '▪ League phase' must tag its matches LEAGUE_STAGE, "
        f"got {stages!r}"
    )
    # The following '▪ Playoffs' header still switches the stage, so the league
    # tag does not leak forward.
    assert stages[-1] == "KNOCKOUT_PLAYOFF"


def test_europa_league_plain_league_phase_header_is_league_stage():
    parsed = FootballTxtParser().parse(EL_LEAGUE_PHASE, competition="EL", season="2024-25")
    matches = parsed["matches"]

    assert len(matches) == 3
    assert [m["stage"] for m in matches] == ["LEAGUE_STAGE"] * 3
    # Dates still resolve for the undated continuation lines.
    assert matches[0]["date"] == "2024-09-25"
    assert matches[2]["date"] == "2024-09-26"


# ---------------------------------------------------------------------------
# 2. GitHub sync: parsed matches but zero inserted must fall back to the API
# ---------------------------------------------------------------------------

def _github_content(label: str) -> str:
    return (
        f"= UEFA {label} 2024/25\n"
        "▪ League phase\n"
        "  Wed Sep 25 2024\n"
        "    18:45  Home Team (ENG) v Away Team (ITA)  1-0\n"
    )


def test_github_zero_insert_reports_not_found(db):
    """Parsed-but-unstored must be reported as not found, never silent success."""
    service = DataService(db, Mock(spec=APIClient))

    with patch.object(
        service.github_fetcher, "fetch_season_file",
        return_value=_github_content("Europa League"),
    ), patch.object(
        service.team_matcher, "find_or_create_team_id", return_value=None,
    ):
        found = service._sync_season_from_github("EL", "Europa League", 2024)

    assert found is False, "zero stored matches must not be reported as found"
    assert db.fetchone("SELECT COUNT(*) FROM matches")[0] == 0


def test_github_with_stored_matches_reports_found(db):
    """Positive control: a genuinely imported season still reports success."""
    service = DataService(db, Mock(spec=APIClient))

    with patch.object(
        service.github_fetcher, "fetch_season_file",
        return_value=_github_content("Europa League"),
    ):
        found = service._sync_season_from_github("EL", "Europa League", 2024)

    assert found is True
    assert db.fetchone("SELECT COUNT(*) FROM matches")[0] == 1


def test_historical_sync_falls_back_to_api_when_github_stores_nothing(db):
    """Legacy seasons must fall back to the bounded API-season fetch.

    EL/UCL 2024/25 and later are now served by ESPN (routed to the ESPN service
    and never the legacy API path); older archive seasons keep the previous
    GitHub-then-API behaviour, so a zero-insert GitHub season is not silent
    success.
    """
    service = DataService(db, Mock(spec=APIClient))
    labels = {c["code"]: c["label"] for c in competitions_metadata()}
    api_calls: list[tuple[str, int]] = []
    espn_calls: list[tuple[str, int]] = []

    def fake_api(comp_id, comp_name, season_year):
        api_calls.append((comp_id, season_year))

    fake_espn = Mock()
    fake_espn.sync_season.side_effect = lambda comp, year: espn_calls.append((comp, year))

    with patch.object(service.github_fetcher, "clone_repository", return_value=None), \
         patch.object(service.github_fetcher, "cleanup", return_value=None), \
         patch.object(
             service.github_fetcher, "fetch_season_file",
             side_effect=lambda season, competition="CL": _github_content(labels[competition]),
         ), \
         patch.object(service.team_matcher, "find_or_create_team_id", return_value=None), \
         patch.object(service, "_sync_season_from_api", side_effect=fake_api), \
         patch.object(service, "_espn", return_value=fake_espn):
        service.sync_historical_matches(years_back=5, delay_between_requests=0)

    # ESPN-backed 2024/25+ seasons dispatch to ESPN, never the legacy API path.
    assert ("EL", 2025) in espn_calls, "EL 2025 must route to ESPN"
    assert ("UCL", 2024) in espn_calls, "UCL 2024 must route to ESPN"
    assert ("EL", 2025) not in api_calls
    assert ("UCL", 2024) not in api_calls

    # Pre-2024 legacy seasons still fall back to the API on zero GitHub inserts.
    assert ("EL", 2022) in api_calls, (
        "pre-2024 EL season with zero GitHub inserts must fall back to the API"
    )
    assert ("UCL", 2022) in api_calls, (
        "pre-2024 UCL season with zero GitHub inserts must fall back to the API"
    )


# ---------------------------------------------------------------------------
# 3. legacy matches table: unknown provenance stays NULL
# ---------------------------------------------------------------------------

def test_legacy_matches_without_competition_id_keep_null_rows(tmp_path):
    db_path = tmp_path / "legacy_matches.db"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT, crest TEXT)"
    )
    con.execute(
        """CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            home_team_id INTEGER NOT NULL,
            away_team_id INTEGER NOT NULL,
            home_score INTEGER,
            away_score INTEGER,
            matchday INTEGER,
            date TEXT,
            status TEXT
        )"""
    )
    con.execute("INSERT INTO teams VALUES (1,'Alpha',NULL,NULL),(2,'Bravo',NULL,NULL)")
    con.execute(
        "INSERT INTO matches VALUES (1,1,2,1,0,1,'2023-09-15T18:00:00Z','FINISHED')"
    )
    con.close()

    database = Database(db_path=str(db_path))
    try:
        assert "competition_id" in database._table_columns("matches")
        row = database.fetchone("SELECT competition_id FROM matches WHERE id = 1")
        assert row[0] is None, (
            "a legacy match row has no competition provenance and must not be "
            "guessed as CL"
        )
    finally:
        database.close()


def test_competition_keyed_integer_solkoff_upgraded_without_redefault(
        tmp_path, monkeypatch):
    """An already competition-keyed INTEGER coefficient table still upgrades.

    Two different competitions can hold the SAME team id; reopening must convert
    the legacy INTEGER column to REAL while preserving both codes/values and
    never reattributing a row to the configured default (here UCL).
    """
    monkeypatch.setenv("COMPETITION_ID", "UCL")
    db_path = tmp_path / "keyed_integer_solkoff.db"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT, crest TEXT)"
    )
    con.execute(
        """CREATE TABLE solkoff_coefficients (
            competition_id TEXT NOT NULL DEFAULT 'CL',
            team_id INTEGER,
            solkoff_value INTEGER NOT NULL,
            calculated_at TEXT NOT NULL,
            PRIMARY KEY (competition_id, team_id),
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )"""
    )
    con.execute("INSERT INTO teams VALUES (1,'Alpha','ALP',NULL)")
    con.execute(
        "INSERT INTO solkoff_coefficients VALUES ('CL',1,3,'t'),('EL',1,7,'t')"
    )
    con.close()

    def _typed_rows(database):
        types = {row[0] for row in database.fetchall(
            "SELECT DISTINCT typeof(solkoff_value) FROM solkoff_coefficients"
        )}
        rows = database.fetchall(
            "SELECT competition_id, team_id, solkoff_value "
            "FROM solkoff_coefficients ORDER BY competition_id"
        )
        return types, rows

    database = Database(db_path=str(db_path))
    try:
        types, rows = _typed_rows(database)
        assert types == {"FLOAT"}, f"INTEGER solkoff_value not upgraded: {types!r}"
        assert rows == [("CL", 1, 3.0), ("EL", 1, 7.0)], rows
        # Same team id, distinct competition codes both kept; no default row.
        assert database.fetchone(
            "SELECT COUNT(*) FROM solkoff_coefficients WHERE competition_id = 'UCL'"
        )[0] == 0
    finally:
        database.close()

    # Reopening again is idempotent (no duplicate rows, still REAL, same values).
    reopened = Database(db_path=str(db_path))
    try:
        types, rows = _typed_rows(reopened)
        assert types == {"FLOAT"}
        assert rows == [("CL", 1, 3.0), ("EL", 1, 7.0)]
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 4. startup historical presence is per competition
# ---------------------------------------------------------------------------

def _seed_cl_history(db_path, count=120):
    db = Database(db_path=str(db_path))
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1,'A',NULL,NULL),(2,'B',NULL,NULL)")
    for i in range(count):
        db.execute(
            """INSERT INTO matches (
                id, home_team_id, away_team_id, home_score, away_score,
                matchday, date, status, stage, round, group_name, competition_id
            ) VALUES (?, 1, 2, 1, 0, NULL, '2023-09-15T18:00:00Z', 'FINISHED',
                      'LEAGUE_STAGE', NULL, NULL, 'CL')""",
            (i + 1,),
        )
    db.commit()
    db.close()


class _SyncThread:
    """Run a thread target inline so the startup path is deterministic."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def test_startup_syncs_missing_competitions_even_when_cl_history_exists(
        tmp_path, monkeypatch):
    db_path = tmp_path / "startup.db"
    _seed_cl_history(db_path, count=120)  # >= the combined-presence threshold

    fake_scheduler = Mock()
    fake_scheduler.data_service = Mock()
    fake_scheduler.elo_calculator = Mock()

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(main_module, "DataScheduler", lambda *a, **k: fake_scheduler)
    monkeypatch.setattr("threading.Thread", _SyncThread)

    async def _run():
        async with main_module.lifespan(main_module.app):
            # EL/UCL have no historical rows, so the presence decision must not
            # short-circuit on the already-present CL data.
            assert fake_scheduler.data_service.sync_historical_matches.called, (
                "startup skipped historical sync for missing EL/UCL because CL existed"
            )

    asyncio.run(_run())
