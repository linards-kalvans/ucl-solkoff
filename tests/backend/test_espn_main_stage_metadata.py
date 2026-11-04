"""Focused tests for the optional, capture-proofed ``team_updates`` operation.

The tool extension is additive: a manifest 2 without ``team_updates`` stays
valid, while an optional list may clean a real leading kickoff artifact from an
existing root's display name and fill *missing* code/crest from an exact pinned
provider record. Every case runs against real temporary DuckDB files and the
existing synthetic ESPN capture builders; nothing touches the network.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from backend import espn_reconcile as rec
from backend.competitions import espn_slug_for
from backend.espn_client import ESPNClient
from tests.backend import espn_payloads as P
from tests.backend.test_espn_reconcile import (
    build_captures,
    insert_ops,
    legacy_source,
    make_club_roots,
    make_manifest,
    write_manifest,
    write_provenance,
)

SCOPED = ("EL", "UCL")
YEARS = (2024, 2025, 2026)
PINNED_YEAR = 2026
BASE_TEAM_ID = 900_000


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def captures(tmp_path_factory):
    directory = tmp_path_factory.mktemp("main-stage-metadata-captures")
    scoreboards = {}
    for competition in SCOPED:
        builder = P.el_season_events if competition == "EL" else P.conf_season_events
        scoreboards[(competition, PINNED_YEAR, PINNED_YEAR)] = builder(PINNED_YEAR)
    build_captures(directory, scoreboards)
    return directory


def _kickoff_name(index: int) -> str:
    return f"12 Sep 2026 - {P._team_name(index)}"


def _team_row(source: Path, team_id: int) -> dict:
    conn = duckdb.connect(str(source), read_only=True)
    try:
        columns = rec._table_columns(conn, "teams")
        row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        assert row is not None
        return {c: rec._normalize_value(v) for c, v in zip(columns, row)}
    finally:
        conn.close()


def _reuse_source(tmp_path: Path, captures: Path):
    """A source whose 36 teams all reuse the captured provider clubs."""
    source = legacy_source(
        tmp_path / "src.db",
        teams=[(BASE_TEAM_ID + i, f"Placeholder {i}") for i in range(36)],
    )
    preliminary = rec.inventory(str(source), str(captures))
    roots, by_ext = make_club_roots(preliminary)
    conn = duckdb.connect(str(source))
    try:
        for external_id, canonical in by_ext.items():
            conn.execute("UPDATE teams SET name = ? WHERE id = ?",
                         (_kickoff_name(int(external_id)), canonical))
    finally:
        conn.close()
    write_provenance(source, "CL")
    inventory = rec.inventory(str(source), str(captures))
    for root in roots:
        root["decision"] = "reuse"
    return source, inventory, roots, by_ext


def _prepare(tmp_path: Path, captures: Path, source: Path, roots, updates):
    bundle = tmp_path / "bundle"
    shutil.copytree(captures, bundle, dirs_exist_ok=True)
    inventory = rec.inventory(str(source), str(captures))
    root_by_external = {
        str(root["external_id"]): int(root["canonical_team_id"]) for root in roots
    }
    operations = []
    for competition in SCOPED:
        builder = P.el_season_events if competition == "EL" else P.conf_season_events
        operations.extend(
            insert_ops(builder(PINNED_YEAR), competition, root_by_external)
        )
    manifest = make_manifest(inventory, roots, operations)
    if updates is not None:
        manifest["team_updates"] = updates
    path = write_manifest(bundle, manifest)
    return inventory, path, manifest


def _standing_proof(inventory, provider_id, competition="EL", season=PINNED_YEAR):
    url = ESPNClient._standings_url(espn_slug_for(competition), season)
    for present in inventory["capture_status"]["present"]:
        if present["url"] == url:
            return {
                "url": url,
                "sha256": present["sha256"],
                "provider_id": str(provider_id),
            }
    raise AssertionError(f"no standings capture for {competition} {season}")


def _scoreboard_proof(inventory, provider_id, competition="EL", year=PINNED_YEAR):
    url = ESPNClient._scoreboard_url(espn_slug_for(competition), year)
    for present in inventory["capture_status"]["present"]:
        if present["url"] == url:
            return {
                "url": url,
                "sha256": present["sha256"],
                "provider_id": str(provider_id),
            }
    raise AssertionError(f"no scoreboard capture for {competition} {year}")


def _update(source: Path, inventory, by_ext, external_id, fields, **overrides):
    canonical = by_ext[str(external_id)]
    update = {
        "canonical_team_id": canonical,
        "entry_id": f"team-update-{external_id}",
        "reason": "remove the recorded kickoff prefix / fill missing metadata",
        "expected_beforeimage": _team_row(source, canonical),
        "fields": dict(fields),
        "metadata_proof": _standing_proof(inventory, external_id),
    }
    update.update(overrides)
    return update


def _build(source: Path, manifest_path: Path, output: Path) -> dict:
    try:
        return rec.build_candidate(str(source), str(manifest_path), str(output))
    except rec.RefusedError as exc:
        return {"state": "refused", "error": str(exc)}


def _state(result: dict) -> str:
    return str(result.get("state"))


# ---------------------------------------------------------------------------
# optional-key compatibility
# ---------------------------------------------------------------------------

def test_manifest_without_team_updates_remains_valid(tmp_path, captures):
    source, inventory, roots, _ = _reuse_source(tmp_path, captures)
    _, manifest_path, manifest = _prepare(tmp_path, captures, source, roots, None)

    assert "team_updates" not in manifest
    assert rec.validate_manifest(manifest, inventory) is None

    receipt = rec.build_candidate(
        str(source), str(manifest_path), str(tmp_path / "candidate.db")
    )
    assert receipt["state"] == "verified", receipt.get("error")
    assert receipt["operation_counts"]["team_updates"] == 0


# ---------------------------------------------------------------------------
# correct kickoff cleanup / missing metadata fill
# ---------------------------------------------------------------------------

def test_kickoff_cleanup_fills_only_missing_code_and_crest(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {
        "name": P._team_name(1),
        "code": P._team_abbr(1),
        "crest": "https://cdn.example/1.png",
    })
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    receipt = _build(source, manifest_path, candidate)
    assert _state(receipt) == "verified", receipt.get("error")

    target = by_ext["1"]
    conn = duckdb.connect(str(candidate), read_only=True)
    try:
        row = conn.execute(
            "SELECT name, code, crest FROM teams WHERE id = ?", (target,)
        ).fetchone()
    finally:
        conn.close()
    assert row == (P._team_name(1), P._team_abbr(1), "https://cdn.example/1.png")

    # Another club's row was not touched at all (still kickoff-prefixed).
    untouched = by_ext["2"]
    assert _team_row(candidate, untouched) == _team_row(source, untouched)
    before_lines = Path(receipt["beforeimages_path"]).read_text(encoding="utf-8")
    operations = [json.loads(line)["operation"]
                  for line in before_lines.splitlines() if line.strip()]
    assert "team-update" in operations


def test_scoreboard_root_proof_is_supported(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {
        "name": P._team_name(1),
        "code": P._team_abbr(1),
        "crest": "https://cdn.example/1.png",
    })
    update["metadata_proof"] = _scoreboard_proof(inventory, 1)
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    receipt = _build(source, manifest_path, candidate)
    assert _state(receipt) == "verified", receipt.get("error")
    row = _team_row(candidate, by_ext["1"])
    assert row["name"] == P._team_name(1)
    assert row["code"] == P._team_abbr(1)
    assert row["crest"] == "https://cdn.example/1.png"


def test_name_cleanup_preserves_protected_code_and_crest(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    target = by_ext["1"]
    conn = duckdb.connect(str(source))
    try:
        conn.execute(
            "UPDATE teams SET code = ?, crest = ? WHERE id = ?",
            ("KEEP", "https://club.example/keep.png", target),
        )
    finally:
        conn.close()
    write_provenance(source, "CL")
    inventory = rec.inventory(str(source), str(captures))

    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    receipt = _build(source, manifest_path, candidate)
    assert _state(receipt) == "verified", receipt.get("error")
    row = _team_row(candidate, target)
    assert row["name"] == P._team_name(1)
    assert row["code"] == "KEEP"
    assert row["crest"] == "https://club.example/keep.png"


def test_existing_nonempty_code_cannot_be_overwritten(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    target = by_ext["1"]
    conn = duckdb.connect(str(source))
    try:
        conn.execute("UPDATE teams SET code = ? WHERE id = ?", ("KEEP", target))
    finally:
        conn.close()
    write_provenance(source, "CL")
    inventory = rec.inventory(str(source), str(captures))

    update = _update(source, inventory, by_ext, "1", {"code": P._team_abbr(1)})
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    with pytest.raises(rec.RefusedError, match="protected"):
        rec.build_candidate(
            str(source), str(manifest_path), str(tmp_path / "candidate.db")
        )


# ---------------------------------------------------------------------------
# proof/target/beforeimage refusals
# ---------------------------------------------------------------------------

def _mutate_sha(update):
    update["metadata_proof"]["sha256"] = "0" * 64


def _mutate_provider(update):
    update["metadata_proof"]["provider_id"] = "999"


def _mutate_code(update):
    update["fields"]["code"] = "E99"


def _mutate_crest(update):
    update["fields"]["crest"] = "https://wrong.example/crest.png"


def _mutate_name(update):
    update["fields"]["name"] = "Semantically Different Club"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (_mutate_sha, "pinned manifest capture"),
        (_mutate_provider, "does not match the declared root external_id"),
        (_mutate_code, "contradicts"),
        (_mutate_crest, "contradicts"),
        (_mutate_name, "neither"),
    ],
)
def test_wrong_proof_or_values_refuse(tmp_path, captures, mutate, expected):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {
        "name": P._team_name(1),
        "code": P._team_abbr(1),
        "crest": "https://cdn.example/1.png",
    })
    mutate(update)
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    result = _build(source, manifest_path, tmp_path / "candidate.db")

    assert _state(result) in {"refused", "rejected"}
    assert expected in str(result.get("error")), result.get("error")


def test_present_foreign_provider_refuses_even_with_self_consistent_fields(
    tmp_path, captures
):
    """A present other club's record must not rename/fill the declared root."""
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {
        "name": P._team_name(2),
        "code": P._team_abbr(2),
        "crest": "https://cdn.example/2.png",
    })
    update["metadata_proof"] = _standing_proof(inventory, 2)
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    result = _build(source, manifest_path, tmp_path / "candidate.db")

    assert _state(result) in {"refused", "rejected"}
    assert "does not match the declared root external_id" in str(
        result.get("error")
    ), result.get("error")


