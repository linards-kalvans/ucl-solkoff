"""Tests for reviewed retired-team redirect resolution (``backend.team_identity``).

Covers the shared resolver contract (legacy absent-table identity, direct
redirects, chain/cycle/dangling refusal) and every runtime writer integration
that must canonicalize before referencing: ``TeamMatcher`` cached/exact/fuzzy
paths, the football-data team/match/standings writers, ESPN provider mappings and
the offline historical-repair native-connection identity entry point.

Everything runs against real temporary DuckDB databases; no network or
production data is touched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pytest

from backend.api_client import APIClient
from backend.data_service import DataService
from backend.database import Database
from backend.espn_service import ESPNDataService
from backend.historical_repair import RepairError, resolve_identity
from backend.team_identity import TeamIdentityError, TeamRedirectResolver
from backend.team_matcher import TeamMatcher


def seed(db, teams, redirects=()):
    """Insert ``(id, name, code, crest)`` teams and ``(retired, canonical)`` rows."""
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


# ---------------------------------------------------------------------------
# resolver contract
# ---------------------------------------------------------------------------

def test_database_migration_creates_redirect_table(tmp_path):
    db = Database(str(tmp_path / "db.db"))
    try:
        columns = {
            row[0] for row in db.fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'team_redirects'"
            )
        }
        assert columns == {"retired_team_id", "canonical_team_id"}
    finally:
        db.close()


def test_absent_redirect_table_is_readonly_identity(tmp_path):
    path = tmp_path / "legacy.db"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT, code TEXT, crest TEXT)")
    con.execute("INSERT INTO teams VALUES (1, 'One', NULL, NULL)")
    con.close()

    con = duckdb.connect(str(path), read_only=True)
    try:
        resolver = TeamRedirectResolver(con)
        assert resolver.canonical_id(1) == 1
        assert resolver.canonical_id(None) is None
        assert resolver.retired_ids() == set()
        tables = {
            row[0] for row in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        assert "team_redirects" not in tables, "resolver must not create the table"
    finally:
        con.close()


def test_direct_redirect_resolves_to_active_root(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    try:
        seed(
            db,
            [(100, "Real Club", "RCL", "r.png"), (200, "Real Club", None, None)],
            [(200, 100)],
        )
        resolver = TeamRedirectResolver(db)
        assert resolver.canonical_id(200) == 100
        assert resolver.canonical_id(100) == 100
        assert resolver.retired_ids() == {200}
        assert resolver.is_retired(200) is True and resolver.is_retired(100) is False
    finally:
        db.close()


def _native_db(path, teams, redirects):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT)")
    for team_id, name in teams:
        con.execute("INSERT INTO teams VALUES (?, ?)", (team_id, name))
    con.execute(
        "CREATE TABLE team_redirects (retired_team_id INTEGER PRIMARY KEY, "
        "canonical_team_id INTEGER)"
    )
    for retired, canonical in redirects:
        con.execute("INSERT INTO team_redirects VALUES (?, ?)", (retired, canonical))
    con.close()


@pytest.mark.parametrize(
    "redirects",
    [
        [(2, 999)],   # dangling target
        [(2, 3), (3, 1)],  # chain
        [(2, 3), (3, 2)],  # cycle
        [(1, 1)],     # self-redirect
        [(9, 1)],     # retired id has no teams row
    ],
)
def test_corrupt_redirects_are_refused(tmp_path, redirects):
    path = tmp_path / "corrupt.db"
    _native_db(path, [(1, "One"), (2, "Two"), (3, "Three")], redirects)
    con = duckdb.connect(str(path), read_only=True)
    try:
        with pytest.raises(TeamIdentityError):
            TeamRedirectResolver(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# fail-loud source handling (never silently identity)
# ---------------------------------------------------------------------------

def test_aborted_query_does_not_fall_back_to_identity(tmp_path):
    # ``team_redirects`` exists but a referenced table is missing: a real query
    # failure must surface, never be mistaken for an absent redirect table.
    path = tmp_path / "aborted.db"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE team_redirects (retired_team_id INTEGER, canonical_team_id INTEGER)"
    )
    con.close()
    con = duckdb.connect(str(path), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            TeamRedirectResolver(con)
    finally:
        con.close()


def test_closed_database_wrapper_fails_loud(tmp_path):
    db = Database(str(tmp_path / "closed.db"))
    db.close()
    with pytest.raises(TeamIdentityError):
        TeamRedirectResolver(db)


def test_closed_native_connection_fails_loud(tmp_path):
    con = duckdb.connect(str(tmp_path / "native.db"))
    con.close()
    with pytest.raises((TeamIdentityError, duckdb.Error)):
        TeamRedirectResolver(con)


def test_unsupported_source_fails_loud():
    with pytest.raises(TeamIdentityError):
        TeamRedirectResolver(object())


# ---------------------------------------------------------------------------
# TeamMatcher: cached / exact / fuzzy canonicalization
# ---------------------------------------------------------------------------

def test_matcher_cached_mapping_resolves_retired_target(tmp_path):
    db = Database(str(tmp_path / "m.db"))
    try:
        # Root name differs from the lookup so only the cached alias path can hit.
        seed(
            db,
            [(100, "Beta Athletic", None, None), (200, "21:00 Beta FC", None, None)],
            [(200, 100)],
        )
        matcher = TeamMatcher(db)
        db.execute(
            "INSERT INTO team_mappings (team_name, team_id, confidence, source) "
            "VALUES ('beta', 200, 1.0, 'cached')"
        )
        db.commit()
        assert matcher.find_or_create_team_id("Beta FC") == 100
    finally:
        db.close()


def test_matcher_exact_retired_name_resolves_to_root(tmp_path):
    db = Database(str(tmp_path / "ex.db"))
    try:
        seed(
            db,
            [(700, "Zeta United", None, None), (600, "Zeta FC", None, None)],
            [(600, 700)],
        )
        matcher = TeamMatcher(db)
        assert matcher.find_or_create_team_id("Zeta FC") == 700
        assert matcher.get_team_id_by_name("Zeta FC") == 700
    finally:
        db.close()


def test_matcher_same_root_aliases_not_ambiguous_different_roots_are(tmp_path):
    db = Database(str(tmp_path / "amb.db"))
    try:
        seed(
            db,
            [(100, "Gamma", None, None), (200, "Gamma", None, None)],
            [(200, 100)],
        )
        matcher = TeamMatcher(db)
        # Two rows with the same clean name resolving to one root is one team.
        assert matcher.find_or_create_team_id("Gamma") == 100

        # Two *active* rows with the same name stay ambiguous.
        seed(db, [(300, "Delta", None, None), (301, "Delta", None, None)])
        assert matcher.find_or_create_team_id("Delta") is None
    finally:
        db.close()


def test_matcher_fuzzy_excludes_retired_rows(tmp_path):
    db = Database(str(tmp_path / "fz.db"))
    try:
        seed(
            db,
            [(500, "Rovers Athletic", None, None), (600, "Omega Town", None, None)],
            [(600, 500)],
        )
        matcher = TeamMatcher(db)
        result = matcher.find_or_create_team_id("Omega Towns")
        assert result is not None
        assert result not in (600, 500), "retired row must not be a fuzzy target"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# football-data writers
# ---------------------------------------------------------------------------

def test_football_data_writers_canonicalize_and_preserve_root(tmp_path):
    db = Database(str(tmp_path / "fd.db"))
    try:
        seed(
            db,
            [
                (100, "Real Madrid", "RMA", "root.png"),
                (900, "Real Madrid CF", None, None),
                (901, "Other Club", None, None),
            ],
            [(900, 100)],
        )
        api = Mock(spec=APIClient)
        api.get_competition_standings.return_value = {
            "standings": [{"table": [
                {
                    "team": {"id": 900, "name": "Real Madrid CF", "tla": "RMC",
                             "crest": "new.png"},
                    "position": 1, "playedGames": 1, "won": 1, "draw": 0, "lost": 0,
                    "goalsFor": 2, "goalsAgainst": 0, "goalDifference": 2, "points": 3,
                }
            ]}]
        }
        api.get_competition_matches.return_value = {"matches": [
            {
                "id": 500,
                "homeTeam": {"id": 900, "name": "Real Madrid CF"},
                "awayTeam": {"id": 901, "name": "Other Club"},
                "score": {"fullTime": {"home": 2, "away": 0}},
                "matchday": 1, "utcDate": "2026-09-15T20:00:00Z", "status": "FINISHED",
            }
        ]}
        svc = DataService(db, api)
        svc.sync_teams("CL")
        svc.sync_matches("CL")
        svc.sync_standings("CL")

        # Root metadata preserved (not overwritten by the retired provider payload).
        assert db.fetchone("SELECT name, code, crest FROM teams WHERE id = 100") == (
            "Real Madrid", "RMA", "root.png"
        )
        # The retained retired row is not reactivated/overwritten, and no twin
        # was created for the payload name.
        assert db.fetchone("SELECT name FROM teams WHERE id = 900")[0] == "Real Madrid CF"
        assert db.fetchone("SELECT COUNT(*) FROM teams WHERE name = 'Real Madrid CF'")[0] == 1
        # Match and standings reference the root.
        assert db.fetchone("SELECT home_team_id, away_team_id FROM matches WHERE id = 500") == (
            100, 901
        )
        assert db.fetchone(
            "SELECT team_id FROM standings WHERE competition_id = 'CL'"
        )[0] == 100
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ESPN provider mappings
# ---------------------------------------------------------------------------

def test_espn_provider_mapping_resolves_retired_to_root(tmp_path):
    db = Database(str(tmp_path / "espn.db"))
    try:
        seed(
            db,
            [(100, "ESPN Club 01", "OLD", "old.png"), (200, "ESPN Club 01", None, None)],
            [(200, 100)],
        )
        db.execute(
            "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
            "VALUES ('espn', '1', 200)"
        )
        db.commit()
        svc = ESPNDataService(db)
        resolved = svc._resolve_team(
            {"externalTeamId": "1", "name": "ESPN Club 01"}, {}
        )
        assert resolved == 100
        assert db.fetchone(
            "SELECT team_id FROM provider_team_mappings "
            "WHERE provider = 'espn' AND external_id = '1'"
        )[0] == 100
        # The retired row is untouched.
        assert db.fetchone("SELECT name FROM teams WHERE id = 200")[0] == "ESPN Club 01"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# offline historical repair (native connection entry point)
# ---------------------------------------------------------------------------

def test_historical_repair_resolve_identity_uses_redirects(tmp_path):
    path = tmp_path / "repair.db"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT, code TEXT, crest TEXT)")
    con.execute("INSERT INTO teams VALUES (100, 'Retired Club', NULL, NULL)")
    con.execute("INSERT INTO teams VALUES (7, 'Retired Club', NULL, NULL)")
    con.execute(
        "CREATE TABLE team_redirects (retired_team_id INTEGER PRIMARY KEY, "
        "canonical_team_id INTEGER)"
    )
    con.execute("INSERT INTO team_redirects VALUES (100, 7)")

    prepared = [SimpleNamespace(team_names=["Retired Club"])]
    plan, new_teams = resolve_identity(con, prepared)

    assert plan["Retired Club"] == 7
    assert new_teams == {}
    # No writes: both stored rows are retained.
    ids = {row[0] for row in con.execute("SELECT id FROM teams").fetchall()}
    assert ids == {100, 7}
    con.close()


def _native_repair_db(path, teams, redirects):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT, code TEXT, crest TEXT)")
    for team_id, name in teams:
        con.execute("INSERT INTO teams VALUES (?, ?, NULL, NULL)", (team_id, name))
    con.execute(
        "CREATE TABLE team_redirects (retired_team_id INTEGER PRIMARY KEY, "
        "canonical_team_id INTEGER)"
    )
    for retired, canonical in redirects:
        con.execute("INSERT INTO team_redirects VALUES (?, ?)", (retired, canonical))
    return con


def test_historical_repair_retired_alias_different_root_name(tmp_path):
    # The source name is carried only by a retained retired row whose root has a
    # different display name: resolve to the root, never allocate a new team.
    con = _native_repair_db(
        tmp_path / "aliasroot.db",
        [(100, "Old Alias"), (7, "Canonical Root")],
        [(100, 7)],
    )
    try:
        prepared = [SimpleNamespace(team_names=["Old Alias"])]
        plan, new_teams = resolve_identity(con, prepared)
        assert plan["Old Alias"] == 7
        assert new_teams == {}
        assert {row[0] for row in con.execute("SELECT id FROM teams").fetchall()} == {100, 7}
    finally:
        con.close()


def test_historical_repair_ambiguous_retired_alias_raises(tmp_path):
    con = _native_repair_db(
        tmp_path / "aliasamb.db",
        [(100, "Old Alias"), (101, "Old Alias"), (7, "Root A"), (8, "Root B")],
        [(100, 7), (101, 8)],
    )
    try:
        prepared = [SimpleNamespace(team_names=["Old Alias"])]
        with pytest.raises(RepairError):
            resolve_identity(con, prepared)
    finally:
        con.close()
