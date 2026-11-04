"""Hermetic tests for the offline ESPN reconciliation tool.

Every test runs against real temporary DuckDB files and synthetic ESPN payload
bodies built from :mod:`tests.backend.espn_payloads`. Nothing touches the
network, production data, secrets or the scheduler. The pinned current year is
2026 (the plan's effective year), and 2026 is the only scoreboard set with
events so the heavy capture replay stays bounded; 2024/2025 captures remain
present (required for coverage) with empty schedules.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest

from backend import espn_reconcile as rec
from backend.competitions import espn_slug_for
from backend.espn_client import _QUALIFYING_SLUGS, _STAGE_MAP, ESPNClient
from tests.backend import espn_payloads as P

SCOPED = ("EL", "UCL")
YEARS = (2024, 2025, 2026)
PINNED_YEAR = 2026


# ---------------------------------------------------------------------------
# synthetic captures
# ---------------------------------------------------------------------------

def _write_body(captures_dir: Path, rel: str, payload) -> tuple[str, str]:
    raw = json.dumps(payload).encode("utf-8")
    path = captures_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return rel, hashlib.sha256(raw).hexdigest()


def build_captures(captures_dir: Path, scoreboards: dict) -> dict:
    """Write captures.json + bodies; ``scoreboards[(comp, season, year_arg)] = events``.

    Events are merged per *calendar* URL year (``dates=<year_arg>``) so the same
    URL referenced by two adjacent season entries is written as one byte-identical
    body, exactly as a real capture would be (a ``dates=2026`` pull carries both
    the 2025/26 and 2026/27 events and each season filters on its own year).
    """
    captures_dir.mkdir(parents=True, exist_ok=True)
    merged: dict = {}
    for (comp, _season_year, year_arg), events in scoreboards.items():
        merged.setdefault((comp, year_arg), []).extend(events)
    entries = []
    for comp in SCOPED:
        slug = espn_slug_for(comp)
        for year in YEARS:
            rel, sha = _write_body(
                captures_dir, f"bodies/{comp}-{year}-standings.json",
                P.standings_payload(comp, year),
            )
            entries.append({
                "competition": comp, "season_year": year,
                "url": ESPNClient._standings_url(slug, year),
                "sha256": sha, "path": rel, "collected_at": "2026-10-16T00:00:00Z",
            })
            for year_arg in (year, year + 1):
                events = merged.get((comp, year_arg), [])
                rel, sha = _write_body(
                    captures_dir, f"bodies/{comp}-sb-{year_arg}.json",
                    P.scoreboard_payload(events, slug=P.SCOREBOARD_SLUGS[comp]),
                )
                entries.append({
                    "competition": comp, "season_year": year,
                    "url": ESPNClient._scoreboard_url(slug, year_arg),
                    "sha256": sha, "path": rel, "collected_at": "2026-10-16T00:00:00Z",
                })
    (captures_dir / "captures.json").write_text(
        json.dumps({"version": 1, "captures": entries}), encoding="utf-8"
    )
    return {"captures": entries}


@pytest.fixture(scope="module")
def captures(tmp_path_factory):
    directory = tmp_path_factory.mktemp("espn-captures")
    scoreboards = {}
    for comp in SCOPED:
        builder = P.el_season_events if comp == "EL" else P.conf_season_events
        scoreboards[(comp, PINNED_YEAR, PINNED_YEAR)] = builder(PINNED_YEAR)
    build_captures(directory, scoreboards)
    return directory


# ---------------------------------------------------------------------------
# synthetic legacy source
# ---------------------------------------------------------------------------

def legacy_source(path: Path, teams=(), matches=(), standings=(),
                  provenance="CL", match_competition=None) -> Path:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "code TEXT, crest TEXT)"
    )
    con.execute(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, home_team_id INTEGER NOT NULL, "
        "away_team_id INTEGER NOT NULL, home_score INTEGER, away_score INTEGER, "
        "matchday INTEGER, date TEXT, status TEXT)"
    )
    con.execute(
        "CREATE TABLE standings (team_id INTEGER, position INTEGER, played INTEGER, "
        "won INTEGER, drawn INTEGER, lost INTEGER, goals_for INTEGER, "
        "goals_against INTEGER, goal_difference INTEGER, points INTEGER, "
        "last_updated TEXT)"
    )
    for row in teams:
        con.execute("INSERT INTO teams VALUES (?, ?, NULL, NULL)", row)
    for row in matches:
        con.execute("INSERT INTO matches VALUES (?, ?, ?, ?, ?, NULL, ?, ?)", row)
    for row in standings:
        con.execute(
            "INSERT INTO standings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
        )
    con.close()
    if match_competition is not None:
        # Explicitly attribute legacy fixtures so they fall outside the EL/UCL
        # reconciliation scope rather than being an unattributed 2024+ hole.
        con = duckdb.connect(str(path))
        con.execute("ALTER TABLE matches ADD COLUMN competition_id TEXT")
        con.execute("UPDATE matches SET competition_id = ?", (match_competition,))
        con.close()
    if provenance is not None:
        write_provenance(path, provenance)
    return path


def write_provenance(source: Path, competition: str = "CL") -> Path:
    """Source-hash-pinned deployment provenance sidecar (contract)."""
    sidecar = source.parent / "source.provenance.json"
    sidecar.write_text(json.dumps({
        "version": 1,
        "source_sha256": rec._file_sha256(source),
        "configured_competition": competition,
        "evidence": ["test deployment env COMPETITION_ID"],
        "deployment": "synthetic-test",
    }), encoding="utf-8")
    return sidecar


# ---------------------------------------------------------------------------
# manifest construction
# ---------------------------------------------------------------------------

def make_club_roots(inv, base=900_000):
    roots, by_ext = [], {}
    for index, external_id in enumerate(inv["provider_team_ids"]):
        canonical = base + index
        roots.append({
            "provider": "espn", "external_id": external_id,
            "canonical_team_id": canonical, "member_team_ids": [canonical],
            "decision": "create-confirmed-new",
            "name": f"Reconciled Club {external_id}",
            "evidence": [f"pinned capture club id {external_id}"],
            "expected_beforeimages": [],
        })
        by_ext[external_id] = canonical
    return roots, by_ext


def insert_ops(events, competition_id, by_ext):
    ops = []
    for event in events:
        slug = event["season"]["slug"]
        if slug in _QUALIFYING_SLUGS or str(slug).startswith("qualifying"):
            continue
        stage, round_name = _STAGE_MAP[slug]
        competitors = event["competitions"][0]["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")
        ops.append({
            "kind": "insert", "external_event_id": str(event["id"]),
            "competition_id": competition_id, "season_year": event["season"]["year"],
            "home_team_id": by_ext[str(home["team"]["id"])],
            "away_team_id": by_ext[str(away["team"]["id"])],
            "stage": stage, "round": round_name, "date": event["date"],
            "home_score": None if home["score"] is None else int(home["score"]),
            "away_score": None if away["score"] is None else int(away["score"]),
            "status": "FINISHED",
            "entry_id": f"insert-{event['id']}", "reason": "synthetic pinned event",
        })
    return ops


def make_manifest(inv, club_roots, fixture_operations, **overrides):
    manifest = {
        "version": rec.MANIFEST_VERSION,
        "source_sha256": inv["source_sha256"],
        "source_path": inv["source"],
        "legacy_competition_default": inv["legacy_competition_default"],
        "source_logical_hashes": inv["source_logical_hashes"],
        "capture_index": "captures.json",
        "captures": [
            {"competition": c["competition"], "season_year": c["season_year"],
             "url": c["url"], "sha256": c["sha256"]}
            for c in inv["capture_status"]["present"]
        ],
        "club_roots": club_roots,
        "team_redirects": [],
        "team_mapping_operations": [],
        "fixture_operations": fixture_operations,
        "standings_decisions": [],
        "reference_rewiring": [],
    }
    manifest.update(overrides)
    return manifest


def write_manifest(directory: Path, manifest: dict) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "manifest.review.json").write_text(
        json.dumps({"manifest_sha256": rec.manifest_sha256(manifest),
                    "reviewer": "test", "reviewed_at": "2026-10-16T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


def _full_manifest(captures, source, directory, **overrides):
    # Manifests resolve captures relative to their own directory, so stage the
    # pinned bytes alongside the manifest.
    shutil.copytree(captures, directory, dirs_exist_ok=True)
    inv = rec.inventory(str(source), str(captures))
    roots, by_ext = make_club_roots(inv)
    ops = []
    for comp in SCOPED:
        builder = P.el_season_events if comp == "EL" else P.conf_season_events
        ops.extend(insert_ops(builder(PINNED_YEAR), comp, by_ext))
    manifest = make_manifest(inv, roots, ops, **overrides)
    return inv, write_manifest(directory, manifest), manifest


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def test_inventory_reports_captures_and_hash(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Only Club")])
    inv = rec.inventory(str(source), str(captures))

    assert inv["capture_status"]["complete"] is True
    assert inv["capture_status"]["missing"] == []
    assert len(inv["capture_status"]["present"]) == 6 * 3
    assert inv["source_sha256"] == rec._file_sha256(source)
    assert set(inv["provider_team_ids"]) == {str(i) for i in range(1, 37)}
    assert inv["legacy_competition_default"] == "CL"
    assert {"teams", "matches", "standings"} <= set(inv["schema"]["tables"])
    assert "matches" in inv["source_logical_hashes"]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_good_build_verifies_and_preserves_source(tmp_path, captures):
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(5, "Champions Club"), (6, "Rival Club")],
        matches=[(1, 5, 6, 2, 1, "2024-09-20", "FINISHED")],
        standings=[(5, 1, 1, 1, 0, 0, 2, 1, 1, 3, "2024-09-21")],
        match_competition="CL",
    )
    before = rec._file_sha256(source)
    inv, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    assert rec.validate_manifest(manifest, inv) is None

    output = tmp_path / "candidate.db"
    receipt = rec.build_candidate(str(source), str(manifest_path), str(output))

    assert receipt["state"] == "verified", receipt.get("error")
    assert receipt["verification"]["verified"] is True
    assert rec._file_sha256(source) == before, "source snapshot must be unchanged"
    assert output.stat().st_mode & 0o777 == 0o600

    # Legacy CL snapshot row preserved and attributed to the recorded default.
    con = duckdb.connect(str(output), read_only=True)
    try:
        assert con.execute(
            "SELECT competition_id FROM standings WHERE team_id = 5"
        ).fetchone()[0] == "CL"
        assert con.execute(
            "SELECT COUNT(*) FROM standings WHERE competition_id IN ('EL','UCL')"
        ).fetchone()[0] == 72
        assert con.execute(
            "SELECT COUNT(*) FROM matches WHERE competition_id = 'EL'"
        ).fetchone()[0] == 189
    finally:
        con.close()

    # Receipt and beforeimage artifacts land next to the candidate.
    assert (tmp_path / "candidate.db.receipt.json").is_file()
    assert Path(receipt["beforeimages_path"]).is_file()


def test_two_builds_are_logically_identical(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Only Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)

    first = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "a.db"))
    second = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "b.db"))

    assert first["state"] == second["state"] == "verified"
    assert first["candidate_logical_fingerprint"] == second["candidate_logical_fingerprint"]


def test_cluster_redirect_rewires_references(tmp_path, captures):
    # Two stored rows are one club; one is retired to the other and an unrelated
    # legacy match referencing the retired id is rewired.
    shutil.copytree(captures, tmp_path, dirs_exist_ok=True)
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(100, "ESPN Club 01"), (200, "ESPN Club 01"), (201, "Other Club")],
        matches=[(7, 200, 201, 1, 0, "2023-09-20", "FINISHED")],
    )
    inv = rec.inventory(str(source), str(captures))
    roots, by_ext = make_club_roots(inv)
    # Provider club "1" reuses root 100 and merges the duplicate 200.
    roots = [
        r if r["external_id"] != "1" else {
            "provider": "espn", "external_id": "1",
            "canonical_team_id": 100, "member_team_ids": [100, 200],
            "decision": "merge-reviewed-cluster",
            "evidence": ["kickoff-prefixed twin proven one club"],
            "expected_beforeimages": [],
        }
        for r in roots
    ]
    by_ext["1"] = 100
    ops = []
    for comp in SCOPED:
        builder = P.el_season_events if comp == "EL" else P.conf_season_events
        ops.extend(insert_ops(builder(PINNED_YEAR), comp, by_ext))
    manifest = make_manifest(
        inv, roots, ops,
        team_redirects=[{"retired_team_id": 200, "canonical_team_id": 100,
                         "reason": "duplicate club", "entry_id": "redirect-200"}],
        reference_rewiring=[{"table": "matches", "column": "home_team_id",
                             "old_team_id": 200, "new_team_id": 100,
                             "reason": "repoint history to root",
                             "entry_id": "rewire-200"}],
    )
    manifest_path = write_manifest(tmp_path, manifest)

    receipt = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))
    assert receipt["state"] == "verified", receipt.get("error")

    con = duckdb.connect(str(tmp_path / "c.db"), read_only=True)
    try:
        assert con.execute(
            "SELECT home_team_id FROM matches WHERE id = 7"
        ).fetchone()[0] == 100
        assert con.execute(
            "SELECT canonical_team_id FROM team_redirects WHERE retired_team_id = 200"
        ).fetchone()[0] == 100
    finally:
        con.close()


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------

def test_build_refuses_existing_output(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "candidate.db"
    output.write_bytes(b"partial")
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(output))


def test_source_symlink_refused(tmp_path):
    real = legacy_source(tmp_path / "real.db", teams=[(5, "Club")])
    link = tmp_path / "link.db"
    os.symlink(real, link)
    with pytest.raises(rec.RefusedError):
        rec.inventory(str(link), str(tmp_path))


def test_source_hardlink_refused(tmp_path):
    real = legacy_source(tmp_path / "real.db", teams=[(5, "Club")])
    os.link(real, tmp_path / "hard.db")
    with pytest.raises(rec.RefusedError):
        rec.inventory(str(tmp_path / "hard.db"), str(tmp_path))


def test_source_adjacent_wal_refused(tmp_path):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    Path(str(source) + ".wal").write_bytes(b"pending")
    with pytest.raises(rec.RefusedError):
        rec.inventory(str(source), str(tmp_path))


def test_production_bind_path_refused():
    with pytest.raises(rec.RefusedError):
        rec._source_safety("/opt/ucl-solkoff/data/ucl.db")
    with pytest.raises(rec.RefusedError):
        rec._new_output_safety("/app/data/candidate.db")


# ---------------------------------------------------------------------------
# capture integrity
# ---------------------------------------------------------------------------

def test_capture_tamper_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    inv, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    # Corrupt the staged pinned body after the manifest was written.
    body = tmp_path / "bodies/EL-2026-standings.json"
    body.write_bytes(b'{"tampered": true}')
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


def test_partial_capture_coverage_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    inv, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    manifest["captures"] = manifest["captures"][:-1]
    write_manifest(tmp_path, manifest)
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_pinned_client_never_falls_back_to_http(captures):
    client = rec.PinnedESPNClient(rec._read_capture_index(captures), captures)
    with pytest.raises(rec.RefusedError):
        client._request("https://example.invalid/not-pinned")
    assert client.served == []


# ---------------------------------------------------------------------------
# manifest validation
# ---------------------------------------------------------------------------

def _mutable_inventory(captures, tmp_path):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    return rec.inventory(str(source), str(captures))


def test_unknown_manifest_key_refused(tmp_path, captures):
    inv = _mutable_inventory(captures, tmp_path)
    roots, _ = make_club_roots(inv)
    manifest = make_manifest(inv, roots, [], surprise=True)
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_unknown_fixture_operation_refused(tmp_path, captures):
    inv = _mutable_inventory(captures, tmp_path)
    roots, _ = make_club_roots(inv)
    manifest = make_manifest(inv, roots, [
        {"kind": "delete-all", "reason": "x", "entry_id": "y"}
    ])
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_unreviewed_provider_club_refused(tmp_path, captures):
    inv = _mutable_inventory(captures, tmp_path)
    roots, _ = make_club_roots(inv)
    manifest = make_manifest(inv, roots[:-1], [])
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_root_collision_refused(tmp_path, captures):
    inv = _mutable_inventory(captures, tmp_path)
    roots, _ = make_club_roots(inv)
    roots[1]["canonical_team_id"] = 5  # collides with an existing team id
    roots[1]["member_team_ids"] = [5]
    manifest = make_manifest(inv, roots, [])
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_redirect_chain_refused(tmp_path, captures):
    inv = _mutable_inventory(captures, tmp_path)
    roots, _ = make_club_roots(inv)
    manifest = make_manifest(
        inv, roots, [],
        team_redirects=[
            {"retired_team_id": 5, "canonical_team_id": 6, "reason": "r", "entry_id": "a"},
            {"retired_team_id": 6, "canonical_team_id": 5, "reason": "r", "entry_id": "b"},
        ],
    )
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, inv)


def test_review_receipt_mismatch_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    # Mutate an entry after review; the pinned hash no longer matches.
    manifest["standings_decisions"] = [
        {"action": "delete", "competition_id": "CL", "team_id": 5,
         "reason": "later edit", "entry_id": "s1"}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


def test_stale_source_hash_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    manifest["source_sha256"] = "0" * 64
    write_manifest(tmp_path, manifest)
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------

def test_failed_phase_yields_rejected_receipt(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    # A team-mapping op whose exact expected beforeimage cannot match the (absent)
    # row fails the apply phase after validation has already passed.
    manifest["team_mapping_operations"] = [{
        "op": "replace", "team_name": "ghost", "team_id": 1234,
        "reason": "forced apply failure", "entry_id": "tm-1",
        "expected_beforeimage": {"team_name": "ghost", "team_id": 1},
    }]
    write_manifest(tmp_path, manifest)

    receipt = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))
    assert receipt["state"] == "rejected"
    assert "error" in receipt
    assert rec._file_sha256(source) == manifest["source_sha256"]


def test_uncertain_legacy_provenance_refused(tmp_path, captures):
    shutil.copytree(captures, tmp_path, dirs_exist_ok=True)
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")], provenance=None)
    inv = rec.inventory(str(source), str(captures))
    assert inv["can_build"] is False
    assert inv["legacy_competition_default"] is None
    assert inv["source_logical_hashes"] is None

    roots, _ = make_club_roots(inv)
    manifest_path = write_manifest(tmp_path, make_manifest(inv, roots, []))
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


def test_insert_date_mismatch_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    manifest["fixture_operations"][0]["date"] = "2030-01-01T00:00Z"
    write_manifest(tmp_path, manifest)

    receipt = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))
    assert receipt["state"] != "verified"
    assert rec._file_sha256(source) == manifest["source_sha256"]


def test_reference_rewiring_unknown_table_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    manifest["reference_rewiring"] = [{
        "table": "evil", "column": "team_id", "old_team_id": 5, "new_team_id": 6,
        "reason": "bogus", "entry_id": "rw",
    }]
    write_manifest(tmp_path, manifest)
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


def test_output_sidecar_conflict_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "c.db"
    Path(str(output) + ".receipt.json").write_text("{}", encoding="utf-8")
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(output))


def test_capture_path_escape_refused(captures):
    with pytest.raises(rec.RefusedError):
        rec._capture_body_path(captures, {"path": "../evil.json", "url": "u",
                                          "sha256": "x"})
    with pytest.raises(rec.RefusedError):
        rec._capture_body_path(captures, {"path": "/etc/passwd", "url": "u",
                                          "sha256": "x"})


def test_unrelated_cl_result_mutation_detected(tmp_path, captures):
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(5, "Champions Club"), (6, "Rival Club")],
        matches=[(1, 5, 6, 2, 1, "2024-09-20", "FINISHED")],
        match_competition="CL",
    )
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "c.db"
    receipt = rec.build_candidate(str(source), str(manifest_path), str(output))
    assert receipt["state"] == "verified"

    con = duckdb.connect(str(output))
    try:
        con.execute("UPDATE matches SET home_score = 9 WHERE id = 1")
    finally:
        con.close()
    result = rec.verify_candidate(str(output), str(manifest_path))
    assert result["verified"] is False


def test_stale_derived_row_detected(tmp_path, captures):
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(5, "Champions Club"), (6, "Rival Club")],
        matches=[(1, 5, 6, 2, 1, "2024-09-20", "FINISHED")],
        match_competition="CL",
    )
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "c.db"
    assert rec.build_candidate(str(source), str(manifest_path), str(output))["state"] == "verified"

    con = duckdb.connect(str(output))
    try:
        con.execute("UPDATE elo_ratings SET rating = rating + 500")
    finally:
        con.close()
    assert rec.verify_candidate(str(output), str(manifest_path))["verified"] is False


def test_qualifying_events_are_not_required_targets(tmp_path, captures):
    """A qualifying slug is skipped by captured-event coverage, like the parser."""
    details = rec._captured_event_details(
        [{"competition": "EL", "season_year": 2024,
          "url": ESPNClient._scoreboard_url("uefa.europa", 2024),
          "path": "bodies/missing.json", "sha256": "0" * 64}],
        captures,
    )
    assert details == {}


# ---------------------------------------------------------------------------
# unattributed / unknown-date scope (CRITICAL2)
# ---------------------------------------------------------------------------

def test_unattributed_2024_row_blocks_candidate(tmp_path, captures):
    source = legacy_source(
        tmp_path / "src.db", teams=[(5, "A"), (6, "B")],
        matches=[(1, 5, 6, 1, 0, "2024-09-20", "FINISHED")],
    )
    inv = rec.inventory(str(source), str(captures))
    assert any(r["match_id"] == 1 for r in inv["scoped_unmanaged_rows"])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


def test_unknown_date_row_blocks_candidate(tmp_path, captures):
    source = legacy_source(
        tmp_path / "src.db", teams=[(5, "A"), (6, "B")],
        matches=[(1, 5, 6, None, None, None, "SCHEDULED")],
    )
    inv = rec.inventory(str(source), str(captures))
    assert any(r["match_id"] == 1 for r in inv["scoped_unmanaged_rows"])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))


# ---------------------------------------------------------------------------
# capture parsing: season filter, conflicts, TBD policy (CRITICAL1)
# ---------------------------------------------------------------------------

def _local_captures(directory: Path, scoreboards: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    build_captures(directory, scoreboards)
    return directory


def test_mixed_calendar_bodies_season_filtered(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2025, 2025): [
            P.event(5001, "2025-09-10T20:00Z", 2025, "league-phase", 1, 2, 1, 0),
            P.event(6001, "2026-09-10T20:00Z", 2026, "league-phase", 3, 4, 2, 0),
        ],
    })
    main, _ = rec._parse_events(rec._read_capture_index(caps), caps)
    assert "5001" in main, "requested-season event must be kept"
    assert "6001" not in main, "adjacent-season event must be filtered by its own year"
    ids, main_ids, _ = rec._inventory_scope(rec._read_capture_index(caps), caps)
    assert {"1", "2"} <= ids
    assert "5001" in main_ids and "6001" not in main_ids


def test_conflicting_same_event_id_refused(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [
            P.event(9001, "2026-09-10T20:00Z", 2026, "league-phase", 1, 2, 1, 0),
        ],
        ("UCL", 2026, 2026): [
            P.event(9001, "2026-09-11T20:00Z", 2026, "league-phase", 3, 4, 2, 0),
        ],
    })
    with pytest.raises(rec.RefusedError):
        rec._parse_events(rec._read_capture_index(caps), caps)


def test_capture_index_order_independent(tmp_path, captures):
    forward, _ = rec._parse_events(rec._read_capture_index(captures), captures)
    reordered = tmp_path / "caps"
    shutil.copytree(captures, reordered, dirs_exist_ok=True)
    index = json.loads((captures / "captures.json").read_text(encoding="utf-8"))
    index["captures"] = list(reversed(index["captures"]))
    (reordered / "captures.json").write_text(json.dumps(index), encoding="utf-8")
    reverse, _ = rec._parse_events(rec._read_capture_index(reordered), reordered)
    assert set(forward) == set(reverse)


def test_tbd_nonfinished_event_not_required(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [
            P.event(9101, "2026-09-10T20:00Z", 2026, "league-phase", 1, 2, None, None,
                    status_name="STATUS_SCHEDULED", status_state="pre",
                    teams_resolved=False),
        ],
    })
    main, _ = rec._parse_events(rec._read_capture_index(caps), caps)
    assert main == {}, "a non-finished unresolved placeholder is skipped consistently"


def test_finished_unresolved_event_refused(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [
            P.event(9102, "2026-09-10T20:00Z", 2026, "league-phase", 1, 2, 1, 0,
                    teams_resolved=False),
        ],
    })
    with pytest.raises(rec.RefusedError):
        rec._parse_events(rec._read_capture_index(caps), caps)


# ---------------------------------------------------------------------------
# qualifier / evidence / outbound-HTTP hardening (MINOR3/5, ADDITIONAL)
# ---------------------------------------------------------------------------

def test_qualifier_wrong_season_or_competition_refused():
    event = {
        "url": "u", "sha256": "s", "event_id": "8001", "slug": "qualifying-round-1",
        "competition": "EL", "season_year": 2025, "home_external": "5",
        "away_external": "6", "date": "2026-07-10T20:00Z",
        "home_score": 1, "away_score": 0,
    }
    op = {"qualifying": {
        "event_id": "8001", "url": "u", "sha256": "s", "round": "qualifying-round-1",
        "home_external": "5", "away_external": "6", "date": "2026-07-10T20:00Z",
        "home_score": 1, "away_score": 0, "season_year": 2026}}
    actual = {"competition_id": "EL", "date": "2026-07-10T20:00Z",
              "home_score": 1, "away_score": 0}
    with pytest.raises(rec.RefusedError):
        rec._validate_qualifier_evidence(op, actual, {"8001": event})

    cross_comp = dict(event, competition="UCL", season_year=2026)
    with pytest.raises(rec.RefusedError):
        rec._validate_qualifier_evidence(op, actual, {"8001": cross_comp})


def test_adopt_freeform_string_evidence_refused():
    with pytest.raises(rec.RefusedError):
        rec._validate_correction_evidence_shape(
            {"evidence": ["trust me"], "external_event_id": "e", "season_year": 2026,
             "competition_id": "EL"},
            {"capture_status": {"present": []}, "provider_main_event_ids": []},
        )


def test_zero_outbound_http_during_build_and_verify(tmp_path, captures, monkeypatch):
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("outbound HTTP attempted during reconciliation")

    monkeypatch.setattr("backend.espn_client.httpx.Client", _boom)
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    receipt = rec.build_candidate(str(source), str(manifest_path), str(tmp_path / "c.db"))
    assert receipt["state"] == "verified"
    assert calls == [], "build+verify must make zero outbound HTTP calls"


# ---------------------------------------------------------------------------
# iteration4: date rule, capture index consistency, NULL-comp qualifier,
# copy-failure safety, malformed inventory, rogue mapping
# ---------------------------------------------------------------------------

def _target_for(source: Path, competition: str):
    target = Path(str(source) + ".target.db")
    shutil.copy2(str(source), str(target))
    previous = os.environ.get("COMPETITION_ID")
    os.environ["COMPETITION_ID"] = competition
    try:
        rec.Database(str(target)).close()
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_ID", None)
        else:
            os.environ["COMPETITION_ID"] = previous
    return rec.Database(str(target)), target


def _apply_target(db, target: Path, manifest: dict, captures_dir: Path) -> dict:
    entries = rec._read_capture_index(captures_dir)
    beforeimages = rec._BeforeImages(str(target) + ".bi.jsonl")
    try:
        with db.transaction():
            return rec._apply_manifest(db, manifest, Path(captures_dir), entries,
                                       beforeimages)
    finally:
        beforeimages.close()


def test_adopt_date_may_not_override_pinned_capture():
    detail = {
        "competition": "EL", "season_year": 2026, "stage": "LEAGUE_STAGE",
        "round": None, "status": "FINISHED", "date": "2026-09-15T20:00Z",
        "home_score": 2, "away_score": 1,
        "home_external": "1", "away_external": "2",
    }
    # Legacy date equals the op date, but both differ from the pinned capture; a
    # signed review + exact beforeimage must still refuse.
    op = {
        "kind": "adopt", "external_event_id": "e", "competition_id": "EL",
        "season_year": 2026, "home_team_id": 100, "away_team_id": 200,
        "stage": "LEAGUE_STAGE", "round": None, "date": "2026-09-16T20:00Z",
        "status": "FINISHED", "home_score": 2, "away_score": 1,
        "match_id": 5, "expected_beforeimage": {},
        "reason": "reschedule", "entry_id": "a1",
        "evidence": [{
            "url": "u", "sha256": "s", "event_id": "e", "season_year": 2026,
            "competition_id": "EL", "correction_reason": "reschedule",
            "expected_beforeimage": {},
        }],
    }
    with pytest.raises(rec.RefusedError):
        rec._validate_fixture_event(op, detail, {"1": 100, "2": 200}, adopt=True)


def test_null_competition_qualifier_attributed_and_preserved(tmp_path):
    comp = "EL"
    qevent = P.event(8002, "2026-07-10T20:00Z", 2026, "qualifying-round-1", 5, 6, 1, 0)
    caps = _local_captures(tmp_path / "caps", {(comp, 2026, 2026): [qevent]})
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(5, "Chaser Club"), (6, "Rival Club")],
        matches=[(5, 5, 6, 1, 0, "2026-07-10T20:00Z", "FINISHED")],
        provenance=comp,
    )
    inv = rec.inventory(str(source), str(caps))
    assert any(r["match_id"] == 5 and r["competition_id"] is None
               for r in inv["scoped_unmanaged_rows"])

    roots, _ = make_club_roots(inv)
    for root in roots:
        if root["external_id"] == "5":
            root.update(decision="reuse", canonical_team_id=5, member_team_ids=[5])
        elif root["external_id"] == "6":
            root.update(decision="reuse", canonical_team_id=6, member_team_ids=[6])

    _, qualifying = rec._parse_events(rec._read_capture_index(caps), caps)
    pinned = qualifying["8002"]
    db, target = _target_for(source, comp)
    try:
        row = rec._row_dict(db.conn, "matches", "id = ?", (5,))
        op = {
            "kind": "qualifier", "match_id": 5,
            "reason": "proven NULL-competition qualifying tie",
            "entry_id": "q-5", "expected_beforeimage": row,
            "qualifying": {
                "url": pinned["url"], "sha256": pinned["sha256"], "event_id": "8002",
                "home_external": pinned["home_external"],
                "away_external": pinned["away_external"], "date": pinned["date"],
                "round": pinned["slug"], "home_score": pinned["home_score"],
                "away_score": pinned["away_score"], "season_year": 2026,
            },
        }
        manifest = make_manifest(inv, roots, [op])
        assert rec.validate_manifest(manifest, inv) is None
        _apply_target(db, target, manifest, caps)
        kept = db.fetchone(
            'SELECT stage, "round", competition_id FROM matches WHERE id = 5'
        )
        assert kept == ("QUALIFYING", "qualifying-round-1", comp)
        assert rec._count_unmanaged(db.conn) == 0
    finally:
        db.close()


def test_capture_index_duplicate_url_consistent_ok(captures):
    rec._validate_capture_index(rec._read_capture_index(captures), captures)


def test_capture_index_duplicate_url_conflict_refused(tmp_path, captures):
    from collections import Counter

    reordered = tmp_path / "caps"
    shutil.copytree(captures, reordered, dirs_exist_ok=True)
    index = json.loads((captures / "captures.json").read_text(encoding="utf-8"))
    counts = Counter(e["url"] for e in index["captures"])
    duplicate = next(url for url, count in counts.items() if count > 1)
    for entry in index["captures"]:
        if entry["url"] == duplicate:
            entry["sha256"] = "0" * 64
            break
    (reordered / "captures.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(rec.RefusedError):
        rec._validate_capture_index(rec._read_capture_index(reordered), reordered)


def test_correction_proof_source_order_invariant(tmp_path):
    event = P.event(7001, "2026-09-10T20:00Z", 2026, "league-phase", 1, 2, 1, 0)
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [event],
        ("EL", 2026, 2027): [event],
    })
    forward, _ = rec._parse_events(rec._read_capture_index(caps), caps)
    reordered = tmp_path / "caps2"
    shutil.copytree(caps, reordered, dirs_exist_ok=True)
    index = json.loads((caps / "captures.json").read_text(encoding="utf-8"))
    index["captures"] = list(reversed(index["captures"]))
    (reordered / "captures.json").write_text(json.dumps(index), encoding="utf-8")
    reverse, _ = rec._parse_events(rec._read_capture_index(reordered), reordered)
    assert forward["7001"]["url"] == reverse["7001"]["url"]
    assert forward["7001"]["sha256"] == reverse["7001"]["sha256"]


def test_reserve_candidate_copy_failure_safe_refusal(tmp_path, captures, monkeypatch):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "c.db"
    before = rec._file_sha256(source)

    def _boom(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr("backend.espn_reconcile.shutil.copyfileobj", _boom)
    with pytest.raises(rec.RefusedError):
        rec.build_candidate(str(source), str(manifest_path), str(output))
    assert not output.exists(), "the owned partial candidate must be removed"
    assert rec._file_sha256(source) == before


def test_inventory_malformed_event_signals_failure(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [
            P.event(9501, "2026-09-10T20:00Z", 2026, "bogus-stage", 1, 2, 1, 0),
        ],
    })
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")],
                           match_competition="CL")
    with pytest.raises(rec.RefusedError):
        rec.inventory(str(source), str(caps))


def test_rogue_event_mapping_detected(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, _ = _full_manifest(captures, source, tmp_path)
    output = tmp_path / "c.db"
    assert rec.build_candidate(str(source), str(manifest_path),
                               str(output))["state"] == "verified"

    con = duckdb.connect(str(output))
    try:
        match_id = con.execute(
            "SELECT match_id FROM provider_match_mappings WHERE provider = 'espn' "
            "LIMIT 1"
        ).fetchone()[0]
        con.execute(
            "DELETE FROM provider_match_mappings WHERE provider = 'espn' AND match_id = ?",
            (match_id,),
        )
        con.execute(
            "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
            "VALUES ('espn', '999999999', ?)", (match_id,),
        )
    finally:
        con.close()
    assert rec.verify_candidate(str(output), str(manifest_path))["verified"] is False


# ---------------------------------------------------------------------------
# iteration5: qualifier participant resolution, unattributed inverse check,
# reviewed root beforeimages
# ---------------------------------------------------------------------------

def _qual_pair_event():
    return {
        "url": "u", "sha256": "s", "event_id": "1", "slug": "qualifying-round-1",
        "competition": "EL", "season_year": 2026,
        "home_external": "1", "away_external": "2",
        "date": "2026-07-10T20:00Z", "home_score": 1, "away_score": 0,
    }


def _qual_op():
    return {"qualifying": {
        "event_id": "1", "url": "u", "sha256": "s", "round": "qualifying-round-1",
        "home_external": "1", "away_external": "2", "date": "2026-07-10T20:00Z",
        "home_score": 1, "away_score": 0, "season_year": 2026}}


def test_qualifier_different_clubs_same_date_score_refused():
    actual = {
        "competition_id": "EL", "date": "2026-07-10T20:00Z",
        "home_score": 1, "away_score": 0, "home_team_id": 7, "away_team_id": 8,
    }
    root_map = {("p", "1"): 100, ("p", "2"): 200,
                ("t", 100): 100, ("t", 200): 200, ("t", 7): 7, ("t", 8): 8}
    with pytest.raises(rec.RefusedError):
        rec._validate_qualifier_evidence(
            _qual_op(), actual, {"1": _qual_pair_event()}, root_map
        )


def test_qualifier_legacy_member_resolves_to_approved_root():
    # Legacy row uses an approved retired member id (300 -> root 100) plus the
    # other root (200); resolved clubs match the pinned event.
    actual = {
        "competition_id": "EL", "date": "2026-07-10T20:00Z",
        "home_score": 1, "away_score": 0, "home_team_id": 300, "away_team_id": 200,
    }
    root_map = {("p", "1"): 100, ("p", "2"): 200,
                ("t", 100): 100, ("t", 300): 100, ("t", 200): 200}
    attribution = rec._validate_qualifier_evidence(
        _qual_op(), actual, {"1": _qual_pair_event()}, root_map
    )
    assert attribution == "EL"


def test_rogue_unattributed_mapping_detected_isolated(tmp_path):
    caps = _local_captures(tmp_path / "caps", {
        ("EL", 2026, 2026): [
            P.event(7002, "2026-09-10T20:00Z", 2026, "league-phase", 1, 2, 1, 0),
        ],
    })
    target = tmp_path / "t.db"
    db = rec.Database(str(target))
    try:
        db.execute("INSERT INTO teams (id, name) VALUES (900001, 'Root One')")
        db.execute("INSERT INTO teams (id, name) VALUES (900002, 'Root Two')")
        db.execute(
            "INSERT INTO matches (id, home_team_id, away_team_id, home_score, "
            "away_score, \"date\", status, stage, competition_id) "
            "VALUES (-1, 900001, 900002, 1, 0, '2026-10-01', 'FINISHED', NULL, NULL)"
        )
        db.execute(
            "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
            "VALUES ('espn', '999999999', -1)"
        )
        db.commit()
        manifest = {"club_roots": [{
            "provider": "espn", "external_id": "1", "canonical_team_id": 900001,
            "member_team_ids": [900001], "decision": "create-confirmed-new",
            "name": "Root One", "evidence": ["reviewed"],
        }]}
        entries = rec._read_capture_index(caps)
        rogue = rec._rogue_event_mappings(db.conn, manifest, entries, caps)
        assert rogue, "an unattributed mapped row with an uncaptured event must fail"
    finally:
        db.close()


def test_club_root_wrong_expected_beforeimage_refused(tmp_path, captures):
    source = legacy_source(tmp_path / "src.db", teams=[(5, "Club")])
    _, manifest_path, manifest = _full_manifest(captures, source, tmp_path)
    manifest["club_roots"][0]["expected_beforeimages"] = [{
        "table": "teams", "key": {"id": 5},
        "before": {"id": 5, "name": "WRONG", "code": None, "crest": None},
    }]
    write_manifest(tmp_path, manifest)
    receipt = rec.build_candidate(str(source), str(manifest_path),
                                  str(tmp_path / "c.db"))
    assert receipt["state"] != "verified"
    assert rec._file_sha256(source) == manifest["source_sha256"]