def test_unknown_update_target_is_refused(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    update["canonical_team_id"] = 123_456
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])

    with pytest.raises(rec.RefusedError, match="not a declared club-root"):
        rec.build_candidate(
            str(source), str(manifest_path), str(tmp_path / "candidate.db")
        )


def test_stale_beforeimage_refuses_before_any_write(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    update["expected_beforeimage"]["name"] = "Stale Other Name"
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    result = _build(source, manifest_path, candidate)
    assert _state(result) == "rejected"
    assert "beforeimage" in str(result.get("error")).lower()
    # No mutation reached the candidate copy.
    assert _team_row(candidate, by_ext["1"]) == _team_row(source, by_ext["1"])


def test_new_root_update_cannot_declare_a_beforeimage(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    new_root = next(r for r in roots if r["external_id"] == "1")
    new_root.update({
        "decision": "create-confirmed-new",
        "canonical_team_id": 950_000,
        "member_team_ids": [950_000],
        "name": P._team_name(1),
        "code": P._team_abbr(1),
        "crest": "https://cdn.example/1.png",
        "metadata_proof": _standing_proof(inventory, 1),
    })
    update = {
        "canonical_team_id": 950_000,
        "entry_id": "new-root-update",
        "reason": "not allowed on a confirmed-new root",
        "expected_beforeimage": {"id": 950_000, "name": None, "code": None, "crest": None},
        "fields": {"name": P._team_name(1)},
        "metadata_proof": _standing_proof(inventory, 1),
    }
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])

    with pytest.raises(rec.RefusedError, match="must be null"):
        rec.build_candidate(
            str(source), str(manifest_path), str(tmp_path / "candidate.db")
        )


