"""Edge-case regression tests for the offline ESPN reconciliation tool.

Positive and negative coverage for the tool v2 branches that the happy-path
suite does not exercise:

1. A reviewed ``insert`` whose captured score contradicts the pinned ESPN body
   is refused; the source snapshot stays unchanged.
2. ``reference_rewiring`` may only rewrite an allowlisted canonical team-id
   column; a score column (``matches.home_score``) is refused.
3. A ``qualifier`` claim needs independently hashed qualifying evidence; a
   free-form claim with no pinned body is refused.
4. An ``adopt`` of an existing legacy FINISHED fixture (exact beforeimage) keeps
   the original match id and rewires the provider event onto it.
5. A properly pinned qualifier is accepted, the row is retained and marked
   QUALIFYING, and the main-stage "unmanaged row" guard ignores it.
6. An explicit standings snapshot-collision decision keeps the winner and
   deletes the same-cluster loser without adding a row.
7. A contaminated ``team_mappings`` key is retired and a verified replacement
   succeeds.
8. An already competition-keyed snapshot builds without a legacy provenance
   sidecar; an explicit ``EL`` legacy default migrates every row to ``EL``.

Everything runs against real temporary DuckDB files (and, for the integration
case, the shared synthetic capture fixture). Nothing touches the network,
production data, secrets or the scheduler. The source snapshot is never written
to; each mutation test works on a throwaway migrated copy and asserts the
original file hash is unchanged.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import duckdb
import pytest

from backend import espn_reconcile as rec
from backend.espn_client import _STAGE_MAP
from tests.backend import espn_payloads as P
from tests.backend.test_espn_reconcile import (  # noqa: F401  (captures = fixture)
    captures,
    _full_manifest,
    build_captures,
    legacy_source,
    make_club_roots,
    make_manifest,
    write_manifest,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _restore_env(key: str, previous) -> None:
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def _local_captures(directory: Path, scoreboards: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    build_captures(directory, scoreboards)
    return directory


def make_keyed_source(path: Path, teams=(), matches=(), standings=(),
                      competition: str = "EL") -> Path:
    """A source that is already competition-keyed (no legacy provenance needed).

    Built by letting the production ``Database`` migration add the
    ``competition_id`` columns and keyed primary keys, then dropping the
    provenance sidecar so the inventory sees a genuinely keyed snapshot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_source(path, teams=teams, matches=matches, standings=standings,
                  provenance=competition)
    previous = os.environ.get("COMPETITION_ID")
    os.environ["COMPETITION_ID"] = competition
    try:
        db = rec.Database(str(path))
        db.close()
    finally:
        _restore_env("COMPETITION_ID", previous)
    # Backfill the keyed competition for fixture rows, then remove provenance.
    con = duckdb.connect(str(path))
    try:
        con.execute("UPDATE matches SET competition_id = ? WHERE competition_id IS NULL",
                    (competition,))
        con.execute("UPDATE standings SET competition_id = ? WHERE competition_id IS NULL",
                    (competition,))
    finally:
        con.close()
    sidecar = path.parent / "source.provenance.json"
    if sidecar.exists():
        sidecar.unlink()
    return path


def open_target(source: Path) -> "tuple[rec.Database, Path]":
    target = Path(str(source) + ".target.db")
    shutil.copy2(str(source), str(target))
    return rec.Database(str(target)), target


def apply_manifest(db, target: Path, manifest: dict, captures_dir: Path) -> dict:
    entries = rec._read_capture_index(captures_dir)
    beforeimages = rec._BeforeImages(str(target) + ".bi.jsonl")
    try:
        with db.transaction():
            return rec._apply_manifest(db, manifest, Path(captures_dir), entries,
                                       beforeimages)
    finally:
        beforeimages.close()


def _assert_build_refuses(source, manifest_path, output) -> dict:
    """Build must not emit a verified candidate; accept raise or rejected receipt."""
    try:
        receipt = rec.build_candidate(str(source), str(manifest_path), str(output))
    except rec.RefusedError:
        return {"state": "refused-by-exception"}
    assert receipt.get("state") != "verified", receipt
    assert not (receipt.get("verification") or {}).get("verified"), receipt
    return receipt


# ---------------------------------------------------------------------------
# negative: score contradicts the pinned capture (integration build)
# ---------------------------------------------------------------------------

def test_insert_score_contradicting_pinned_capture_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Only Club")])
    before = rec._file_sha256(source)
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)

    op = manifest["fixture_operations"][0]
    assert op["kind"] == "insert"
    assert op["home_score"] is not None
    op["home_score"] = op["home_score"] + 3  # contradicts the pinned event body

    write_manifest(tmp_path, manifest)  # re-signed: not a digest mismatch
    receipt = _assert_build_refuses(source, manifest_path, tmp_path / "c1.db")

    assert receipt["state"] == "refused-by-exception" or receipt["state"] == "rejected"
    assert rec._file_sha256(source) == before, "source snapshot must be unchanged"


