"""Tests for the offline scoped historical-archive repair tool.

These tests build a synthetic, fully consistent two-season source (189 fixtures
each: 144 league + 16 play-off + 16 R16 + 8 QF + 4 SF + 1 final) and exercise
the repair pipeline against a temporary DuckDB: dry-run isolation, faithful
reproduction of league tables, scoped replacement, identity rules, atomic
failure, protected-table integrity, idempotence, collisions and hash-stable ids.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from backend.data_service import DataService
from backend.database import Database
from backend.historical_repair import (
    RepairError,
    SeasonSource,
    load_season,
    resolve_identity,
    run_repair,
    source_league_table,
    stage_fixtures,
    validate_season,
)
from backend.season_service import build_breakdown, build_standings
from backend.team_matcher import TeamMatcher

REPO_ROOT = Path(__file__).resolve().parents[2]

CANON = [f"Canon{i:02d}" for i in range(36)]
LEAGUE_DATES = [(9, 17), (10, 1), (10, 22), (11, 5), (11, 26), (12, 10), (1, 21), (1, 28)]
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _header(year, month, day):
    season_year = year if month >= 8 else year + 1
    return f"  Tue {_MONTHS[month]} {day} {season_year}"


def _score(i, j):
    return (i * 7 + j * 3) % 4, (i * 5 + j) % 3


def _rounds():
    arr = list(range(36))
    out = []
    for _ in range(8):
        out.append([(arr[i], arr[35 - i]) for i in range(18)])
        arr = [arr[0], arr[-1]] + arr[1:-1]
    return out


def build_source(year=2024, drop_league_match=False):
    """Deterministic complete-season source text."""
    lines = [f"= UEFA Champions League {year}/{str(year + 1)[-2:]}", ""]
    rounds = _rounds()
    dropped = False
    for md, pairs in enumerate(rounds, start=1):
        month, day = LEAGUE_DATES[md - 1]
        lines.append(f"▪ League, Matchday {md}")
        lines.append(_header(year, month, day))
        for k, (a, b) in enumerate(pairs):
            if drop_league_match and not dropped and md == 1 and k == 0:
                dropped = True
                continue
            hs, as_ = _score(a, b)
            lines.append(f"    18:45  {CANON[a]} (AAA) v {CANON[b]} (BBB)  {hs}-{as_} ({hs}-{as_})")
        lines.append("")

    def tie_block(title, md, month, day, pairs):
        lines.append(title)
        lines.append(_header(year, month, day))
        for a, b in pairs:
            hs, as_ = _score(a, b)
            lines.append(f"    21:00  {CANON[a]} (AAA) v {CANON[b]} (BBB)  {hs}-{as_}")
        lines.append("")

    pairs16 = [(2 * i, 2 * i + 1) for i in range(8)]
    tie_block("▪ Playoffs, Matchday 1", 1, 2, 11, pairs16)
    tie_block("▪ Playoffs, Matchday 2", 2, 2, 18, [(b, a) for a, b in pairs16])
    tie_block("▪ Finals, Round of 16", None, 3, 4, pairs16)
    tie_block("▪ Finals, Round of 16", None, 3, 11, [(b, a) for a, b in pairs16])
    pairs8 = pairs16[:4]
    tie_block("▪ Finals, Quarterfinals", None, 4, 8, pairs8)
    tie_block("▪ Finals, Quarterfinals", None, 4, 15, [(b, a) for a, b in pairs8])
    pairs4 = pairs8[:2]
    tie_block("▪ Finals, Semifinals", None, 4, 29, pairs4)
    tie_block("▪ Finals, Semifinals", None, 5, 6, [(b, a) for a, b in pairs4])
    tie_block("▪ Finals, Final", None, 5, 31, [pairs4[0]])
    return "\n".join(lines) + "\n"


def write_source(tmp_path, year, text=None):
    content = text if text is not None else build_source(year)
    path = tmp_path / f"cl-{year}.txt"
    path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return str(path), digest


def source(year, tmp_path, text=None):
    path, digest = write_source(tmp_path, year, text)
    return SeasonSource(year=year, path=path, expected_sha256=digest)


# ---------------------------------------------------------------------------
# database seeding
# ---------------------------------------------------------------------------

def new_db(db_path):
    database = Database(db_path=str(db_path))
    database.close()


def exec_sql(db_path, sql, params=None):
    con = duckdb.connect(str(db_path))
    try:
        con.execute(sql, params or [])
        con.commit()
    finally:
        con.close()


def seed_teams(db_path, rows):
    con = duckdb.connect(str(db_path))
    try:
        for tid, name in rows:
            con.execute(
                "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
                [tid, name],
            )
        con.commit()
    finally:
        con.close()


def counts(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            "matches": con.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "teams": con.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "standings": con.execute("SELECT COUNT(*) FROM standings").fetchone()[0],
            "elo": con.execute("SELECT COUNT(*) FROM elo_ratings").fetchone()[0],
            "solkoff": con.execute("SELECT COUNT(*) FROM solkoff_coefficients").fetchone()[0],
            "cache": con.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0],
        }
    finally:
        con.close()


def seed_protected(db_path, team_id):
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO standings (team_id, position, played, won, drawn, lost, "
            "goals_for, goals_against, goal_difference, points, last_updated) "
            "VALUES (?, 1, 8, 5, 1, 2, 15, 8, 7, 16, '2026-09-01T00:00:00Z')",
            [team_id],
        )
        con.execute(
            "INSERT INTO elo_ratings (team_id, rating, matches_played, updated_at) "
            "VALUES (?, 1800.0, 10, '2026-09-01T00:00:00Z')",
            [team_id],
        )
        con.execute(
            "INSERT INTO solkoff_coefficients (team_id, solkoff_value, calculated_at) "
            "VALUES (?, 1.5, '2026-09-01T00:00:00Z')",
            [team_id],
        )
        con.execute(
            "INSERT INTO api_cache (cache_key, endpoint, response_data, cached_at, expires_at) "
            "VALUES ('k', '/x', '{}', '2026-09-01T00:00:00Z', '2027-01-01T00:00:00Z')"
        )
        con.commit()
    finally:
        con.close()


def fixtures_in_window(db_path, year, competition_id="CL"):
    start, end = f"{year}-08-01", f"{year + 1}-08-01"
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            'SELECT COUNT(*) FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
            [competition_id, start, end],
        ).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# dry run and faithful reproduction
# ---------------------------------------------------------------------------

def test_dry_run_reports_without_writing(tmp_path):
    db_path = tmp_path / "dryrun.db"
    new_db(db_path)
    before = counts(db_path)
    report = run_repair(
        str(db_path), [source(2024, tmp_path), source(2025, tmp_path)], apply=False
    )
    assert report.applied is False
    assert report.staged_count == 378
    assert report.staged_by_year == {"2024": 189, "2025": 189}
    assert counts(db_path) == before
    assert all(s["league_clubs"] == 36 for s in report.seasons)


def test_faithful_reproduction_matches_independent_source_table(tmp_path):
    db_path = tmp_path / "faithful.db"
    new_db(db_path)
    seasons = [source(2024, tmp_path), source(2025, tmp_path)]
    run_repair(str(db_path), seasons, apply=True)

    for src in seasons:
        prepared = load_season(src)
        validate_season(prepared)
        # Identity resolution after apply: clean nodes now exist.
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            plan, new_teams = resolve_identity(con, [prepared])
        finally:
            con.close()
        assert new_teams == {}
        expected = source_league_table(prepared, plan)

        database = Database(db_path=str(db_path))
        try:
            standings = build_standings(database, src.year)
            assert len(standings) == 36
            for team in standings:
                ref = expected[team["teamId"]]
                assert team["played"] == 8
                assert team["points"] == ref["points"]
                assert team["gf"] == ref["gf"]
                assert team["ga"] == ref["ga"]
                assert team["solkoffCoefficient"] == ref["solkoff"]

            # Breakdown agrees too (not just counts).
            sample = standings[0]["teamId"]
            breakdown = build_breakdown(database, sample, src.year)
            assert breakdown["matchesCount"] == 8
            assert breakdown["opponentsCount"] == 8
            assert breakdown["solkoffCoefficient"] == expected[sample]["solkoff"]
        finally:
            database.close()


def test_scoped_replacement_preserves_other_rows(tmp_path):
    db_path = tmp_path / "scoped.db"
    new_db(db_path)
    seed_teams(db_path, [(1, "Legacy A"), (2, "Legacy B"), (3, "Other Comp A"), (4, "Other Comp B")])
    # Outside-window CL, other competition in-window, future-window CL.
    exec_sql(
        db_path,
        "INSERT INTO matches (id, home_team_id, away_team_id, home_score, away_score, "
        "matchday, \"date\", status, stage, competition_id) VALUES "
        "(901, 1, 2, 1, 0, 1, '2023-09-17', 'FINISHED', 'LEAGUE_STAGE', 'CL'), "
        "(902, 3, 4, 2, 2, 1, '2024-09-20', 'FINISHED', 'LEAGUE_STAGE', 'EL'), "
        "(903, 1, 2, 3, 1, 1, '2026-09-16', 'FINISHED', 'LEAGUE_STAGE', 'CL')",
    )
    # Polluted in-window rows that must be replaced.
    exec_sql(
        db_path,
        "INSERT INTO matches (id, home_team_id, away_team_id, home_score, away_score, "
        "matchday, \"date\", status, competition_id) VALUES "
        "(-111, 1, 2, 9, 9, NULL, '2024-09-17', 'FINISHED', 'CL'), "
        "(-112, 2, 1, 9, 9, NULL, '2025-01-29', 'FINISHED', 'CL')",
    )

    run_repair(str(db_path), [source(2024, tmp_path)], apply=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        kept = dict(con.execute(
            "SELECT id, \"date\" FROM matches WHERE id IN (901, 902, 903)"
        ).fetchall())
        assert kept == {901: "2023-09-17", 902: "2024-09-20", 903: "2026-09-16"}
        assert con.execute("SELECT COUNT(*) FROM matches WHERE id IN (-111, -112)").fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM matches WHERE competition_id = 'EL'"
        ).fetchone()[0] == 1
        assert fixtures_in_window(db_path=db_path, year=2024) == 189
        assert fixtures_in_window(db_path=db_path, year=2026) == 1
    finally:
        con.close()


# ---------------------------------------------------------------------------
# identity rules
# ---------------------------------------------------------------------------

def test_polluted_only_creates_clean_node_and_keeps_old(tmp_path):
    db_path = tmp_path / "polluted.db"
    new_db(db_path)
    seed_teams(db_path, [(100, CANON[0]), (-200, f"18:45 {CANON[1]}")])
    report = run_repair(str(db_path), [source(2024, tmp_path)], apply=True)

    identity = report.identity
    assert identity[CANON[0]] == 100  # trusted clean id reused
    assert identity[CANON[1]] < 0 and identity[CANON[1]] != -200  # new clean node

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        names = dict(con.execute("SELECT id, name FROM teams").fetchall())
        assert names[-200] == f"18:45 {CANON[1]}"  # polluted row untouched
        assert names[identity[CANON[1]]] == CANON[1]
        assert not names[identity[CANON[1]]].startswith("18:45")
    finally:
        con.close()


def test_ambiguous_identity_requires_explicit_mapping(tmp_path):
    db_path = tmp_path / "ambiguous.db"
    new_db(db_path)
    seed_teams(db_path, [(100, CANON[0]), (101, CANON[0])])
    with pytest.raises(RepairError, match="ambiguous"):
        run_repair(str(db_path), [source(2024, tmp_path)], apply=False)


def test_explicit_mapping_rejects_unknown_team(tmp_path):
    db_path = tmp_path / "map.db"
    new_db(db_path)
    seed_teams(db_path, [(100, CANON[0])])
    with pytest.raises(RepairError, match="unknown team"):
        run_repair(str(db_path), [source(2024, tmp_path)], explicit_mapping={CANON[0]: 99999})


def test_explicit_mapping_overrides_polluted(tmp_path):
    db_path = tmp_path / "map2.db"
    new_db(db_path)
    seed_teams(db_path, [(100, CANON[0]), (-200, f"21:00 {CANON[1]}")])
    # Explicitly point the polluted-spelling source at the existing polluted row.
    report = run_repair(
        str(db_path), [source(2024, tmp_path)], explicit_mapping={CANON[1]: -200}, apply=False
    )
    assert report.identity[CANON[1]] == -200


# ---------------------------------------------------------------------------
# validation / atomicity
# ---------------------------------------------------------------------------

def test_checksum_mismatch_refused(tmp_path):
    db_path = tmp_path / "sha.db"
    new_db(db_path)
    before = counts(db_path)
    bad = SeasonSource(year=2024, path=write_source(tmp_path, 2024)[0], expected_sha256="0" * 64)
    with pytest.raises(RepairError, match="checksum mismatch"):
        run_repair(str(db_path), [bad], apply=True)
    assert counts(db_path) == before


def test_unapproved_year_refused(tmp_path):
    db_path = tmp_path / "year.db"
    new_db(db_path)
    with pytest.raises(RepairError, match="not approved"):
        run_repair(str(db_path), [source(2023, tmp_path)], apply=True)


def test_failed_validation_leaves_db_unchanged(tmp_path):
    db_path = tmp_path / "invalid.db"
    new_db(db_path)
    bad_text = build_source(2024, drop_league_match=True)
    src = source(2024, tmp_path, text=bad_text)
    before = counts(db_path)
    with pytest.raises(RepairError):
        run_repair(str(db_path), [src], apply=True)
    assert counts(db_path) == before


# ---------------------------------------------------------------------------
# protected tables
# ---------------------------------------------------------------------------

def test_protected_tables_untouched(tmp_path):
    db_path = tmp_path / "protected.db"
    new_db(db_path)
    seed_teams(db_path, [(1, "Legacy A")])
    seed_protected(db_path, 1)
    before = counts(db_path)

    run_repair(str(db_path), [source(2024, tmp_path)], apply=True)

    after = counts(db_path)
    for table in ("standings", "elo", "solkoff", "cache"):
        assert after[table] == before[table]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        assert con.execute(
            "SELECT solkoff_value FROM solkoff_coefficients WHERE team_id = 1"
        ).fetchone()[0] == 1.5
        assert con.execute(
            "SELECT rating FROM elo_ratings WHERE team_id = 1"
        ).fetchone()[0] == 1800.0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# idempotence / collisions / stable ids
# ---------------------------------------------------------------------------

def _snapshot(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        matches = con.execute(
            'SELECT id, home_team_id, away_team_id, home_score, away_score, matchday, "date", '
            "stage, \"round\", competition_id FROM matches ORDER BY id"
        ).fetchall()
        teams = con.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
        return matches, teams
    finally:
        con.close()


def test_second_apply_is_idempotent(tmp_path):
    db_path = tmp_path / "idem.db"
    new_db(db_path)
    seasons = [source(2024, tmp_path), source(2025, tmp_path)]
    run_repair(str(db_path), seasons, apply=True)
    first = _snapshot(db_path)
    report2 = run_repair(str(db_path), seasons, apply=True)
    second = _snapshot(db_path)

    assert first == second
    assert report2.new_teams == []


def test_scoped_mapping_recorded_for_new_clean_node(tmp_path):
    db_path = tmp_path / "mapping.db"
    new_db(db_path)
    exec_sql(
        db_path,
        "CREATE TABLE team_mappings (team_name TEXT PRIMARY KEY, team_id INTEGER, "
        "confidence REAL, source TEXT)",
    )
    report = run_repair(str(db_path), [source(2024, tmp_path)], apply=True)
    new_id = report.identity[CANON[10]]

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT team_id, source FROM team_mappings WHERE team_name = ?",
            [CANON[10].lower()],
        ).fetchone()
        assert row == (new_id, "historical_repair")
    finally:
        con.close()


def test_fixture_id_collision_outside_window_refused(tmp_path):
    db_path = tmp_path / "collision.db"
    new_db(db_path)
    src = source(2024, tmp_path)
    prepared = load_season(src)
    con = duckdb.connect(str(db_path))
    try:
        plan, _ = resolve_identity(con, [prepared])
    finally:
        con.close()
    staged_id = next(
        m.match_id for m in stage_fixtures([prepared], plan) if m.stage == "LEAGUE_STAGE"
    )
    seed_teams(db_path, [(1, "Legacy A"), (2, "Legacy B")])
    # A different fixture, outside every approved window, already owns this id.
    exec_sql(
        db_path,
        "INSERT INTO matches (id, home_team_id, away_team_id, home_score, away_score, "
        "matchday, \"date\", status, competition_id) VALUES (?, 1, 2, 1, 0, 1, '2019-09-17', 'FINISHED', 'CL')",
        [staged_id],
    )
    before = counts(db_path)
    with pytest.raises(RepairError, match="outside the approved windows"):
        run_repair(str(db_path), [src], apply=True)
    assert counts(db_path) == before


def test_ids_are_stable_across_python_hash_seeds(tmp_path):
    code = (
        "from backend.historical_repair import deterministic_match_id;"
        "print(deterministic_match_id('CL','2024-25',3,7,'2024-09-17','LEAGUE_STAGE',1))"
    )
    outputs = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT), env=env,
            capture_output=True, text=True, check=True,
        )
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    assert int(outputs[0]) < 0


# ---------------------------------------------------------------------------
# reviewer round: matcher must never return/store a polluted target
# ---------------------------------------------------------------------------

def _open_db(path):
    database = Database(db_path=str(path))
    return database


def test_matcher_ignores_polluted_cached_mapping(tmp_path):
    db = _open_db(tmp_path / "poison.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (100,'Liverpool FC',NULL,NULL)")
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (-200,'20.45 Liverpool FC',NULL,NULL)")
        db.commit()
        matcher = TeamMatcher(db)  # creates team_mappings
        # Poisoned alias already cached to the polluted node.
        db.execute(
            "INSERT INTO team_mappings (team_name, team_id, confidence, source) "
            "VALUES ('liverpool', -200, 1.0, 'synthetic')"
        )
        db.commit()

        assert matcher.find_or_create_team_id("21:00 Liverpool FC") == 100
        names = dict(db.fetchall("SELECT id, name FROM teams"))
        assert names[-200] == "20.45 Liverpool FC"  # polluted row untouched
        assert len(names) == 2  # no extra node created
    finally:
        db.close()


def test_matcher_prefers_clean_canonical_mapping(tmp_path):
    db = _open_db(tmp_path / "canonical.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (100,'Liverpool FC',NULL,NULL)")
        db.commit()
        matcher = TeamMatcher(db)
        db.execute(
            "INSERT INTO team_mappings (team_name, team_id, confidence, source) "
            "VALUES ('liverpool', 100, 1.0, 'historical_repair')"
        )
        db.commit()
        assert matcher.find_or_create_team_id("18:45 Liverpool FC") == 100
    finally:
        db.close()


def test_matcher_fuzzy_skips_polluted_rows(tmp_path):
    db = _open_db(tmp_path / "fuzzy.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (5,'Bayer 04 Leverkusen',NULL,NULL)")
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (-200,'18:45 Bayer 04 Leverkusen',NULL,NULL)")
        db.commit()
        matcher = TeamMatcher(db)

        # Variant spelling must fuzzy-match the CLEAN row, not the polluted one.
        assert matcher.find_or_create_team_id("Bayer 04 Leverkusen FC") == 5
    finally:
        db.close()


def test_matcher_exact_clean_prefers_positive_and_is_unambiguous(tmp_path):
    db = _open_db(tmp_path / "positive.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (100,'Alpha FC',NULL,NULL)")
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (-300,'Alpha FC',NULL,NULL)")
        db.commit()
        matcher = TeamMatcher(db)
        assert matcher.find_or_create_team_id("Alpha FC") == 100
    finally:
        db.close()


def test_matcher_only_polluted_rows_creates_clean_node(tmp_path):
    db = _open_db(tmp_path / "onlypol.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (-200,'18:45 Bayer 04 Leverkusen',NULL,NULL)")
        db.commit()
        matcher = TeamMatcher(db)

        new_id = matcher.find_or_create_team_id("Bayer 04 Leverkusen")
        names = dict(db.fetchall("SELECT id, name FROM teams"))
        assert new_id != -200
        assert names[new_id] == "Bayer 04 Leverkusen"
        assert names[-200] == "18:45 Bayer 04 Leverkusen"
    finally:
        db.close()


def test_matcher_does_not_rewrite_unrelated_team_metadata(tmp_path):
    db = _open_db(tmp_path / "norewrite.db")
    try:
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (7,'Other FC','OTH','crest://7')")
        db.execute("INSERT INTO teams (id,name,code,crest) VALUES (100,'Liverpool FC','LIV','crest://liv')")
        db.commit()
        matcher = TeamMatcher(db)
        matcher.find_or_create_team_id("21:00 Liverpool FC")

        rows = dict((r[0], (r[1], r[2], r[3])) for r in db.fetchall("SELECT id, name, code, crest FROM teams"))
        assert rows[7] == ("Other FC", "OTH", "crest://7")
        assert rows[100] == ("Liverpool FC", "LIV", "crest://liv")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# reviewer round: scoped mapping upsert + protected fingerprint exclusion
# ---------------------------------------------------------------------------

def test_repair_upserts_scoped_alias_preserving_unrelated_keys(tmp_path):
    db_path = tmp_path / "alias.db"
    new_db(db_path)
    exec_sql(
        db_path,
        "CREATE TABLE team_mappings (team_name TEXT PRIMARY KEY, team_id INTEGER, "
        "confidence REAL, source TEXT)",
    )
    exec_sql(
        db_path,
        "INSERT INTO team_mappings VALUES ('canon00', -200, 1.0, 'synthetic'), "
        "('unrelated key', 999, 0.5, 'legacy')",
    )

    report = run_repair(str(db_path), [source(2024, tmp_path)], apply=True)
    clean_id = report.identity[CANON[0]]

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = dict(con.execute("SELECT team_name, team_id FROM team_mappings").fetchall())
    finally:
        con.close()
    assert rows["canon00"] == clean_id
    assert rows["unrelated key"] == 999  # unrelated key preserved
    canon00_change = next(c for c in report.mapping_changes if c["key"] == "canon00")
    assert canon00_change == {"key": "canon00", "before": -200, "after": clean_id}
    assert report.protected_before == report.protected_after


def test_repair_mapping_changes_report_only_scoped_keys(tmp_path):
    db_path = tmp_path / "mapreport.db"
    new_db(db_path)
    exec_sql(
        db_path,
        "CREATE TABLE team_mappings (team_name TEXT PRIMARY KEY, team_id INTEGER, "
        "confidence REAL, source TEXT)",
    )
    exec_sql(db_path, "INSERT INTO team_mappings VALUES ('keep me', 42, 0.1, 'x')")
    report = run_repair(str(db_path), [source(2024, tmp_path)], apply=True)
    assert all(c["key"] != "keep me" for c in report.mapping_changes)
    # Every canonical source name got an upsert to its clean id.
    assert len(report.mapping_changes) == len(report.identity)


# ---------------------------------------------------------------------------
# reviewer round: repair then offline re-import is a no-op
# ---------------------------------------------------------------------------

def _window_match_ids(db_path, year):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            'SELECT id FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
            ["CL", f"{year}-08-01", f"{year + 1}-08-01"],
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def test_reimport_after_repair_adds_nothing(tmp_path):
    db_path = tmp_path / "reimport.db"
    new_db(db_path)
    src = source(2024, tmp_path)
    run_repair(str(db_path), [src], apply=True)

    before_counts = counts(db_path)
    before_ids = _window_match_ids(db_path, 2024)
    text = Path(src.path).read_text(encoding="utf-8")

    database = Database(db_path=str(db_path))
    try:
        service = DataService(database, Mock())
        service.github_fetcher.fetch_season_file = lambda season, competition="CL": text
        service._sync_season_from_github("CL", "Champions League", 2024)
    finally:
        database.close()

    after_counts = counts(db_path)
    after_ids = _window_match_ids(db_path, 2024)
    assert after_ids == before_ids  # canonical fixture ids consistent
    assert after_counts["matches"] == before_counts["matches"]
    assert after_counts["teams"] == before_counts["teams"]
    assert after_counts["standings"] == before_counts["standings"]
    assert after_counts["elo"] == before_counts["elo"]
    assert after_counts["solkoff"] == before_counts["solkoff"]
    assert after_counts["cache"] == before_counts["cache"]


# ---------------------------------------------------------------------------
# reviewer round: knockout clubs must be league participants
# ---------------------------------------------------------------------------

def test_knockout_club_outside_league_refused(tmp_path):
    text = build_source(2024)
    lines = text.split("\n")
    seen_r16 = False
    for i, line in enumerate(lines):
        if line.startswith("▪ Finals, Round of 16"):
            seen_r16 = True
            continue
        if seen_r16 and " v " in line:
            lines[i] = line.replace(CANON[1], "Foreign FC", 1)
            break
    src = source(2024, tmp_path, text="\n".join(lines))
    prepared = load_season(src)
    with pytest.raises(RepairError, match="not in the league phase"):
        validate_season(prepared)