# ---------------------------------------------------------------------------
# confirmed-new root creation metadata
# ---------------------------------------------------------------------------

def _new_root_source(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    new_root = next(r for r in roots if r["external_id"] == "1")
    new_root.update({
        "decision": "create-confirmed-new",
        "canonical_team_id": 950_000,
        "member_team_ids": [950_000],
        "name": P._team_name(1),
        "code": P._team_abbr(1),
        "crest": "https://cdn.example/1.png",
        "metadata_proof": _standing_proof(inventory, 1),
        "evidence": ["pinned capture club id 1"],
    })
    return source, inventory, roots, by_ext, new_root


def test_confirmed_new_root_gets_capture_proofed_metadata(tmp_path, captures):
    source, inventory, roots, _, _ = _new_root_source(tmp_path, captures)
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, None)
    candidate = tmp_path / "candidate.db"

    receipt = rec.build_candidate(str(source), str(manifest_path), str(candidate))
    assert receipt["state"] == "verified", receipt.get("error")
    row = _team_row(candidate, 950_000)
    assert row["name"] == P._team_name(1)
    assert row["code"] == P._team_abbr(1)
    assert row["crest"] == "https://cdn.example/1.png"


def test_confirmed_new_root_wrong_metadata_refuses(tmp_path, captures):
    source, inventory, roots, _, new_root = _new_root_source(tmp_path, captures)
    new_root["code"] = "E99"
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, None)

    result = _build(source, manifest_path, tmp_path / "candidate.db")
    assert _state(result) == "rejected"
    assert "contradicts" in str(result.get("error"))


def test_confirmed_new_root_foreign_present_provider_refuses(tmp_path, captures):
    source, inventory, roots, _, new_root = _new_root_source(tmp_path, captures)
    new_root.update({
        "name": P._team_name(2),
        "code": P._team_abbr(2),
        "crest": "https://cdn.example/2.png",
        "metadata_proof": _standing_proof(inventory, 2),
    })
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, None)

    result = _build(source, manifest_path, tmp_path / "candidate.db")
    assert _state(result) == "rejected"
    assert "does not match the declared root external_id" in str(
        result.get("error")
    ), result.get("error")