# ---------------------------------------------------------------------------
# negative: rewiring a score column / free-form qualifier proof
# ---------------------------------------------------------------------------

def test_reference_rewiring_non_identity_column_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Chaser Club")])
    before = rec._file_sha256(source)
    inv, _, manifest = _full_manifest(captures, source, tmp_path)

    manifest["reference_rewiring"] = [{
        "table": "matches", "column": "home_score",
        "old_team_id": 5, "new_team_id": 6,
        "reason": "bogus: rewrite a CL score", "entry_id": "rewire-score",
    }]
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)
    assert rec._file_sha256(source) == before, "source snapshot must be unchanged"


def test_qualifier_without_hashed_evidence_refused():
    # A free-form claim with no pinned qualifying body proves nothing.
    with pytest.raises(rec.RefusedError):
        rec._validate_qualifier_evidence(
            {"qualifying": {"event_id": "999999", "url": "https://x",
                            "sha256": "0" * 64}},
            None, {},
        )


# ---------------------------------------------------------------------------
# positive: adopt an existing legacy FINISHED fixture, keeping its id
# ---------------------------------------------------------------------------

def test_adopt_preserves_existing_match_id(tmp_path):
    comp = "EL"
    event = P.event(7001, "2026-09-15T20:00Z", 2026, "league-phase", 1, 2, 2, 1)
    caps = _local_captures(tmp_path / "caps", {(comp, 2026, 2026): [event]})

    source = make_keyed_source(tmp_path / "src.db", competition=comp)
    inv0 = rec.inventory(str(source), str(caps))
    _roots0, by_ext0 = make_club_roots(inv0)
    home, away = by_ext0["1"], by_ext0["2"]

    # The legacy row already carries the two canonical roots as teams; the roots
    # for provider clubs 1/2 reuse them instead of creating new ids.
    con = duckdb.connect(str(source))
    try:
        con.execute("INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
                    (home, "Root One"))
        con.execute("INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
                    (away, "Root Two"))
        con.execute(
            "INSERT INTO matches (id, home_team_id, away_team_id, home_score, "
            "away_score, \"date\", status, competition_id) "
            "VALUES (5, ?, ?, 2, 1, ?, ?, ?)",
            (home, away, "2026-09-15T20:00Z", "FINISHED", comp),
        )
    finally:
        con.close()

    before = rec._file_sha256(source)
    inv = rec.inventory(str(source), str(caps))
    assert inv["snapshot_keyed"] is True

    db, target = open_target(source)
    try:
        row = rec._row_dict(db.conn, "matches", "id = ?", (5,))
        stage, round_name = _STAGE_MAP["league-phase"]
        op = {
            "kind": "adopt", "external_event_id": "7001", "competition_id": comp,
            "season_year": 2026, "home_team_id": home, "away_team_id": away,
            "stage": stage, "round": round_name, "date": "2026-09-15T20:00Z",
            "status": "FINISHED", "home_score": 2, "away_score": 1,
            "match_id": 5, "expected_beforeimage": row,
            "reason": "adopt the legacy EL fixture onto the pinned event",
            "entry_id": "adopt-7001",
        }
        roots, _ = make_club_roots(inv)
        for root in roots:
            if root["external_id"] == "1":
                root.update(decision="reuse", canonical_team_id=home,
                            member_team_ids=[home])
            elif root["external_id"] == "2":
                root.update(decision="reuse", canonical_team_id=away,
                            member_team_ids=[away])
        manifest = make_manifest(inv, roots, [op])
        assert rec.validate_manifest(manifest, inv) is None

        apply_manifest(db, target, manifest, caps)

        kept = db.fetchone(
            "SELECT id, home_team_id, away_team_id, home_score, away_score, "
            "status, competition_id FROM matches WHERE id = 5"
        )
        assert kept is not None, "adopt must preserve the existing match id"
        assert kept == (5, home, away, 2, 1, "FINISHED", comp)
        mapped = db.fetchone(
            "SELECT match_id FROM provider_match_mappings "
            "WHERE provider = 'espn' AND external_id = '7001'"
        )
        assert mapped == (5,)
    finally:
        db.close()
    assert rec._file_sha256(source) == before, "source snapshot must be unchanged"


# ---------------------------------------------------------------------------
# positive: pinned qualifier accepted, retained, and ignored by the guard
# ---------------------------------------------------------------------------

def test_qualifier_with_pinned_evidence_accepted_and_ignored(tmp_path):
    comp = "EL"
    qevent = P.event(8001, "2026-07-10T20:00Z", 2026, "qualifying-round-1", 5, 6, 1, 0)
    caps = _local_captures(tmp_path / "caps", {(comp, 2026, 2026): [qevent]})

    source = make_keyed_source(
        tmp_path / "src.db",
        teams=[(5, "Chaser Club"), (6, "Rival Club")],
        matches=[(5, 5, 6, 1, 0, "2026-07-10T20:00Z", "FINISHED")],
        competition=comp,
    )
    before = rec._file_sha256(source)
    inv = rec.inventory(str(source), str(caps))
    main, qualifying = rec._parse_events(rec._read_capture_index(caps), caps)
    pinned = qualifying["8001"]

    db, target = open_target(source)
    try:
        row = rec._row_dict(db.conn, "matches", "id = ?", (5,))
        op = {
            "kind": "qualifier", "match_id": 5,
            "reason": "legacy qualifying tie proven by pinned event",
            "entry_id": "qualifier-5", "expected_beforeimage": row,
            "qualifying": {
                "url": pinned["url"], "sha256": pinned["sha256"],
                "event_id": "8001", "home_external": pinned["home_external"],
                "away_external": pinned["away_external"], "date": pinned["date"],
                "round": pinned["slug"], "home_score": pinned["home_score"],
                "away_score": pinned["away_score"], "season_year": 2026,
            },
        }
        roots, _ = make_club_roots(inv)
        # Explicit participant mapping: provider clubs 5/6 are the stored clubs
        # 5/6, so the legacy row resolves to the same roots as the pinned event.
        for root in roots:
            if root["external_id"] == "5":
                root.update(decision="reuse", canonical_team_id=5, member_team_ids=[5])
            elif root["external_id"] == "6":
                root.update(decision="reuse", canonical_team_id=6, member_team_ids=[6])
        manifest = make_manifest(inv, roots, [op])
        assert rec.validate_manifest(manifest, inv) is None

        apply_manifest(db, target, manifest, caps)

        kept = db.fetchone(
            "SELECT id, stage, \"round\" FROM matches WHERE id = 5"
        )
        assert kept is not None, "qualifier row must be retained, not deleted"
        assert kept[1] == "QUALIFYING" and kept[2] == "qualifying-round-1"
        assert rec._count_unmanaged(db.conn) == 0, "guard must ignore QUALIFYING rows"
    finally:
        db.close()
    assert rec._file_sha256(source) == before


# ---------------------------------------------------------------------------
# positive: standings snapshot collision keeps winner, deletes cluster loser
# ---------------------------------------------------------------------------

def test_standings_snapshot_collision_keeps_winner_deletes_loser(tmp_path):
    comp = "EL"
    caps = _local_captures(tmp_path / "caps", {})  # no main-stage events
    source = make_keyed_source(
        tmp_path / "src.db",
        teams=[(100, "Winner Club"), (200, "Winner Club")],
        standings=[
            (100, 1, 1, 1, 0, 0, 2, 1, 1, 3, "2026-10-01"),
            (200, 2, 1, 0, 0, 1, 0, 2, -2, 0, "2026-10-01"),
        ],
        competition=comp,
    )
    before = rec._file_sha256(source)
    inv = rec.inventory(str(source), str(caps))
    roots, _ = make_club_roots(inv)
    roots[0] = {  # provider club "1" is the reviewed root of the 100/200 cluster
        "provider": "espn", "external_id": "1",
        "canonical_team_id": 100, "member_team_ids": [100, 200],
        "decision": "reuse",
        "evidence": ["two stored rows proven one club"],
        "expected_beforeimages": [],
    }

    db, target = open_target(source)
    try:
        before_count = db.fetchone(
            "SELECT COUNT(*) FROM standings WHERE competition_id = ?", (comp,)
        )[0]
        loser_row = rec._row_dict(
            db.conn, "standings",
            "competition_id = ? AND team_id = ?", (comp, 200),
        )
        decision = {
            "action": "keep", "competition_id": comp, "team_id": 100,
            "loser_team_id": 200, "expected_beforeimage": loser_row,
            "reason": "same-cluster duplicate standings row",
            "entry_id": "standings-keep-100",
        }
        manifest = make_manifest(inv, roots, [], standings_decisions=[decision])
        assert rec.validate_manifest(manifest, inv) is None

        apply_manifest(db, target, manifest, caps)

        assert db.fetchone(
            "SELECT 1 FROM standings WHERE competition_id = ? AND team_id = 100",
            (comp,),
        ) == (1,), "winner row must remain"
        assert db.fetchone(
            "SELECT 1 FROM standings WHERE competition_id = ? AND team_id = 200",
            (comp,),
        ) is None, "loser row must be deleted"
        after_count = db.fetchone(
            "SELECT COUNT(*) FROM standings WHERE competition_id = ?", (comp,)
        )[0]
        assert after_count == before_count - 1, "no row may be added"
    finally:
        db.close()
    assert rec._file_sha256(source) == before