def test_confirmed_new_root_team_update_with_null_beforeimage_applies(
    tmp_path, captures
):
    source, inventory, roots, _, new_root = _new_root_source(tmp_path, captures)
    # The root is created without metadata; one reviewed update fills it after
    # creation (the only valid null-beforeimage path).
    for field in ("code", "crest", "metadata_proof"):
        new_root.pop(field, None)
    update = {
        "canonical_team_id": 950_000,
        "entry_id": "new-root-created-metadata",
        "reason": "fill capture-proofed metadata after confirmed-new creation",
        "expected_beforeimage": None,
        "fields": {
            "name": P._team_name(1),
            "code": P._team_abbr(1),
            "crest": "https://cdn.example/1.png",
        },
        "metadata_proof": _standing_proof(inventory, 1),
    }
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    receipt = _build(source, manifest_path, candidate)
    assert _state(receipt) == "verified", receipt.get("error")
    row = _team_row(candidate, 950_000)
    assert row["name"] == P._team_name(1)
    assert row["code"] == P._team_abbr(1)
    assert row["crest"] == "https://cdn.example/1.png"
    before_lines = Path(receipt["beforeimages_path"]).read_text(encoding="utf-8")
    assert any(
        json.loads(line)["operation"] == "team-update"
        for line in before_lines.splitlines() if line.strip()
    )


# ---------------------------------------------------------------------------
# invalid field shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fields, match",
    [
        ({}, "non-empty fields"),
        ({"id": "7"}, "unknown key"),
        ({"name": ""}, "real string"),
        ({"name": " "}, "real string"),
    ],
)
def test_invalid_update_fields_refuse(tmp_path, captures, fields, match):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    update["fields"] = fields
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])

    with pytest.raises(rec.RefusedError, match=match):
        rec.build_candidate(
            str(source), str(manifest_path), str(tmp_path / "candidate.db")
        )


def test_noop_name_update_refuses(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    update["fields"] = {"name": update["expected_beforeimage"]["name"]}
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])

    with pytest.raises(rec.RefusedError, match="would not change"):
        rec.build_candidate(
            str(source), str(manifest_path), str(tmp_path / "candidate.db")
        )


# ---------------------------------------------------------------------------
# transaction safety / verification
# ---------------------------------------------------------------------------

def test_failed_apply_rolls_back_team_update(tmp_path, captures, monkeypatch):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    original = rec._apply_resolved_team_updates

    def exploding_apply(db, resolved, beforeimages):
        original(db, resolved, beforeimages)
        raise RuntimeError("forced failure after the team update")

    monkeypatch.setattr(rec, "_apply_resolved_team_updates", exploding_apply)
    result = _build(source, manifest_path, candidate)

    assert _state(result) == "rejected"
    assert "forced failure" in str(result.get("error"))
    # The candidate copy exists but the update was rolled back.
    assert _team_row(candidate, by_ext["1"]) == _team_row(source, by_ext["1"])


def test_candidate_tamper_is_detected(tmp_path, captures):
    source, inventory, roots, by_ext = _reuse_source(tmp_path, captures)
    update = _update(source, inventory, by_ext, "1", {"name": P._team_name(1)})
    _, manifest_path, _ = _prepare(tmp_path, captures, source, roots, [update])
    candidate = tmp_path / "candidate.db"

    receipt = rec.build_candidate(str(source), str(manifest_path), str(candidate))
    assert receipt["state"] == "verified", receipt.get("error")

    unrelated = by_ext["3"]
    conn = duckdb.connect(str(candidate))
    try:
        conn.execute("UPDATE teams SET name = 'TAMPERED' WHERE id = ?", (unrelated,))
    finally:
        conn.close()

    verification = rec.verify_candidate(str(candidate), str(manifest_path))
    assert verification["verified"] is False
    assert verification["checks"]["independent_rebuild_matches"]["ok"] is False
    assert verification["checks"]["unrelated_team_rows_preserved"]["ok"] is False


def test_kickoff_prefix_accepts_plain_clock_and_preserves_club_prefixes():
    assert rec._strip_kickoff_prefix("18:45 Jagiellonia Białystok") == "Jagiellonia Białystok"
    assert rec._strip_kickoff_prefix("21:00 Manchester City FC") == "Manchester City FC"
    assert rec._strip_kickoff_prefix("2024-09-25T18:45Z Club") == "Club"
    assert rec._strip_kickoff_prefix("1. FC Union Berlin") is None
    assert rec._strip_kickoff_prefix("FC Bayern München") is None