# ---------------------------------------------------------------------------
# positive: contaminated team_mappings key retired + verified replacement
# ---------------------------------------------------------------------------

def test_team_mappings_contaminated_key_retire_and_verified_replace(tmp_path):
    comp = "EL"
    caps = _local_captures(tmp_path / "caps", {})
    source = make_keyed_source(
        tmp_path / "src.db",
        teams=[(5, "A"), (6, "B"), (7, "C")],
        competition="CL",
    )
    before = rec._file_sha256(source)
    inv = rec.inventory(str(source), str(caps))

    db, target = open_target(source)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS team_mappings (team_name TEXT PRIMARY KEY, "
            "team_id INTEGER, confidence REAL, source TEXT)"
        )
        db.execute("INSERT INTO team_mappings VALUES (?, ?, ?, ?)",
                   ("Contaminated FC", 42, 0.5, "legacy"))
        db.execute("INSERT INTO team_mappings VALUES (?, ?, ?, ?)",
                   ("Mapped United", 7, 0.9, "legacy"))
        contaminated = rec._row_dict(
            db.conn, "team_mappings", "team_name = ?", ("Contaminated FC",)
        )
        mapped = rec._row_dict(
            db.conn, "team_mappings", "team_name = ?", ("Mapped United",)
        )
        ops = [
            {"op": "retire", "team_name": "Contaminated FC",
             "reason": "contaminated legacy key", "entry_id": "tm-retire",
             "expected_beforeimage": contaminated},
            {"op": "replace", "team_name": "Mapped United", "team_id": 6,
             "reason": "verified replacement", "entry_id": "tm-replace",
             "expected_beforeimage": mapped},
        ]
        roots, _ = make_club_roots(inv)
        manifest = make_manifest(inv, roots, [], team_mapping_operations=ops)
        assert rec.validate_manifest(manifest, inv) is None

        apply_manifest(db, target, manifest, caps)

        assert db.fetchone(
            "SELECT 1 FROM team_mappings WHERE team_name = ?", ("Contaminated FC",)
        ) is None, "contaminated key must be retired"
        assert db.fetchone(
            "SELECT team_id FROM team_mappings WHERE team_name = ?",
            ("Mapped United",),
        ) == (6,), "verified replacement must apply"
    finally:
        db.close()
    assert rec._file_sha256(source) == before


# ---------------------------------------------------------------------------
# positive: keyed snapshot without provenance + explicit EL legacy default
# ---------------------------------------------------------------------------

def test_competition_keyed_source_needs_no_legacy_provenance(tmp_path):
    caps = _local_captures(tmp_path / "caps", {})
    source = make_keyed_source(
        tmp_path / "keyed" / "src.db", teams=[(5, "Only Club")], competition="EL"
    )
    inv = rec.inventory(str(source), str(caps))

    assert inv["snapshot_keyed"] is True
    assert inv["migration_required"] is False
    assert inv["legacy_competition_default"] is None
    assert inv["provenance"] is None
    assert inv["can_build"] is True


def test_explicit_el_legacy_default_migration(tmp_path):
    caps = _local_captures(tmp_path / "caps", {})
    (tmp_path / "legacy").mkdir(parents=True, exist_ok=True)
    source = legacy_source(
        tmp_path / "legacy" / "src.db",
        teams=[(5, "Only Club")],
        standings=[(5, 1, 0, 0, 0, 0, 0, 0, 0, 0, "2026-10-01")],
        provenance="EL",
    )
    inv = rec.inventory(str(source), str(caps))
    assert inv["migration_required"] is True
    assert inv["legacy_competition_default"] == "EL"
    assert inv["provenance"]["configured_competition"] == "EL"
    assert inv["can_build"] is True

    scratch, _hashes, _refs = rec._migrated_source_scratch(source, "EL")
    try:
        con = duckdb.connect(scratch)
        try:
            rec._assert_legacy_attribution(con, "EL")  # must not raise
            assert con.execute(
                "SELECT competition_id FROM standings WHERE team_id = 5"
            ).fetchone()[0] == "EL"
        finally:
            con.close()
    finally:
        shutil.rmtree(Path(scratch).parent, ignore_errors=True)
