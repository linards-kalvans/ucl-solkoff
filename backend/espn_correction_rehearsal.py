"""Copy-only ESPN correction rehearsal tool.

This module is deliberately **not** a runtime component and **not** the
full-takeover reconciler. It applies an explicit, reviewed correction profile to
a disposable copy of the immutable audit snapshot and then independently verifies
that copy. It never writes to the source, a production path, an API, the
scheduler or the network.

The successful receipt uses a deliberately weaker vocabulary than the full
takeover tool: ``state: rehearsed``, ``rehearsal_only: true``,
``production_ready: false`` and ``full_takeover: false``. A correction profile
can therefore never be mistaken for a production approval or a full-takeover
verification. The full-takeover manifest and its unapproved 151-club draft remain
separate gates, as do the missing main-stage events.

The profile is closed and strict:

* only enumerated team creations, display renames, redirects, alias corrections,
  match field corrections and declared reference rewires are allowed;
* every changed match row carries an exact 12-column beforeimage and afterimage;
* qualifier evidence is parsed from a pinned *qualifying* ESPN body, never from a
  main-stage body, and is not passed through the 36-club standings validator;
* older raw-text corrections are allowed only for explicitly listed match IDs and
  are independently reparsed from the pinned text file, including its date
  header and section, using the declared season rather than the generic July
  inference;
* no match insert, delete or arbitrary SQL is expressible.

See ``docs/superpowers/plans/2026-10-24-copy-only-correction-rehearsal.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import duckdb

from backend import espn_reconcile as _rec
from backend.competitions import (
    PROVIDER_ESPN,
    espn_slug_for,
    normalize_competition,
)
from backend.database import Database
from backend.elo_calculator import EloCalculator
from backend.espn_client import _QUALIFYING_SLUGS
from backend.solkoff_calculator import SolkoffCalculator
from backend.team_identity import TeamIdentityError, TeamRedirectResolver

TOOL_REVISION = "espn-correction-rehearsal/5"
PROFILE = "copy-only-corrections"
PROFILE_VERSION = 3
REVIEW_SCOPE = PROFILE
CAPTURE_INDEX_NAME = "captures.json"

SOURCE_COMPETITIONS: Tuple[str, ...] = ("CL", "EL", "UCL")
DERIVED_COMPETITIONS: Tuple[str, ...] = ("CL", "EL", "UCL")

# Real ESPN qualifying scoreboards use their own league slugs and (for the
# audited fixture case) event season slugs such as ``first-round``. Both are
# explicitly recognised here. This is intentionally separate from the main-stage
# parser so a qualifier body can never be presented as a main-stage capture.
QUALIFYING_SLUGS_BY_COMPETITION: Dict[str, str] = {
    "EL": "uefa.europa_qual",
    "UCL": "uefa.europa.conf_qual",
}
QUALIFYING_ROUND_NAMES: Dict[str, str] = {
    "preliminary-round": "Preliminary Round",
    "first-round": "First Round",
    "second-round": "Second Round",
    "third-round": "Third Round",
    "playoff-round": "Play-offs",
    "play-offs": "Play-offs",
    "qualifying-first-round": "First Qualifying Round",
    "qualifying-second-round": "Second Qualifying Round",
    "qualifying-third-round": "Third Qualifying Round",
    "first-qualifying-round": "First Qualifying Round",
    "second-qualifying-round": "Second Qualifying Round",
    "third-qualifying-round": "Third Qualifying Round",
    "playoffs": "Play-offs",
}

_WRITABLE_MATCH_FIELDS = frozenset({
    "date", "stage", "round", "home_score", "away_score",
    "home_team_id", "away_team_id",
})
_MATCH_COLUMNS = (
    "id", "home_team_id", "away_team_id", "home_score", "away_score",
    "matchday", "date", "status", "stage", "round", "group_name",
    "competition_id",
)
_PROOF_KINDS = frozenset({"espn-main", "espn-qualifier", "raw-text"})
_RAW_ROUND_NAMES: Dict[str, str] = {
    "semifinals": "Semi-finals",
    "semi-finals": "Semi-finals",
    "quarterfinals": "Quarter-finals",
    "quarter-finals": "Quarter-finals",
    "final": "Final",
    "playoffs": "Play-offs",
    "play-offs": "Play-offs",
    "round of 16": "Round of 16",
}

_MANIFEST_KEYS = frozenset({
    "version", "profile", "source_sha256", "source_path",
    "legacy_competition_default", "source_logical_hashes", "capture_index",
    "captures", "expected", "pending", "audit_season_year",
    "audit_season_bounds", "team_roots", "team_creations", "team_renames",
    "team_redirects", "alias_corrections", "reviewed_alias_bindings",
    "legacy_exception_allowlist", "identity_exception_allowlist",
    "metadata_discrepancies", "mixed_identity_groups", "match_corrections",
    "reference_rewiring",
})
_EXPECTED_KEYS = frozenset({
    "match_rows", "source_team_rows", "qualifying_corrections",
    "wrong_club_slots", "identity_corrections", "legacy_nonidentity_exceptions",
    "team_renames", "created_team_rows", "redirects", "mixed_source_rows",
    "mixed_split_rows", "mixed_remaining_rows",
})
_PENDING_KEYS = frozenset({
    "main_stage_events", "full_takeover_clubs_unapproved", "note",
})
_AUDIT_BOUNDS_KEYS = frozenset({"start", "end"})
_CAPTURE_KEYS = frozenset({"competition", "season_year", "url", "sha256"})
_ROOT_KEYS = frozenset({
    "entry_id", "external_id", "canonical_team_id", "member_team_ids",
    "identity_name", "name_variants", "country", "proof", "member_proofs",
    "expected_beforeimage",
})
_CREATION_KEYS = frozenset({
    "entry_id", "team_id", "name", "reason", "identity_name", "country",
    "external_id", "proof",
})
_RENAME_KEYS = frozenset({"entry_id", "team_id", "name", "expected_beforeimage"})
_REDIRECT_KEYS = frozenset({
    "entry_id", "retired_team_id", "canonical_team_id", "reason",
    "expected_beforeimage",
})
_ALIAS_KEYS = frozenset({
    "entry_id", "team_name", "team_id", "reason", "expected_beforeimage",
})
_BINDING_KEYS = frozenset({"name", "team_id", "country"})
_ALLOWLIST_KEYS = frozenset({"match_id", "allowed_fields", "reason"})
_IDENTITY_ALLOWLIST_KEYS = frozenset({"match_id", "allowed_fields", "reason"})
_MIXED_GROUP_KEYS = frozenset({
    "entry_id", "retired_team_id", "redirect_team_id", "expected_source_rows",
    "destinations", "reason",
})
_MIXED_DEST_KEYS = frozenset({"team_id", "match_ids", "reason"})
_MATCH_KEYS = frozenset({
    "entry_id", "match_id", "expected_beforeimage", "expected_afterimage",
    "fields", "evidence", "participant_bindings", "reason",
})
_REWIRE_KEYS = frozenset({
    "entry_id", "table", "column", "old_team_id", "new_team_id", "reason",
    "expected_count",
})
_REVIEW_KEYS = frozenset({
    "version", "scope", "profile", "manifest_sha256", "authorization",
    "technical_review",
})
_TECH_REVIEW_KEYS = frozenset({"reviewer", "reviewed_at", "notes"})
_MAIN_PROOF_KEYS = frozenset({
    "kind", "url", "sha256", "event_id", "season_year", "competition_id",
    "leg",
})
_QUAL_PROOF_KEYS = frozenset({
    "kind", "url", "sha256", "event_id", "season_year", "competition_id",
    "round", "leg",
})
_RAW_PROOF_KEYS = frozenset({
    "kind", "source_url", "path", "sha256", "line", "line_text", "section",
    "date", "home_name", "home_country", "away_name", "away_country",
    "home_score", "away_score", "shootout_home", "shootout_away",
})
_ESPN_TEAM_PROOF_KEYS = frozenset({
    "kind", "url", "sha256", "event_id", "season_year", "competition_id",
    "external_id", "identity_name", "country",
})
_MEMBER_PROOF_KEYS = frozenset({"member_team_id", "proof"})
_PARTICIPANT_BINDING_KEYS = frozenset({
    "side", "external_id", "display_name", "country", "source_team_id",
    "team_id", "raw_name", "raw_country",
})
_DISCREPANCY_KEYS = frozenset({"match_id", "fields", "reason"})

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATE_HEADER = re.compile(r"^\s*(?:[A-Za-z]{3}\s+)?([A-Za-z]{3})\s+(\d{1,2})\s*$")
_SECTION_HEADER = re.compile(r"^\s*[▪•]\s*(.+?)\s*$")
_RAW_MATCH = re.compile(
    r"^\s*(?:\d{1,2}:\d{2}\s+)?(.+?)\s+v\s+(.+?)\s+(\d+)\s*-\s*(\d+)(.*)$"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_RAW_SOURCE_URL = re.compile(
    r"^https://raw\.githubusercontent\.com/openfootball/champions-league/"
    r"([0-9a-f]{40})/(\d{4}-\d{2})/(el|conf|cl|elq|confq)\.txt$"
)
_FILE_COMPETITIONS = {"el": "EL", "conf": "UCL", "cl": "CL",
                      "elq": "EL", "confq": "UCL"}
_QUALIFIER_FILE_STEMS = frozenset({"elq", "confq"})
_GROUP_HEADER = re.compile(r"^(?:group|gruppe)\s+[A-Za-z0-9]+$", re.IGNORECASE)
_QUALIFIER_ROUND_SECTION = re.compile(r"^\d+\.\s*Round$", re.IGNORECASE)


# Reuse the full-tool refusal classes at the module boundary so every reused
# helper's fail-closed error is caught by this CLI too. The profiles and
# acceptance rules remain separate; only the exception vocabulary is shared.
CorrectionRehearsalError = _rec.ReconcileError
RefusedError = _rec.RefusedError


# Reused, tested primitives from the full-takeover tool. Reusing these does not
# weaken its validator: the full tool's manifest/profile/acceptance functions
# are never called from here.
_file_sha256 = _rec._file_sha256
_canonical_json = _rec._canonical_json
_normalize_value = _rec._normalize_value
_assert_int32 = _rec._assert_int32
_source_safety = _rec._source_safety
_new_output_safety = _rec._new_output_safety
_reject_private_artifact = _rec._reject_private_artifact
_reserve_new_file = _rec._reserve_new_file
_atomic_write_exclusive = _rec._atomic_write_exclusive
_BeforeImages = _rec._BeforeImages
_table_columns = _rec._table_columns
_table_exists = _rec._table_exists
_logical_hashes = _rec._logical_hashes
_FINGERPRINT_TABLES = _rec._FINGERPRINT_TABLES
_row_dict = _rec._row_dict
_assert_beforeimage = _rec._assert_beforeimage
_read_capture_index = _rec._read_capture_index
_read_capture_body = _rec._read_capture_body
_reject_production = _rec._reject_production
_reject_alias = _rec._reject_alias
_looks_placeholder = _rec._looks_placeholder
_TEAM_REFERENCE_COLUMNS = _rec._TEAM_REFERENCE_COLUMNS


# ---------------------------------------------------------------------------
# strict helpers
# ---------------------------------------------------------------------------

def _reject_unknown_keys(obj: Dict[str, Any], allowed: Iterable[str], what: str) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise RefusedError(f"{what} has unknown keys {unknown}")


def _require_keys(obj: Dict[str, Any], required: Iterable[str], what: str) -> None:
    missing = sorted(set(required) - set(obj))
    if missing:
        raise RefusedError(f"{what} is missing keys {missing}")


def _require_int(obj: Dict[str, Any], key: str, what: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RefusedError(f"{what}.{key} must be an integer")
    return value


def _require_str(obj: Dict[str, Any], key: str, what: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip() or _looks_placeholder(value):
        raise RefusedError(f"{what}.{key} must be a non-placeholder string")
    return value


def _require_list(obj: Dict[str, Any], key: str, what: str) -> list:
    value = obj.get(key)
    if not isinstance(value, list):
        raise RefusedError(f"{what}.{key} must be a list")
    return value


def _require_object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RefusedError(f"{what} must be an object")
    return value


def _private_parent(path: Path, label: str) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise RefusedError(f"{label} parent directory {parent!r} does not exist")
    mode = parent.stat().st_mode & 0o777
    if mode & 0o077:
        raise RefusedError(
            f"{label} parent {parent!r} must be private (0700 or stricter), "
            f"found {oct(mode)}"
        )


def _int32(value: Any, what: str) -> int:
    return _assert_int32(value, what)


def _normal_name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _variant_key(value: Any) -> str:
    """Conservative review-name key: case, accents, punctuation, kickoff only."""
    import unicodedata
    text = str(value or "")
    text = re.sub(r"^\s*\d{1,2}:\d{2}\s+", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_STRUCTURAL_PREFIXES = ("1 fsv", "1", "fk", "sk", "fc", "cf", "ac", "as", "ss", "sp", "sc")


def _source_name_equivalent(source_name: Any, raw_name: Any) -> bool:
    left = _variant_key(source_name)
    right = _variant_key(raw_name)
    if left == right:
        return True
    for prefix in _STRUCTURAL_PREFIXES:
        if left == f"{prefix} {right}" or right == f"{prefix} {left}":
            return True
    return False


def _season_year_for_date(value: str) -> int:
    try:
        parsed = _date.fromisoformat(value)
    except (TypeError, ValueError):
        raise RefusedError(f"invalid ISO date {value!r}")
    return parsed.year if parsed.month >= 8 else parsed.year - 1


def _valid_qualifier_date(value: str, season_year: int) -> bool:
    try:
        parsed = _date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return _date(season_year, 6, 1) <= parsed < _date(season_year, 10, 1)


def _capture_role(url: str) -> str:
    marker = "/soccer/"
    if marker not in url:
        return "unknown"
    tail = url.split(marker, 1)[1].split("/", 1)[0]
    if tail in QUALIFYING_SLUGS_BY_COMPETITION.values():
        return "qualifier"
    for competition in ("EL", "UCL"):
        if tail == espn_slug_for(competition):
            return "main"
    return "unknown"


def review_receipt_path(manifest_path: os.PathLike) -> Path:
    """The correction-specific review receipt; never the generic full one."""
    manifest = Path(str(manifest_path))
    return manifest.parent / (manifest.stem + ".rehearsal-review.json")


def manifest_sha256(manifest: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()


def _verify_review(manifest: Dict[str, Any], manifest_path: os.PathLike) -> Dict[str, Any]:
    path = review_receipt_path(manifest_path)
    if not path.is_file():
        raise RefusedError(
            f"rehearsal review receipt {path!r} is missing; an unreviewed "
            f"correction profile cannot be rehearsed"
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"rehearsal review receipt {path!r} is not valid JSON: {exc}")
    receipt = _require_object(receipt, "rehearsal review receipt")
    _reject_unknown_keys(receipt, _REVIEW_KEYS, "rehearsal review receipt")
    _require_keys(receipt, _REVIEW_KEYS, "rehearsal review receipt")
    if receipt["version"] != PROFILE_VERSION:
        raise RefusedError("rehearsal review receipt version is not supported")
    if receipt["scope"] != REVIEW_SCOPE or receipt["profile"] != PROFILE:
        raise RefusedError(
            "rehearsal review receipt scope/profile is not copy-only-corrections"
        )
    if receipt["manifest_sha256"] != manifest_sha256(manifest):
        raise RefusedError(
            "rehearsal review receipt does not pin this manifest; re-review required"
        )
    _require_str(receipt, "authorization", "rehearsal review receipt")
    review = _require_object(receipt["technical_review"], "technical_review")
    _reject_unknown_keys(review, _TECH_REVIEW_KEYS, "technical_review")
    _require_keys(review, _TECH_REVIEW_KEYS, "technical_review")
    for key in ("reviewer", "reviewed_at", "notes"):
        _require_str(review, key, "technical_review")
    return receipt


def _inspect_source(source_db: os.PathLike) -> Dict[str, Any]:
    source = _source_safety(source_db)
    source_sha = _file_sha256(source)
    conn = duckdb.connect(str(source), read_only=True)
    try:
        tables = sorted(row[0] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall())
        schema = {"tables": tables,
                  "columns": {table: _table_columns(conn, table) for table in tables}}
        counts = {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                  for table in tables}
        keyed = _rec._snapshot_keyed(conn)
        refs = _rec._reference_columns(conn)
    finally:
        conn.close()
    default: Optional[str] = None
    if not keyed:
        provenance = _rec._read_provenance(source, source_sha, None)
        if provenance is None:
            raise RefusedError(
                "source needs a recorded competition default but has no pinned "
                "source.provenance.json"
            )
        default = normalize_competition(provenance["configured_competition"])
    workdir = Path(tempfile.mkdtemp(prefix="espn-correction-inspect-"))
    os.chmod(workdir, 0o700)
    scratch = workdir / "migrated.db"
    try:
        shutil.copy2(str(source), str(scratch))
        os.chmod(scratch, 0o600)
        previous = os.environ.get("COMPETITION_ID")
        if default is not None:
            os.environ["COMPETITION_ID"] = default
        try:
            db = Database(str(scratch))
        finally:
            if previous is None:
                os.environ.pop("COMPETITION_ID", None)
            else:
                os.environ["COMPETITION_ID"] = previous
        try:
            hashes = _logical_hashes(db.conn, _FINGERPRINT_TABLES)
            if default is not None:
                _rec._assert_legacy_attribution(db.conn, default)
        finally:
            db.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if _file_sha256(source) != source_sha:
        raise RefusedError("source changed while it was inspected")
    return {
        "tool_revision": TOOL_REVISION,
        "profile": PROFILE,
        "source": str(source),
        "source_sha256": source_sha,
        "migration_required": not keyed,
        "legacy_competition_default": default,
        "source_logical_hashes": hashes,
        "schema": schema,
        "counts": counts,
        "reference_columns": refs,
    }


def _resolve_entries(manifest: Dict[str, Any], captures_dir: Path) -> List[Dict[str, Any]]:
    index = _read_capture_index(captures_dir)
    declared: Dict[Tuple[str, int, str], str] = {}
    for entry in manifest["captures"]:
        entry = _require_object(entry, "capture declaration")
        _reject_unknown_keys(entry, _CAPTURE_KEYS, "capture declaration")
        _require_keys(entry, _CAPTURE_KEYS, "capture declaration")
        competition = normalize_competition(entry["competition"])
        if competition not in SOURCE_COMPETITIONS or competition == "CL":
            raise RefusedError(f"capture competition {competition!r} is not EL/UCL")
        season_year = _require_int(entry, "season_year", "capture declaration")
        if not isinstance(entry["url"], str) or not entry["url"]:
            raise RefusedError("capture declaration url must be a non-empty string")
        if not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64:
            raise RefusedError("capture declaration sha256 must be a 64-char digest")
        key = (competition, season_year, entry["url"])
        if key in declared:
            raise RefusedError(f"duplicate capture declaration {key!r}")
        declared[key] = entry["sha256"]
    resolved: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in index:
        key = (normalize_competition(entry["competition"]),
               int(entry["season_year"]), str(entry["url"]))
        if key not in declared:
            continue
        if declared[key] != entry["sha256"]:
            raise RefusedError(f"capture {key!r} body hash does not match the declaration")
        if key in seen:
            continue
        seen.add(key)
        resolved.append(dict(entry))
    missing = sorted(set(declared) - seen)
    if missing:
        raise RefusedError(f"declared captures are missing from the index: {missing}")
    return resolved


def _parse_qualifier_entry(entry: Dict[str, Any],
                           captures_dir: Path) -> Dict[str, Dict[str, Any]]:
    competition = normalize_competition(entry["competition"])
    season_year = int(entry["season_year"])
    expected_slug = QUALIFYING_SLUGS_BY_COMPETITION.get(competition)
    if expected_slug is None:
        raise RefusedError(f"no qualifying slug is defined for {competition!r}")
    url = str(entry["url"])
    raw = _read_capture_body(captures_dir, entry)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RefusedError(f"pinned qualifying body {url!r} is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise RefusedError(f"pinned qualifying body {url!r} is not an object")
    try:
        _rec._PARSER._verify_scoreboard_league(expected_slug, payload)
    except Exception as exc:  # noqa: BLE001
        raise RefusedError(
            f"pinned body {url!r} is not an authoritative {expected_slug!r} "
            f"qualifying scoreboard: {exc}"
        )
    events = payload.get("events")
    if not isinstance(events, list):
        raise RefusedError(f"pinned qualifying body {url!r} has no events list")
    parsed: Dict[str, Dict[str, Any]] = {}
    for event in events:
        season = (event.get("season") or {})
        if season.get("year") != season_year:
            continue
        slug = season.get("slug")
        if slug not in _QUALIFYING_SLUGS and slug not in QUALIFYING_ROUND_NAMES:
            raise RefusedError(
                f"qualifying body {url!r} carries non-qualifying stage {slug!r}; "
                f"refusing role confusion"
            )
        detail = _rec._qualifying_detail(event, competition, season_year, url,
                                         entry["sha256"])
        if detail is None or detail["status"] != "FINISHED":
            continue
        if not _valid_qualifier_date(str(detail["date"] or "")[:10], season_year):
            raise RefusedError(
                f"qualifying event {detail['event_id']!r} has a date outside "
                f"the declared qualification window"
            )
        detail["round"] = QUALIFYING_ROUND_NAMES[slug]
        parsed[detail["event_id"]] = detail
    return parsed


def _parse_captures(entries: Sequence[Dict[str, Any]],
                    captures_dir: Path) -> Tuple[Dict[str, Dict[str, Any]],
                                                  Dict[str, Dict[str, Any]]]:
    main: Dict[str, Dict[str, Any]] = {}
    qualifying: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        role = _capture_role(str(entry["url"]))
        if role == "qualifier":
            for event_id, detail in _parse_qualifier_entry(entry, captures_dir).items():
                if event_id in qualifying:
                    raise RefusedError(f"duplicate qualifying event id {event_id!r}")
                qualifying[event_id] = detail
        elif role == "main":
            try:
                entry_main, _ = _rec._events_for_entry(entry, captures_dir)
            except _rec.ReconcileError as exc:
                raise RefusedError(f"pinned main capture rejected: {exc}")
            for event_id, detail in entry_main.items():
                if not detail.get("stage") or detail["stage"] == "QUALIFYING":
                    raise RefusedError(
                        f"main capture carries a non-main event {event_id!r}"
                    )
                if event_id in main:
                    raise RefusedError(f"duplicate main event id {event_id!r}")
                main[event_id] = detail
        else:
            raise RefusedError(f"capture {entry['url']!r} has an unknown role")
    overlap = set(main) & set(qualifying)
    if overlap:
        raise RefusedError(
            f"event id(s) appear in both main and qualifying roles: {sorted(overlap)[:5]}"
        )
    return main, qualifying


def _resolve_bundle_file(base: Path, value: Any, what: str) -> Path:
    """Resolve a bundle-relative pinned file, refusing every path escape."""
    if not isinstance(value, str) or not value:
        raise RefusedError(f"{what} path must be a non-empty string")
    if os.path.isabs(value):
        raise RefusedError(f"{what} path must be bundle-relative, not absolute")
    norm = os.path.normpath(value)
    if norm == ".." or norm.startswith(".." + os.sep):
        raise RefusedError(f"{what} path escapes the bundle directory")
    base_real = os.path.realpath(str(base))
    target = Path(base_real) / norm
    _reject_production(target, what)
    _reject_alias(target, what)
    if not target.is_file():
        raise RefusedError(f"{what} {value!r} is not a regular file")
    if os.stat(str(target)).st_nlink > 1:
        raise RefusedError(f"{what} {value!r} has multiple hard links")
    return target


def _fixed_source_identity(url: Any, what: str) -> Tuple[str, int, str]:
    if not isinstance(url, str):
        raise RefusedError(f"{what} source_url must be a string")
    match = _RAW_SOURCE_URL.match(url)
    if not match:
        raise RefusedError(
            f"{what} source_url must be a fixed-commit openfootball URL, not a "
            f"free-form or branch URL"
        )
    commit, season, file_stem = match.groups()
    start_year = int(season[:4])
    return commit, start_year, _FILE_COMPETITIONS[file_stem], file_stem


def _raw_line_semantics(line: str) -> Optional[tuple]:
    match = _RAW_MATCH.match(line)
    if not match:
        return None
    home_raw, away_raw, _h, _a, _tail = match.groups()
    home_match = re.match(r"^(.*?)\s*\(([A-Z]{3})\)\s*$", home_raw.strip())
    away_match = re.match(r"^(.*?)\s*\(([A-Z]{3})\)\s*$", away_raw.strip())
    if home_match is None or away_match is None:
        return None
    scores = list(re.finditer(r"(\d+)\s*-\s*(\d+)", line))
    if not scores:
        return None
    from backend.football_txt_parser import FootballTxtParser
    chosen = FootballTxtParser()._choose_score(scores, line)
    pen = re.search(r"(\d+)\s*-\s*(\d+)\s+pen", line, re.IGNORECASE)
    shootout = (None, None) if pen is None else (int(pen.group(1)), int(pen.group(2)))
    return (
        _variant_key(home_match.group(1)), home_match.group(2),
        _variant_key(away_match.group(1)), away_match.group(2),
        int(chosen.group(1)), int(chosen.group(2)), shootout,
    )


def _parse_raw_fixture(proof: Dict[str, Any], base: Path,
                       aliases: Dict[Tuple[str, str], int],
                       *, allow_qualifier_stem: bool = False,
                       require_all_bindings: bool = True,
                       require_semantic_uniqueness: bool = False) -> Dict[str, Any]:
    what = "raw-text proof"
    commit, start_year, file_competition, file_stem = _fixed_source_identity(
        proof["source_url"], what
    )
    if file_stem in _QUALIFIER_FILE_STEMS and not allow_qualifier_stem:
        raise RefusedError("qualifier raw file stems are allowed for identity proof only")
    path = _resolve_bundle_file(base, proof["path"], what)
    if path.name != f"{start_year}-{start_year + 1 - 2000:02d}__{file_stem}.txt":
        raise RefusedError(f"{what}: local file identity does not match source_url")
    if _file_sha256(path) != proof["sha256"]:
        raise RefusedError(f"{what}: raw file hash does not match the pinned digest")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RefusedError(f"{what}: cannot read raw file: {exc}")
    line_no = _require_int(proof, "line", what)
    if line_no < 1 or line_no > len(lines):
        raise RefusedError(f"{what}: line {line_no} is outside the pinned file")
    line = lines[line_no - 1]
    if line != proof["line_text"]:
        raise RefusedError(f"{what}: exact pinned line text does not match")
    if lines.count(line) != 1:
        raise RefusedError(f"{what}: quoted line is not unique in the pinned file")

    month = day = None
    for index in range(line_no - 2, -1, -1):
        match = _DATE_HEADER.match(lines[index])
        if match:
            month = _MONTHS.get(match.group(1).lower()[:3])
            day = int(match.group(2))
            break
    if month is None or day is None:
        raise RefusedError(f"{what}: no explicit date header precedes the pinned line")
    if file_stem in _QUALIFIER_FILE_STEMS:
        calendar_year = start_year
    else:
        calendar_year = start_year if month >= 8 else start_year + 1
    try:
        parsed_date = _date(calendar_year, month, day)
    except ValueError:
        raise RefusedError(f"{what}: date header is not a real calendar day")
    if file_stem in _QUALIFIER_FILE_STEMS:
        in_season = _date(start_year, 6, 1) <= parsed_date < _date(start_year, 10, 1)
    else:
        in_season = _date(start_year, 8, 1) <= parsed_date < _date(start_year + 1, 8, 1)
    if not in_season:
        raise RefusedError(f"{what}: date header falls outside the file season")
    if proof["date"] != parsed_date.isoformat():
        raise RefusedError(f"{what}: date header does not match the declared date")

    section = None
    for index in range(line_no - 2, -1, -1):
        match = _SECTION_HEADER.match(lines[index])
        if match:
            section = match.group(1).strip()
            break
    if section is None or proof["section"] != section:
        raise RefusedError(f"{what}: section does not match the pinned heading")
    if _GROUP_HEADER.match(section):
        section_kind = "group"
        round_name = None
    elif _QUALIFIER_ROUND_SECTION.match(section):
        if file_stem not in _QUALIFIER_FILE_STEMS or not allow_qualifier_stem:
            raise RefusedError(
                "qualifier round headings are allowed only for identity/country proof"
            )
        section_kind = "qualifier_round"
        round_name = None
    else:
        section_kind = "knockout"
        round_name = _raw_section_round(section, what)

    match = _RAW_MATCH.match(line)
    if not match:
        raise RefusedError(f"{what}: pinned line is not a parsable match line")
    home_raw, away_raw, _h, _a, _tail = match.groups()
    home_match = re.match(r"^(.*?)\s*\(([A-Z]{3})\)\s*$", home_raw.strip())
    away_match = re.match(r"^(.*?)\s*\(([A-Z]{3})\)\s*$", away_raw.strip())
    if home_match is None or away_match is None:
        raise RefusedError(f"{what}: participant country codes are missing")
    home_name, home_country = home_match.group(1).strip(), home_match.group(2)
    away_name, away_country = away_match.group(1).strip(), away_match.group(2)
    if (home_name, home_country) != (proof["home_name"], proof["home_country"]):
        raise RefusedError(f"{what}: home name/country do not match the pinned line")
    if (away_name, away_country) != (proof["away_name"], proof["away_country"]):
        raise RefusedError(f"{what}: away name/country do not match the pinned line")
    scores = list(re.finditer(r"(\d+)\s*-\s*(\d+)", line))
    if not scores:
        raise RefusedError(f"{what}: pinned line has no score")
    from backend.football_txt_parser import FootballTxtParser
    chosen = FootballTxtParser()._choose_score(scores, line)
    if (int(chosen.group(1)), int(chosen.group(2))) != (
        proof["home_score"], proof["away_score"]
    ):
        raise RefusedError(f"{what}: primary score does not match the pinned line")
    pen = re.search(r"(\d+)\s*-\s*(\d+)\s+pen", line, re.IGNORECASE)
    shootout = (None, None) if pen is None else (int(pen.group(1)), int(pen.group(2)))
    if (proof.get("shootout_home"), proof.get("shootout_away")) != (None, None) and (
        proof.get("shootout_home"), proof.get("shootout_away")
    ) != shootout:
        raise RefusedError(f"{what}: shootout score does not match the pinned line")
    if require_semantic_uniqueness:
        target = _raw_line_semantics(line)
        duplicates = [item for item in lines if _raw_line_semantics(item) == target]
        if len(duplicates) != 1:
            raise RefusedError(
                f"{what}: oriented pair/score is not semantically unique in the file"
            )
    home_binding = aliases.get((_normal_name(home_name), home_country))
    away_binding = aliases.get((_normal_name(away_name), away_country))
    if require_all_bindings and (home_binding is None or away_binding is None):
        raise RefusedError(f"{what}: participant has no reviewed name+country binding")
    return {
        "commit": commit,
        "file_season_year": start_year,
        "file_competition": file_competition,
        "date": parsed_date.isoformat(),
        "section": section,
        "section_kind": section_kind,
        "round": round_name,
        "home_name": home_name,
        "home_country": home_country,
        "away_name": away_name,
        "away_country": away_country,
        "home_team_id": home_binding,
        "away_team_id": away_binding,
        "home_score": proof["home_score"],
        "away_score": proof["away_score"],
        "shootout": shootout,
    }


def _raw_section_round(section: str, what: str) -> str:
    key = _normal_name(section)
    if key not in _RAW_ROUND_NAMES:
        raise RefusedError(f"{what}: unsupported raw section {section!r}")
    return _RAW_ROUND_NAMES[key]


def _stem_for(competition: str) -> str:
    for stem, code in _FILE_COMPETITIONS.items():
        if code == competition:
            return stem
    raise RefusedError(f"no raw file stem for competition {competition!r}")


def _parse_espn_team_proof(proof: Dict[str, Any], entries_by_url: Dict[str, Dict[str, Any]],
                           captures_dir: Path, side: Optional[str] = None) -> Dict[str, Any]:
    url = proof["url"]
    entry = entries_by_url.get(url)
    if entry is None or entry["sha256"] != proof["sha256"]:
        raise RefusedError("ESPN team proof does not pin a declared capture body")
    competition = normalize_competition(proof["competition_id"])
    role = _capture_role(url)
    if role == "qualifier":
        expected_slug = QUALIFYING_SLUGS_BY_COMPETITION.get(competition)
    elif role == "main":
        expected_slug = espn_slug_for(competition)
    else:
        expected_slug = None
    if expected_slug is None:
        raise RefusedError("ESPN team proof competition/role is inconsistent")
    raw = _read_capture_body(captures_dir, entry)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RefusedError(f"ESPN team proof body is invalid JSON: {exc}")
    try:
        _rec._PARSER._verify_scoreboard_league(expected_slug, payload)
    except Exception as exc:  # noqa: BLE001
        raise RefusedError(f"ESPN team proof has the wrong league identity: {exc}")
    event_id = str(proof["event_id"])
    for event in payload.get("events") or []:
        if str(event.get("id")) != event_id:
            continue
        if (event.get("season") or {}).get("year") != proof["season_year"]:
            raise RefusedError("ESPN team proof event season does not match")
        for competition_block in event.get("competitions") or []:
            for competitor in competition_block.get("competitors") or []:
                if side is not None and competitor.get("homeAway") != side:
                    continue
                team = competitor.get("team") or {}
                if str(team.get("id")) != str(proof["external_id"]):
                    continue
                country = team.get("country") or team.get("countryCode")
                return {
                    "identity_name": str(team.get("displayName") or team.get("name") or ""),
                    "country": country,
                    "event_id": event_id,
                    "side": competitor.get("homeAway"),
                    "external_id": str(team.get("id")),
                }
    raise RefusedError("ESPN team proof event/participant was not found")

# ---------------------------------------------------------------------------
# profile validation
# ---------------------------------------------------------------------------

def _validate_manifest_header(manifest: Dict[str, Any], inventory: Dict[str, Any],
                              source_path: Path) -> None:
    _reject_unknown_keys(manifest, _MANIFEST_KEYS, "correction manifest")
    _require_keys(manifest, _MANIFEST_KEYS, "correction manifest")
    if manifest["version"] != PROFILE_VERSION:
        raise RefusedError("correction manifest version is not supported")
    if manifest["profile"] != PROFILE:
        raise RefusedError(f"correction manifest profile must be {PROFILE!r}")
    if manifest["source_sha256"] != inventory["source_sha256"]:
        raise RefusedError("correction manifest source_sha256 does not match the source")
    if not isinstance(manifest["source_path"], str):
        raise RefusedError("correction manifest source_path must be a string")
    if os.path.realpath(manifest["source_path"]) != os.path.realpath(str(source_path)):
        raise RefusedError("correction manifest source_path does not match --source")
    if manifest["legacy_competition_default"] != inventory["legacy_competition_default"]:
        raise RefusedError("correction manifest legacy competition default is stale")
    if manifest["source_logical_hashes"] != inventory["source_logical_hashes"]:
        raise RefusedError(
            "correction manifest source_logical_hashes do not match the migrated source"
        )
    if manifest["capture_index"] != CAPTURE_INDEX_NAME:
        raise RefusedError(f"capture_index must be {CAPTURE_INDEX_NAME!r}")
    if not isinstance(manifest["captures"], list) or not manifest["captures"]:
        raise RefusedError("correction manifest must declare pinned captures")
    audit_year = _require_int(manifest, "audit_season_year", "correction manifest")
    bounds = _require_object(manifest["audit_season_bounds"], "audit_season_bounds")
    _reject_unknown_keys(bounds, _AUDIT_BOUNDS_KEYS, "audit_season_bounds")
    _require_keys(bounds, _AUDIT_BOUNDS_KEYS, "audit_season_bounds")
    expected_start = f"{audit_year:04d}-08-01"
    expected_end = f"{audit_year + 1:04d}-08-01"
    if bounds != {"start": expected_start, "end": expected_end}:
        raise RefusedError(
            f"audit_season_bounds must be pinned to {expected_start}..{expected_end}"
        )

    expected = _require_object(manifest["expected"], "expected")
    _reject_unknown_keys(expected, _EXPECTED_KEYS, "expected")
    _require_keys(expected, _EXPECTED_KEYS, "expected")
    for key in _EXPECTED_KEYS:
        value = _require_int(expected, key, "expected")
        if value < 0:
            raise RefusedError(f"expected.{key} must be non-negative")
    if expected["match_rows"] != inventory["counts"].get("matches"):
        raise RefusedError("expected.match_rows does not match the source")
    if expected["source_team_rows"] != inventory["counts"].get("teams"):
        raise RefusedError("expected.source_team_rows does not match the source")

    for ref in inventory.get("reference_columns") or []:
        if ref["column"] not in _TEAM_REFERENCE_COLUMNS.get(ref["table"], frozenset()):
            raise RefusedError(
                f"unexpected team reference {ref['table']}.{ref['column']} blocks "
                f"the correction rehearsal"
            )
    pending = _require_object(manifest["pending"], "pending")
    _reject_unknown_keys(pending, _PENDING_KEYS, "pending")
    _require_keys(pending, _PENDING_KEYS, "pending")
    _require_int(pending, "main_stage_events", "pending")
    _require_int(pending, "full_takeover_clubs_unapproved", "pending")
    _require_str(pending, "note", "pending")


def _validate_team_ops(manifest: Dict[str, Any], source_teams: set,
                       source_team_names: Dict[int, str]
                       ) -> Tuple[set, Dict[int, int], Dict[Tuple[str, str], int]]:
    created: set = set()
    for op in manifest["team_creations"]:
        op = _require_object(op, "team creation")
        _reject_unknown_keys(op, _CREATION_KEYS, "team creation")
        _require_keys(op, _CREATION_KEYS, "team creation")
        team_id = _int32(op["team_id"], "team creation id")
        if team_id in source_teams or team_id in created:
            raise RefusedError(f"new team id {team_id} collides with an existing team")
        _require_str(op, "entry_id", "team creation")
        _require_str(op, "name", "team creation")
        _require_str(op, "identity_name", "team creation")
        _country(op["country"], "team creation country")
        for existing_id, existing_name in source_team_names.items():
            if (_variant_key(existing_name) == _variant_key(op["name"])
                    or _variant_key(existing_name) == _variant_key(op["identity_name"])
                    or _source_name_equivalent(existing_name, op["identity_name"])):
                raise RefusedError(
                    f"new team name {op['name']!r} ambiguously matches existing "
                    f"source club {existing_id} ({existing_name!r}); refusing blind "
                    f"duplicate creation"
                )
        _require_str(op, "external_id", "team creation external id")
        _require_str(op, "reason", "team creation")
        if not isinstance(op["proof"], list) or not op["proof"]:
            raise RefusedError("team creation needs pinned identity proof")
        created.add(team_id)
    if len(created) != manifest["expected"]["created_team_rows"]:
        raise RefusedError("created_team_rows does not match team_creations")

    all_teams = set(source_teams) | created
    for op in manifest["team_renames"]:
        op = _require_object(op, "team rename")
        _reject_unknown_keys(op, _RENAME_KEYS, "team rename")
        _require_keys(op, _RENAME_KEYS, "team rename")
        team_id = _int32(op["team_id"], "team rename id")
        if team_id not in all_teams:
            raise RefusedError(f"team rename target {team_id} does not exist")
        _require_str(op, "name", "team rename")
        if not isinstance(op["expected_beforeimage"], dict):
            raise RefusedError("team rename needs a full expected_beforeimage")

    redirect_map: Dict[int, int] = {}
    for op in manifest["team_redirects"]:
        op = _require_object(op, "team redirect")
        _reject_unknown_keys(op, _REDIRECT_KEYS, "team redirect")
        _require_keys(op, _REDIRECT_KEYS, "team redirect")
        retired = _int32(op["retired_team_id"], "redirect retired id")
        canonical = _int32(op["canonical_team_id"], "redirect canonical id")
        if retired not in source_teams or canonical not in all_teams:
            raise RefusedError("team redirect endpoints must exist")
        if retired == canonical:
            raise RefusedError("a team redirect cannot point at itself")
        if retired in redirect_map:
            raise RefusedError(f"duplicate redirect for retired team {retired}")
        redirect_map[retired] = canonical
        _require_str(op, "reason", "team redirect")
        if not isinstance(op["expected_beforeimage"], (dict, type(None))):
            raise RefusedError("redirect expected_beforeimage must be an object or null")
    for retired, canonical in redirect_map.items():
        if canonical in redirect_map:
            raise RefusedError(
                f"redirect {retired}->{canonical} is a chain; flat roots are required"
            )
    if len(redirect_map) != manifest["expected"]["redirects"]:
        raise RefusedError("expected.redirects does not match team_redirects")

    aliases: Dict[Tuple[str, str], int] = {}
    for op in manifest["reviewed_alias_bindings"]:
        op = _require_object(op, "reviewed alias binding")
        _reject_unknown_keys(op, _BINDING_KEYS, "reviewed alias binding")
        _require_keys(op, _BINDING_KEYS, "reviewed alias binding")
        team_id = _int32(op["team_id"], "alias binding team id")
        if team_id not in all_teams:
            raise RefusedError(f"alias binding target {team_id} does not exist")
        name = _require_str(op, "name", "reviewed alias binding")
        country = _country(op["country"], "alias binding country")
        key = (_normal_name(name), country)
        if key in aliases and aliases[key] != team_id:
            raise RefusedError(f"alias binding {key!r} has conflicting team ids")
        aliases[key] = team_id

    for op in manifest["alias_corrections"]:
        op = _require_object(op, "alias correction")
        _reject_unknown_keys(op, _ALIAS_KEYS, "alias correction")
        _require_keys(op, _ALIAS_KEYS, "alias correction")
        _require_str(op, "team_name", "alias correction")
        team_id = _int32(op["team_id"], "alias correction team id")
        if team_id not in all_teams:
            raise RefusedError(f"alias correction target {team_id} does not exist")
        if not isinstance(op["expected_beforeimage"], (dict, type(None))):
            raise RefusedError("alias expected_beforeimage must be an object or null")
    return created, redirect_map, aliases


def _country(value: Any, what: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Z]{3}$", value):
        raise RefusedError(f"{what} must be a three-letter uppercase country code")
    return value


def _source_team_names(source_path: Path) -> Dict[int, str]:
    conn = duckdb.connect(str(source_path), read_only=True)
    try:
        return {int(row[0]): str(row[1]) for row in conn.execute(
            "SELECT id, name FROM teams"
        ).fetchall()}
    finally:
        conn.close()


def _validate_identity_proofs(proofs: Any, *, names: set, countries: set,
                              team_ids: set, external_id: str,
                              aliases: Dict[Tuple[str, str], int],
                              entries_by_url: Dict[str, Dict[str, Any]],
                              captures_dir: Path, what: str) -> Dict[str, bool]:
    if not isinstance(proofs, list) or not proofs:
        raise RefusedError(f"{what} needs at least one pinned identity proof")
    provider_ok = False
    country_ok = False
    for raw in proofs:
        proof = _require_object(raw, what)
        kind = proof.get("kind")
        if kind == "raw-identity":
            _reject_unknown_keys(proof, _RAW_PROOF_KEYS, what)
            _require_keys(proof, _RAW_PROOF_KEYS, what)
            parsed = _parse_raw_fixture(
                proof, Path(captures_dir), aliases,
                allow_qualifier_stem=True, require_all_bindings=False,
            )
            pairs = [
                (parsed["home_name"], parsed["home_country"], parsed["home_team_id"]),
                (parsed["away_name"], parsed["away_country"], parsed["away_team_id"]),
            ]
            matches = [p for p in pairs
                       if _variant_key(p[0]) in names and p[1] in countries
                       and p[2] in team_ids]
            if not matches:
                raise RefusedError(f"{what}: raw identity line names a different club")
            country_ok = True
        elif kind == "espn-team":
            _reject_unknown_keys(proof, _ESPN_TEAM_PROOF_KEYS, what)
            _require_keys(proof, _ESPN_TEAM_PROOF_KEYS, what)
            if str(proof["external_id"]) != external_id:
                raise RefusedError(f"{what}: ESPN team proof external id differs")
            parsed = _parse_espn_team_proof(proof, entries_by_url, Path(captures_dir))
            if _variant_key(parsed["identity_name"]) not in names:
                raise RefusedError(f"{what}: ESPN team proof names a different club")
            if parsed["country"] is not None:
                if _country(parsed["country"], "ESPN team proof country") not in countries:
                    raise RefusedError(f"{what}: ESPN team proof country differs")
                country_ok = True
            provider_ok = True
        else:
            raise RefusedError(f"{what}: unknown identity proof kind {kind!r}")
    if not provider_ok:
        raise RefusedError(f"{what}: needs a provider-pinned ESPN team proof")
    if not country_ok:
        raise RefusedError(f"{what}: needs a pinned country proof")
    return {"provider": provider_ok, "country": country_ok}


def _validate_roots(manifest: Dict[str, Any], all_teams: set,
                    redirect_map: Dict[int, int],
                    aliases: Dict[Tuple[str, str], int],
                    entries_by_url: Dict[str, Dict[str, Any]],
                    captures_dir: Path,
                    source_team_names: Dict[int, str], created: set
                    ) -> Tuple[Dict[Tuple[str, Any], int],
                               Dict[int, Tuple[str, str]]]:
    resolution: Dict[Tuple[str, Any], int] = {}
    identity_by_team: Dict[int, Tuple[str, str]] = {}
    root_by_external: Dict[str, Dict[str, Any]] = {}
    variant_sets_by_country: Dict[str, List[Tuple[str, set]]] = {}
    for root in manifest["team_roots"]:
        root = _require_object(root, "team root")
        _reject_unknown_keys(root, _ROOT_KEYS, "team root")
        _require_keys(root, _ROOT_KEYS, "team root")
        _require_str(root, "entry_id", "team root")
        external_id = _require_str(root, "external_id", "team root")
        canonical = _int32(root["canonical_team_id"], "team root canonical id")
        identity_name = _require_str(root, "identity_name", "team root")
        country = _country(root["country"], "team root country")
        variants = _require_list(root, "name_variants", "team root")
        variant_keys = {_variant_key(identity_name)}
        for variant in variants:
            if not isinstance(variant, str) or not variant.strip():
                raise RefusedError("team root name variants must be strings")
            variant_keys.add(_variant_key(variant))
        if not variant_keys:
            raise RefusedError("team root needs reviewed name variants")
        if canonical not in all_teams:
            raise RefusedError(f"team root {external_id!r} target {canonical} does not exist")
        if canonical in redirect_map:
            raise RefusedError(f"team root {external_id!r} target is a retired id")
        if not isinstance(root["member_team_ids"], list):
            raise RefusedError("team root member_team_ids must be a list")
        if not isinstance(root["expected_beforeimage"], (dict, type(None))):
            raise RefusedError("team root expected_beforeimage must be an object or null")
        key = ("p", external_id)
        if key in resolution:
            raise RefusedError(f"duplicate provider root {external_id!r}")
        resolution[key] = canonical
        members = [canonical]
        for member in root["member_team_ids"]:
            member_id = _int32(member, "team root member id")
            if member_id not in all_teams:
                raise RefusedError(f"team root member {member_id} does not exist")
            members.append(member_id)
        for member_id in members:
            key = ("t", member_id)
            if key in resolution and resolution[key] != canonical:
                raise RefusedError(f"team {member_id} belongs to two reviewed roots")
            resolution[key] = canonical
            identity_by_team[member_id] = (identity_name, country)
        for member_id in members:
            if member_id in created:
                continue
            source_name = source_team_names.get(member_id)
            if source_name is None:
                raise RefusedError(f"root member {member_id} is not a source team")
            if _variant_key(source_name) not in variant_keys:
                raise RefusedError(
                    f"source team {member_id} name {source_name!r} is not in the "
                    f"reviewed root variants"
                )
        _validate_identity_proofs(
            root["proof"], names=variant_keys, countries={country},
            team_ids={canonical}, external_id=external_id, aliases=aliases,
            entries_by_url=entries_by_url, captures_dir=captures_dir,
            what=f"team root {external_id}",
        )
        root_by_external[external_id] = root
        variant_sets_by_country.setdefault(country, []).append(
            (external_id, variant_keys)
        )
    for creation in manifest["team_creations"]:
        external_id = str(creation["external_id"])
        if external_id not in root_by_external:
            raise RefusedError(f"created team {external_id} has no matching root proof")
        root = root_by_external[external_id]
        if int(root["canonical_team_id"]) != int(creation["team_id"]):
            raise RefusedError("created team root canonical id differs")
        if (_normal_name(root["identity_name"])
                != _normal_name(creation["identity_name"])):
            raise RefusedError("created team identity differs from its root")
        if _country(creation["country"], "team creation country") != root["country"]:
            raise RefusedError("created team country differs from its root")
        _validate_identity_proofs(
            creation["proof"], names={_variant_key(creation["identity_name"])} | {
                _variant_key(v) for v in root["name_variants"]
            },
            countries={creation["country"]}, team_ids={int(creation["team_id"])},
            external_id=external_id, aliases=aliases,
            entries_by_url=entries_by_url, captures_dir=captures_dir,
            what=f"team creation {external_id}",
        )

    for country, entries in variant_sets_by_country.items():
        for index, (left_id, left_set) in enumerate(entries):
            for right_id, right_set in entries[index + 1:]:
                if left_set & right_set:
                    raise RefusedError(
                        f"same-country roots {left_id} and {right_id} share "
                        f"reviewed name variants"
                    )
    mixed_retired = {
        int(group["retired_team_id"]) for group in manifest["mixed_identity_groups"]
    }
    for root in manifest["team_roots"]:
        canonical = int(root["canonical_team_id"])
        required = [int(m) for m in root["member_team_ids"] if int(m) != canonical]
        proved = {}
        for raw in root["member_proofs"]:
            item = _require_object(raw, "member proof")
            _reject_unknown_keys(item, _MEMBER_PROOF_KEYS, "member proof")
            _require_keys(item, _MEMBER_PROOF_KEYS, "member proof")
            member_id = _int32(item["member_team_id"], "member proof team id")
            if member_id in proved:
                raise RefusedError(f"duplicate member proof for {member_id}")
            _validate_identity_proofs(
                item["proof"], names={_variant_key(root["identity_name"])} | {
                    _variant_key(v) for v in root["name_variants"]
                },
                countries={root["country"]}, team_ids={canonical},
                external_id=str(root["external_id"]), aliases=aliases,
                entries_by_url=entries_by_url, captures_dir=captures_dir,
                what=f"team root member {member_id}",
            )
            proved[member_id] = True
        for member_id in required:
            if member_id in mixed_retired:
                continue
            if member_id not in proved:
                raise RefusedError(
                    f"team root member {member_id} has no proof it is the same club"
                )
    for retired, canonical in redirect_map.items():
        if retired not in identity_by_team:
            raise RefusedError(f"redirect retired team {retired} is not a root member")
        if canonical not in identity_by_team:
            raise RefusedError(f"redirect canonical team {canonical} is not a root")
        if retired in {int(op["team_id"]) for op in manifest["team_creations"]}:
            raise RefusedError("a newly created root cannot be redirected")
        if identity_by_team[retired] != identity_by_team[canonical]:
            raise RefusedError(
                f"redirect {retired}->{canonical} crosses different club identities"
            )
    return resolution, identity_by_team


def _validate_participant_bindings(op: Dict[str, Any], fields: Dict[str, Any],
                                   evidence: Sequence[Dict[str, Any]],
                                   entries_by_url: Dict[str, Dict[str, Any]],
                                   captures_dir: Path,
                                   aliases: Dict[Tuple[str, str], int],
                                   allowed_targets: set,
                                   legacy_allowlist: Dict[int, set],
                                   root_info: Dict[int, Tuple[str, set, str]],
                                   source_team_names: Dict[int, str]) -> None:
    bindings = op.get("participant_bindings")
    if not isinstance(bindings, list):
        raise RefusedError("match correction needs participant_bindings")
    espn_proofs = [p for p in evidence
                   if p.get("kind") in ("espn-main", "espn-qualifier")]
    if not espn_proofs:
        if bindings:
            raise RefusedError("raw-only corrections do not accept participant_bindings")
        return
    if len(espn_proofs) != 1:
        raise RefusedError("a corrected row needs exactly one ESPN fixture proof")
    proof = espn_proofs[0]
    by_side: Dict[str, Dict[str, Any]] = {}
    before = op["expected_beforeimage"]
    after = op["expected_afterimage"]
    match_id = int(op["match_id"])
    for raw in bindings:
        binding = _require_object(raw, "participant binding")
        _reject_unknown_keys(binding, _PARTICIPANT_BINDING_KEYS, "participant binding")
        _require_keys(binding, _PARTICIPANT_BINDING_KEYS, "participant binding")
        side = binding["side"]
        if side not in ("home", "away") or side in by_side:
            raise RefusedError("participant bindings need one home and one away side")
        by_side[side] = binding
        column = f"{side}_team_id"
        source_team_id = _int32(binding["source_team_id"], "binding source team id")
        team_id = _int32(binding["team_id"], "binding target team id")
        if source_team_id != before[column]:
            raise RefusedError(
                f"{side} binding source_team_id does not match the row beforeimage"
            )
        if team_id != after[column]:
            raise RefusedError(
                f"{side} binding target does not match the row afterimage"
            )
        external_id = _require_str(binding, "external_id", "participant binding")
        display_name = _require_str(binding, "display_name", "participant binding")
        country = _country(binding["country"], "participant binding country")
        constructed = dict(proof)
        constructed["external_id"] = external_id
        parsed = _parse_espn_team_proof(constructed, entries_by_url,
                                        Path(captures_dir), side=side)
        if parsed["external_id"] != external_id:
            raise RefusedError("participant binding external id differs from the event")
        if _normal_name(parsed["identity_name"]) != _normal_name(display_name):
            raise RefusedError("participant binding display name differs from the event")
        if parsed["country"] is not None and parsed["country"] != country:
            raise RefusedError("participant binding country differs from the event")
        raw_name = _require_str(binding, "raw_name", "participant binding")
        raw_country = _country(binding["raw_country"], "participant binding raw country")
        alias_target = aliases.get((_normal_name(raw_name), raw_country))
        if alias_target != team_id:
            raise RefusedError(
                "participant binding raw alias does not resolve to the row participant"
            )
        if raw_country != country:
            raise RefusedError("participant binding country differs from its raw country")
        if source_team_id != team_id:
            if team_id not in allowed_targets:
                raise RefusedError(
                    f"changed {side} participant does not target a proven root/new team"
                )
            if team_id in root_info:
                external, variants, root_country = root_info[team_id]
                if external_id != external:
                    raise RefusedError(
                        "changed participant external id differs from its target root"
                    )
                if _variant_key(display_name) not in variants:
                    raise RefusedError(
                        "changed participant display name is not in the target root "
                        "variants"
                    )
                if country != root_country:
                    raise RefusedError(
                        "changed participant country differs from its target root"
                    )
        else:
            source_name = source_team_names.get(team_id)
            if source_name is None:
                raise RefusedError("unchanged binding team is not a source team")
            if not _source_name_equivalent(source_name, raw_name):
                raise RefusedError(
                    "unchanged participant raw_name does not match the source team name"
                )
            if team_id in root_info:
                external, variants, root_country = root_info[team_id]
                if external_id != external or _variant_key(display_name) not in variants                         or country != root_country:
                    raise RefusedError(
                        "unchanged participant does not match its declared root"
                    )
    if set(by_side) != {"home", "away"}:
        raise RefusedError("a corrected row needs bindings for both oriented sides")


def _validate_match_evidence(op: Dict[str, Any], fields: Dict[str, Any],
                             evidence: Sequence[Dict[str, Any]],
                             main_by_id: Dict[str, Dict[str, Any]],
                             qual_by_id: Dict[str, Dict[str, Any]],
                             aliases: Dict[Tuple[str, str], int], base: Path,
                             legacy_allowlist: Dict[int, set],
                             identity_allowlist: Dict[int, set],
                             discrepancies: Dict[int, set]) -> None:
    match_id = int(op["match_id"])
    before = op["expected_beforeimage"]
    after = op["expected_afterimage"]
    bound = False
    saw_qualifier = False
    for raw_proof in evidence:
        proof = _require_object(raw_proof, "match evidence")
        if proof.get("kind") not in _PROOF_KINDS:
            raise RefusedError(f"unknown evidence kind {proof.get('kind')!r}")
        kind = proof["kind"]
        if kind == "raw-text":
            _reject_unknown_keys(proof, _RAW_PROOF_KEYS, "raw-text evidence")
            _require_keys(proof, _RAW_PROOF_KEYS, "raw-text evidence")
            legacy = match_id in legacy_allowlist
            identity_only = match_id in identity_allowlist
            if not legacy and not identity_only:
                raise RefusedError(
                    f"match {match_id} has no legacy or identity allowlist entry"
                )
            allowed = legacy_allowlist[match_id] if legacy else identity_allowlist[match_id]
            if not set(fields) <= allowed:
                raise RefusedError(
                    f"match {match_id} raw-proof fields exceed its allowlist"
                )
            if "stage" in fields:
                raise RefusedError("raw text can never prove a stage change")
            parsed = _parse_raw_fixture(
                proof, base, aliases, allow_qualifier_stem=not legacy,
                require_semantic_uniqueness=(identity_only and not legacy),
            )
            if parsed["file_competition"] != after["competition_id"]:
                raise RefusedError("raw proof file competition differs from the row")
            if (parsed["section_kind"] in ("group", "qualifier_round")
                    and not set(fields) <= {"home_team_id", "away_team_id"}):
                raise RefusedError(
                    "a group/qualifier-round heading may prove participant "
                    "identity only"
                )
            if parsed["shootout"] != (None, None) and match_id not in legacy_allowlist:
                raise RefusedError(
                    "a shootout proof needs a declared legacy non-identity exception"
                )
            if (parsed["home_team_id"], parsed["away_team_id"]) != (
                after["home_team_id"], after["away_team_id"]
            ):
                raise RefusedError("raw proof binds a different oriented participant pair")
            if (parsed["home_score"], parsed["away_score"]) != (
                after["home_score"], after["away_score"]
            ):
                raise RefusedError("raw proof binds a different stored/per-leg score")
            if identity_only and not legacy:
                if not set(fields) <= {"home_team_id", "away_team_id"}:
                    raise RefusedError("identity-only proof may change only team slots")
                declared = discrepancies.get(match_id, set())
                if "date" in set(fields) or "round" in set(fields):
                    raise RefusedError(
                        "identity-only proof cannot change date or round"
                    )
                if parsed["date"] != after["date"]:
                    if _season_year_for_date(after["date"]) != parsed["file_season_year"]:
                        raise RefusedError(
                            "identity-only date discrepancy crosses the file season"
                        )
                    if "date" not in declared:
                        raise RefusedError(
                            "identity-only date drift needs an explicit metadata "
                            "discrepancy entry"
                        )
                if (parsed["section_kind"] == "knockout"
                        and after["round"] is not None
                        and after["round"] != parsed["round"]):
                    if "round" not in declared:
                        raise RefusedError(
                            "identity-only round drift needs an explicit metadata "
                            "discrepancy entry"
                        )
            else:
                if parsed["date"] != after["date"]:
                    raise RefusedError("legacy raw proof date differs from the row")
                if (parsed["section_kind"] in ("group", "qualifier_round")
                        and "round" in fields):
                    raise RefusedError(
                        "group/qualifier-round heading cannot prove a round change"
                    )
                if (after["round"] is not None and parsed["round"] is not None
                        and after["round"] != parsed["round"]):
                    raise RefusedError("legacy raw proof round differs from the row")
            bound = True
            continue

        allowed_keys = _MAIN_PROOF_KEYS if kind == "espn-main" else _QUAL_PROOF_KEYS
        _reject_unknown_keys(proof, allowed_keys, f"{kind} evidence")
        _require_keys(proof, allowed_keys, f"{kind} evidence")
        event_id = _require_str(proof, "event_id", f"{kind} evidence")
        detail = (main_by_id if kind == "espn-main" else qual_by_id).get(event_id)
        if detail is None:
            raise RefusedError(f"{kind} evidence event {event_id!r} is not a pinned event")
        if detail["url"] != proof["url"] or detail["sha256"] != proof["sha256"]:
            raise RefusedError(f"{kind} evidence does not pin the parsed body hash")
        competition = normalize_competition(proof["competition_id"])
        if competition != normalize_competition(after["competition_id"]):
            raise RefusedError("match evidence competition differs from the row")
        if competition != normalize_competition(detail["competition"]):
            raise RefusedError("match evidence competition differs from the parsed body")
        season_year = _require_int(proof, "season_year", f"{kind} evidence")
        if season_year != int(detail["season_year"]):
            raise RefusedError("match evidence season does not match the parsed event")
        if proof.get("leg") != detail.get("leg"):
            raise RefusedError("match evidence leg does not match the parsed event")

        if (detail["home_score"], detail["away_score"]) != (
            after["home_score"], after["away_score"]
        ):
            raise RefusedError("evidence binds a different per-leg score")

        if kind == "espn-qualifier":
            saw_qualifier = True
            derived_round = QUALIFYING_ROUND_NAMES.get(str(detail.get("slug")))
            if derived_round is None:
                raise RefusedError("qualifier evidence stage is not recognised")
            if proof["round"] != derived_round:
                raise RefusedError(
                    "qualifier evidence round differs from the parsed event round"
                )
            if after["stage"] != "QUALIFYING" or after["round"] != derived_round:
                raise RefusedError(
                    "qualifier afterimage must be QUALIFYING with the parsed round"
                )
            if not _valid_qualifier_date(str(detail["date"])[:10], season_year):
                raise RefusedError("qualifier evidence date is outside its season")
        else:
            if detail.get("stage") == "QUALIFYING" or not detail.get("stage"):
                raise RefusedError("main evidence is not a main-stage event")
            if season_year != _season_year_for_date(str(after["date"])):
                raise RefusedError(
                    "main evidence season does not match the row's own season"
                )
            if after["stage"] is not None and after["stage"] != detail.get("stage"):
                raise RefusedError("main evidence stage differs from the stored stage")
            if (after["round"] is not None
                    and after["round"] != detail.get("round")):
                raise RefusedError("main evidence round differs from the stored round")
        if str(detail["date"])[:10] != after["date"]:
            raise RefusedError("evidence binds a different fixture date")
        bound = True

    if not bound:
        raise RefusedError(f"match {match_id} has no whole-fixture proof")
    if fields.get("stage") == "QUALIFYING" and not saw_qualifier:
        raise RefusedError(
            f"match {match_id} cannot become QUALIFYING without qualifier evidence"
        )


def _validate_match_corrections(manifest: Dict[str, Any], all_teams: set,
                                aliases: Dict[str, int], base: Path,
                                entries_by_url: Dict[str, Dict[str, Any]],
                                main_by_id: Dict[str, Dict[str, Any]],
                                qual_by_id: Dict[str, Dict[str, Any]],
                                allowed_targets: set,
                                root_info: Dict[int, Tuple[str, set, str]],
                                source_team_names: Dict[int, str],
                                ) -> Tuple[Dict[int, Dict[str, Any]], int,
                                           Dict[int, set], Dict[int, set]]:
    allowlist: Dict[int, set] = {}
    for item in manifest["legacy_exception_allowlist"]:
        item = _require_object(item, "legacy exception allowlist entry")
        _reject_unknown_keys(item, _ALLOWLIST_KEYS, "legacy exception allowlist entry")
        _require_keys(item, _ALLOWLIST_KEYS, "legacy exception allowlist entry")
        match_id = _require_int(item, "match_id", "legacy exception allowlist entry")
        fields = _require_list(item, "allowed_fields", "legacy exception allowlist entry")
        if not fields or not set(fields) <= _WRITABLE_MATCH_FIELDS:
            raise RefusedError(f"legacy allowlist {match_id} has invalid fields")
        if set(fields) <= {"home_team_id", "away_team_id"}:
            raise RefusedError(
                f"legacy non-identity allowlist {match_id} must name an older "
                f"non-identity field exception"
            )
        _require_str(item, "reason", "legacy exception allowlist entry")
        if match_id in allowlist:
            raise RefusedError(f"duplicate legacy allowlist match {match_id}")
        allowlist[match_id] = set(fields)
    identity_allowlist: Dict[int, set] = {}
    for item in manifest["identity_exception_allowlist"]:
        item = _require_object(item, "identity exception allowlist entry")
        _reject_unknown_keys(item, _IDENTITY_ALLOWLIST_KEYS,
                             "identity exception allowlist entry")
        _require_keys(item, _IDENTITY_ALLOWLIST_KEYS,
                      "identity exception allowlist entry")
        match_id = _require_int(item, "match_id", "identity exception allowlist entry")
        fields = _require_list(item, "allowed_fields",
                               "identity exception allowlist entry")
        if not fields or not set(fields) <= {"home_team_id", "away_team_id"}:
            raise RefusedError(
                f"identity allowlist {match_id} may only change participant slots"
            )
        _require_str(item, "reason", "identity exception allowlist entry")
        if match_id in identity_allowlist:
            raise RefusedError(f"duplicate identity allowlist match {match_id}")
        identity_allowlist[match_id] = set(fields)

    discrepancies: Dict[int, set] = {}
    for item in manifest["metadata_discrepancies"]:
        item = _require_object(item, "metadata discrepancy")
        _reject_unknown_keys(item, _DISCREPANCY_KEYS, "metadata discrepancy")
        _require_keys(item, _DISCREPANCY_KEYS, "metadata discrepancy")
        match_id = _require_int(item, "match_id", "metadata discrepancy")
        fields = _require_list(item, "fields", "metadata discrepancy")
        if not fields or not set(fields) <= {"date", "round"}:
            raise RefusedError("metadata discrepancy fields must be date and/or round")
        _require_str(item, "reason", "metadata discrepancy")
        if match_id in discrepancies:
            raise RefusedError(f"duplicate metadata discrepancy for {match_id}")
        discrepancies[match_id] = set(fields)
    merged: Dict[int, Dict[str, Any]] = {}
    qualifier_matches: set = set()
    for op in manifest["match_corrections"]:
        op = _require_object(op, "match correction")
        _reject_unknown_keys(op, _MATCH_KEYS, "match correction")
        _require_keys(op, _MATCH_KEYS, "match correction")
        match_id = _int32(op["match_id"], "match correction id")
        before = _require_object(op["expected_beforeimage"], "expected_beforeimage")
        after = _require_object(op["expected_afterimage"], "expected_afterimage")
        if set(before) != set(_MATCH_COLUMNS) or set(after) != set(_MATCH_COLUMNS):
            raise RefusedError(
                f"match {match_id} before/after images must have all match columns"
            )
        if before["id"] != match_id or after["id"] != match_id:
            raise RefusedError(f"match {match_id} image id does not match")
        fields = _require_object(op["fields"], "match correction fields")
        if not fields or not set(fields) <= _WRITABLE_MATCH_FIELDS:
            raise RefusedError(f"match {match_id} has disallowed or empty fields")
        for field, value in fields.items():
            if field in ("home_team_id", "away_team_id"):
                value = _int32(value, f"match {match_id} {field}")
                if value not in all_teams:
                    raise RefusedError(f"match {match_id} target team {value} is missing")
            if field == "date" and not isinstance(value, str):
                raise RefusedError(f"match {match_id} date must be a string")
        computed = dict(before)
        computed.update(fields)
        if computed != after:
            raise RefusedError(
                f"match {match_id} expected_afterimage is not exactly before+fields"
            )
        if computed == before:
            raise RefusedError(f"match {match_id} correction is a no-op")
        if after["competition_id"] != before["competition_id"]:
            raise RefusedError(f"match {match_id} may not change competition_id")
        for side in ("home", "away"):
            column = f"{side}_team_id"
            if before[column] != after[column]:
                if after[column] not in allowed_targets:
                    raise RefusedError(
                        f"match {match_id} changes {column} without a proven root/new "
                        f"target"
                    )
        _require_str(op, "entry_id", "match correction")
        _require_str(op, "reason", "match correction")
        evidence = _require_list(op, "evidence", "match correction")
        if not evidence:
            raise RefusedError(f"match {match_id} has no evidence")
        _validate_match_evidence(op, fields, evidence, main_by_id, qual_by_id,
                                 aliases, base, allowlist, identity_allowlist,
                                 discrepancies)
        _validate_participant_bindings(
            op, fields, evidence, entries_by_url, base, aliases,
            allowed_targets, allowlist, root_info, source_team_names,
        )
        if any(p.get("kind") == "espn-qualifier" for p in evidence):
            qualifier_matches.add(match_id)

        existing = merged.get(match_id)
        if existing is None:
            merged[match_id] = {
                "before": before, "after": after, "fields": dict(fields),
            }
        else:
            if existing["before"] != before:
                raise RefusedError(f"match {match_id} has conflicting beforeimages")
            for field, value in fields.items():
                if field in existing["fields"] and existing["fields"][field] != value:
                    raise RefusedError(
                        f"match {match_id} has conflicting assignments for {field}"
                    )
                existing["fields"][field] = value
    for match_id, entry in merged.items():
        combined = dict(entry["before"])
        combined.update(entry["fields"])
        if combined != entry["after"]:
            raise RefusedError(
                f"match {match_id} overlapping corrections do not match one final "
                f"consolidated afterimage"
            )
    if len(qualifier_matches) != manifest["expected"]["qualifying_corrections"]:
        raise RefusedError("expected.qualifying_corrections does not match the evidence")
    for match_id, fields in discrepancies.items():
        if match_id not in merged:
            raise RefusedError("metadata discrepancy has no matching correction")
        if match_id not in identity_allowlist:
            raise RefusedError(
                "metadata discrepancies are limited to identity-only corrections"
            )
        if not set(merged[match_id]["fields"]) <= {"home_team_id", "away_team_id"}:
            raise RefusedError(
                "metadata discrepancies cannot accompany non-identity field edits"
            )
        if fields & set(merged[match_id]["fields"]):
            raise RefusedError(
                "metadata discrepancy fields may not be changed by the correction"
            )
    if len(identity_allowlist) != manifest["expected"]["identity_corrections"]:
        raise RefusedError("identity_corrections does not match the identity allowlist")
    if len(allowlist) != manifest["expected"]["legacy_nonidentity_exceptions"]:
        raise RefusedError("legacy_nonidentity_exceptions does not match the allowlist")
    return merged, len(qualifier_matches), allowlist, identity_allowlist


def _validate_rewiring(manifest: Dict[str, Any], redirect_map: Dict[int, int],
                       merged: Dict[int, Dict[str, Any]]) -> None:
    rewires: set = set()
    for op in manifest["reference_rewiring"]:
        op = _require_object(op, "reference rewiring")
        _reject_unknown_keys(op, _REWIRE_KEYS, "reference rewiring")
        _require_keys(op, _REWIRE_KEYS, "reference rewiring")
        table = _require_str(op, "table", "reference rewiring")
        if not _IDENTIFIER.match(table) or table == "team_redirects":
            raise RefusedError("reference rewiring table is not an allowed reference")
        column = _require_str(op, "column", "reference rewiring")
        if column not in _TEAM_REFERENCE_COLUMNS.get(table, frozenset()):
            raise RefusedError(f"reference rewiring {table}.{column} is not allowed")
        old = _int32(op["old_team_id"], "reference rewiring old id")
        new = _int32(op["new_team_id"], "reference rewiring new id")
        if redirect_map.get(old) != new:
            raise RefusedError(
                f"reference rewiring {old}->{new} is not a declared flat redirect"
            )
        expected_count = _require_int(op, "expected_count", "reference rewiring")
        if expected_count < 0:
            raise RefusedError("reference rewiring expected_count must be non-negative")
        key = (table, column, old, new)
        if key in rewires:
            raise RefusedError(f"duplicate reference rewiring {key!r}")
        rewires.add(key)
        _require_str(op, "reason", "reference rewiring")

    for match_id, entry in merged.items():
        for column in ("home_team_id", "away_team_id"):
            value = entry["after"][column]
            if value in redirect_map:
                new = redirect_map[value]
                if ("matches", column, value, new) not in rewires:
                    raise RefusedError(
                        f"match {match_id} still references retired team {value} "
                        f"after its corrections; split the mixed id before the "
                        f"redirect (no declared match reference rewire)"
                    )


def _require_mixed_group_for_cross_alias(manifest: Dict[str, Any],
                                          source_path: Path,
                                          redirect_map: Dict[int, int],
                                          identity_by_team: Dict[int, Tuple[str, str]]) -> None:
    grouped = {int(group["retired_team_id"])
               for group in manifest["mixed_identity_groups"]}
    conn = duckdb.connect(str(source_path), read_only=True)
    try:
        if not _table_exists(conn, "team_mappings"):
            return
        aliases = conn.execute(
            "SELECT team_name, team_id FROM team_mappings"
        ).fetchall()
        match_refs = {}
        for match_id, home, away in conn.execute(
            "SELECT id, home_team_id, away_team_id FROM matches"
        ).fetchall():
            match_refs[int(match_id)] = (int(home), int(away))
    finally:
        conn.close()
    names_by_identity: Dict[str, Tuple[int, Tuple[str, str]]] = {}
    for root in manifest["team_roots"]:
        canonical = int(root["canonical_team_id"])
        identity = (str(root["identity_name"]), str(root["country"]))
        for name in [root["identity_name"]] + list(root["name_variants"]):
            names_by_identity.setdefault(_variant_key(name), (canonical, identity))
    for retired, canonical in redirect_map.items():
        if retired in grouped:
            continue
        if not any(retired in pair for pair in match_refs.values()):
            continue
        for alias_name, team_id in aliases:
            if int(team_id) != retired:
                continue
            stripped = re.sub(r"^\d{1,2}:\d{2}\s*", "", str(alias_name))
            norm = _variant_key(stripped)
            match = names_by_identity.get(norm)
            if match is None:
                raise RefusedError(
                    f"redirect {retired}->{canonical} has a source alias "
                    f"({alias_name!r}) that resolves to no reviewed root variant; "
                    f"an explicit mixed identity group is required"
                )
            if match[1] != identity_by_team[canonical]:
                raise RefusedError(
                    f"redirect {retired}->{canonical} has a source alias naming "
                    f"a different club ({alias_name!r}); an explicit mixed "
                    f"identity group is required (zero-split merge refused)"
                )


def _validate_mixed_groups(manifest: Dict[str, Any], source_path: Path,
                           all_teams: set, redirect_map: Dict[int, int],
                           merged: Dict[int, Dict[str, Any]],
                           identity_allowlist: Dict[int, set],
                           legacy_allowlist: Dict[int, set]) -> Dict[str, int]:
    groups = manifest["mixed_identity_groups"]
    conn = duckdb.connect(str(source_path), read_only=True)
    try:
        source_rows = {}
        for match_id, home, away in conn.execute(
            "SELECT id, home_team_id, away_team_id FROM matches"
        ).fetchall():
            source_rows[int(match_id)] = {"home_team_id": int(home),
                                          "away_team_id": int(away)}
    finally:
        conn.close()
    seen_retired: set = set()
    total = split = remaining = 0
    accounted_corrections: set = set()
    for raw in groups:
        group = _require_object(raw, "mixed identity group")
        _reject_unknown_keys(group, _MIXED_GROUP_KEYS, "mixed identity group")
        _require_keys(group, _MIXED_GROUP_KEYS, "mixed identity group")
        _require_str(group, "entry_id", "mixed identity group")
        retired = _int32(group["retired_team_id"], "mixed group retired id")
        redirect_team = _int32(group["redirect_team_id"], "mixed group redirect id")
        if retired in seen_retired:
            raise RefusedError(f"duplicate mixed group for retired team {retired}")
        seen_retired.add(retired)
        if redirect_map.get(retired) != redirect_team:
            raise RefusedError("mixed group redirect team is not the declared redirect")
        incidents = [match_id for match_id, row in source_rows.items()
                     if retired in (row["home_team_id"], row["away_team_id"])]
        expected_rows = _require_int(group, "expected_source_rows",
                                     "mixed identity group")
        if expected_rows != len(incidents):
            raise RefusedError(
                f"mixed group {retired} declared {expected_rows} source rows but "
                f"the source has {len(incidents)}"
            )
        destinations = _require_list(group, "destinations", "mixed identity group")
        if not isinstance(destinations, list) or len(destinations) < 2:
            raise RefusedError(
                "a mixed group needs at least two destinations; a zero-split "
                "whole-id merge is refused"
            )
        assigned: Dict[int, int] = {}
        destination_ids = []
        for raw_dest in destinations:
            dest = _require_object(raw_dest, "mixed destination")
            _reject_unknown_keys(dest, _MIXED_DEST_KEYS, "mixed destination")
            _require_keys(dest, _MIXED_DEST_KEYS, "mixed destination")
            team_id = _int32(dest["team_id"], "mixed destination team id")
            destination_ids.append(team_id)
            if team_id not in all_teams:
                raise RefusedError(f"mixed destination team {team_id} is missing")
            _require_str(dest, "reason", "mixed destination")
            match_ids = _require_list(dest, "match_ids", "mixed destination")
            if not match_ids:
                raise RefusedError("mixed destination group is empty")
            for match_id in match_ids:
                match_id = _int32(match_id, "mixed destination match id")
                if match_id in assigned:
                    raise RefusedError(f"mixed match {match_id} assigned twice")
                assigned[match_id] = team_id
        if len(set(destination_ids)) != len(destination_ids):
            raise RefusedError("mixed group has duplicate destination team ids")
        if set(assigned) != set(incidents):
            raise RefusedError(
                "mixed group does not exactly partition every source incident row"
            )
        if redirect_team not in destination_ids:
            raise RefusedError("mixed group omits the canonical redirect destination")
        for match_id, destination in assigned.items():
            row = source_rows[match_id]
            after = (merged[match_id]["after"] if match_id in merged else row)
            slots = [column for column in ("home_team_id", "away_team_id")
                     if row[column] == retired]
            if not slots:
                raise RefusedError("mixed group row does not reference the retired id")
            if destination == redirect_team:
                if any(after[column] != retired for column in slots):
                    raise RefusedError(
                        "canonical mixed destination was split instead of rewired"
                    )
                remaining += 1
                continue
            if match_id not in identity_allowlist:
                raise RefusedError(
                    f"mixed split row {match_id} lacks an identity-only allowlist entry"
                )
            split_fields = set(merged.get(match_id, {}).get("fields", {}))
            allowed_split = {"home_team_id", "away_team_id"} | legacy_allowlist.get(
                match_id, set()
            )
            if not split_fields <= allowed_split:
                raise RefusedError(
                    "mixed split may not change fields outside identity/legacy scope"
                )
            if any(after[column] != destination for column in slots):
                raise RefusedError(
                    f"mixed split row {match_id} has a cross-club/incorrect destination"
                )
            if retired in (after["home_team_id"], after["away_team_id"]):
                raise RefusedError("mixed split row still references the retired id")
            split += 1
            accounted_corrections.add(match_id)
        total += len(incidents)
    for match_id, entry in merged.items():
        if any(entry["before"][column] in seen_retired
               for column in ("home_team_id", "away_team_id")):
            if match_id not in accounted_corrections:
                raise RefusedError(
                    f"match {match_id} corrects a mixed-group id outside the group"
                )
    expected = manifest["expected"]
    observed = {"mixed_source_rows": total, "mixed_split_rows": split,
                "mixed_remaining_rows": remaining}
    for key, value in observed.items():
        if expected[key] != value:
            raise RefusedError(f"expected.{key} does not match proved group rows")
    if not groups:
        if any(expected[key] for key in ("mixed_source_rows", "mixed_split_rows",
                                         "mixed_remaining_rows")):
            raise RefusedError("mixed expected counts require a mixed group")
    return observed


def _validate_manifest(manifest: Dict[str, Any], inventory: Dict[str, Any],
                       source_path: Path, captures_dir: Path,
                       entries: Sequence[Dict[str, Any]],
                       main_by_id: Dict[str, Dict[str, Any]],
                       qual_by_id: Dict[str, Dict[str, Any]]
                       ) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    _validate_manifest_header(manifest, inventory, source_path)
    source_teams = _source_team_ids(source_path)
    source_team_names = _source_team_names(source_path)
    created, redirect_map, aliases = _validate_team_ops(
        manifest, source_teams, source_team_names
    )
    all_teams = source_teams | created
    entries_by_url = {str(entry["url"]): entry for entry in entries}
    roots, identity_by_team = _validate_roots(
        manifest, all_teams, redirect_map, aliases, entries_by_url, captures_dir,
        _source_team_names(source_path), created,
    )
    allowed_targets = {int(root["canonical_team_id"])
                       for root in manifest["team_roots"]} | created
    root_info = {}
    for root in manifest["team_roots"]:
        root_info[int(root["canonical_team_id"])] = (
            str(root["external_id"]),
            {_variant_key(root["identity_name"])}
            | {_variant_key(v) for v in root["name_variants"]},
            str(root["country"]),
        )
    merged, qualifier_count, legacy_allow, identity_allow = (
        _validate_match_corrections(
            manifest, all_teams, aliases, captures_dir, entries_by_url,
            main_by_id, qual_by_id, allowed_targets, root_info, source_team_names,
        )
    )
    _validate_rewiring(manifest, redirect_map, merged)
    _require_mixed_group_for_cross_alias(manifest, source_path, redirect_map,
                                         identity_by_team)
    _validate_mixed_groups(manifest, source_path, all_teams, redirect_map,
                           merged, identity_allow, legacy_allow)
    wrong_club_slots = sum(
        1 for op in manifest["match_corrections"]
        if any(p.get("kind") == "espn-qualifier" for p in op["evidence"])
        for field in ("home_team_id", "away_team_id")
        if field in op["fields"]
    )
    if manifest["expected"]["wrong_club_slots"] != wrong_club_slots:
        raise RefusedError("expected.wrong_club_slots does not match the qualifier proofs")
    if manifest["expected"]["team_renames"] != len(manifest["team_renames"]):
        raise RefusedError("expected.team_renames does not match team_renames")
    stats = {
        "team_creations": len(created),
        "team_renames": len(manifest["team_renames"]),
        "redirects": len(redirect_map),
        "alias_corrections": len(manifest["alias_corrections"]),
        "match_corrections": len(merged),
        "qualifying_corrections": qualifier_count,
        "wrong_club_slots": wrong_club_slots,
        "identity_corrections": len(identity_allow),
        "legacy_nonidentity_exceptions": len(legacy_allow),
        "reference_rewiring": len(manifest["reference_rewiring"]),
    }
    return merged, stats, roots


def _source_team_ids(source_path: Path) -> set:
    conn = duckdb.connect(str(source_path), read_only=True)
    try:
        return {int(row[0]) for row in conn.execute("SELECT id FROM teams").fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

def _assert_team_beforeimages(db, manifest: Dict[str, Any]) -> None:
    for op in manifest["team_renames"]:
        actual = _row_dict(db.conn, "teams", "id = ?", (int(op["team_id"]),))
        _assert_beforeimage(actual, op["expected_beforeimage"], "team rename")
    for op in manifest["team_redirects"]:
        actual = _row_dict(db.conn, "team_redirects", "retired_team_id = ?",
                           (int(op["retired_team_id"]),))
        _assert_beforeimage(actual, op["expected_beforeimage"], "team redirect")
        if actual is not None and int(actual["canonical_team_id"]) != int(op["canonical_team_id"]):
            raise RefusedError("existing redirect contradicts the manifest")
    for op in manifest["team_roots"]:
        actual = _row_dict(
            db.conn, "provider_team_mappings",
            "provider = ? AND external_id = ?",
            (PROVIDER_ESPN, str(op["external_id"])),
        )
        _assert_beforeimage(actual, op["expected_beforeimage"], "team root")
        if actual is not None and int(actual["team_id"]) != int(op["canonical_team_id"]):
            raise RefusedError("existing provider mapping contradicts the reviewed root")
    for op in manifest["alias_corrections"]:
        actual = None
        if _table_exists(db.conn, "team_mappings"):
            actual = _row_dict(db.conn, "team_mappings", "team_name = ?",
                               (op["team_name"],))
        _assert_beforeimage(actual, op["expected_beforeimage"], "alias correction")


def _assert_match_beforeimages(db, merged: Dict[int, Dict[str, Any]]) -> None:
    for match_id, entry in merged.items():
        actual = _row_dict(db.conn, "matches", "id = ?", (match_id,))
        _assert_beforeimage(actual, entry["before"], f"match {match_id}")


def _apply_profile(db, manifest: Dict[str, Any], merged: Dict[int, Dict[str, Any]],
                   beforeimages: _BeforeImages) -> Dict[str, int]:
    counts = {
        "team_creations": 0, "team_renames": 0, "redirects": 0,
        "team_roots": 0, "alias_corrections": 0, "match_corrections": 0,
        "reference_rewiring": 0,
    }
    for op in manifest["team_creations"]:
        if db.fetchone("SELECT 1 FROM teams WHERE id = ?", (int(op["team_id"]),)):
            raise RefusedError(f"new team id {op['team_id']} already exists")
        beforeimages.record(str(op["entry_id"]), "team-create", "teams",
                            {"id": int(op["team_id"])}, None, str(op["reason"]))
        db.execute("INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
                   (int(op["team_id"]), op["name"]))
        counts["team_creations"] += 1
    for op in manifest["team_roots"]:
        actual = _row_dict(db.conn, "provider_team_mappings",
                           "provider = ? AND external_id = ?",
                           (PROVIDER_ESPN, str(op["external_id"])))
        beforeimages.record(str(op["entry_id"]), "team-root", "provider_team_mappings",
                            {"provider": PROVIDER_ESPN,
                             "external_id": str(op["external_id"])}, actual,
                            "reviewed provider root")
        if actual is None:
            db.execute(
                "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
                "VALUES (?, ?, ?)",
                (PROVIDER_ESPN, str(op["external_id"]), int(op["canonical_team_id"])),
            )
        counts["team_roots"] += 1

    for match_id, entry in merged.items():
        before = entry["before"]
        beforeimages.record(f"match-{match_id}", "match-correct", "matches",
                            {"id": match_id}, before, "enumerated correction")
        assignments = sorted(entry["fields"])
        sql = ", ".join(f'"{field}" = ?' for field in assignments)
        db.execute(f'UPDATE matches SET {sql} WHERE id = ?',
                   tuple(entry["fields"][field] for field in assignments) + (match_id,))
        counts["match_corrections"] += 1

    for op in manifest["team_renames"]:
        actual = _row_dict(db.conn, "teams", "id = ?", (int(op["team_id"]),))
        _assert_beforeimage(actual, op["expected_beforeimage"], "team rename")
        beforeimages.record(str(op["entry_id"]), "team-rename", "teams",
                            {"id": int(op["team_id"])}, actual, "reviewed display rename")
        db.execute("UPDATE teams SET name = ? WHERE id = ?",
                   (op["name"], int(op["team_id"])))
        counts["team_renames"] += 1

    for op in manifest["team_redirects"]:
        retired = int(op["retired_team_id"])
        actual = _row_dict(db.conn, "team_redirects", "retired_team_id = ?", (retired,))
        _assert_beforeimage(actual, op["expected_beforeimage"], "team redirect")
        if actual is None:
            beforeimages.record(str(op["entry_id"]), "redirect", "team_redirects",
                                {"retired_team_id": retired}, None, str(op["reason"]))
            db.execute(
                "INSERT INTO team_redirects (retired_team_id, canonical_team_id) "
                "VALUES (?, ?)",
                (retired, int(op["canonical_team_id"])),
            )
        counts["redirects"] += 1

    for op in manifest["alias_corrections"]:
        db.execute(
            "CREATE TABLE IF NOT EXISTS team_mappings (team_name TEXT PRIMARY KEY, "
            "team_id INTEGER, confidence REAL, source TEXT)"
        )
        actual = _row_dict(db.conn, "team_mappings", "team_name = ?",
                           (op["team_name"],))
        _assert_beforeimage(actual, op["expected_beforeimage"], "alias correction")
        beforeimages.record(str(op["entry_id"]), "alias-correct", "team_mappings",
                            {"team_name": op["team_name"]}, actual,
                            str(op["reason"]))
        if actual is None:
            db.execute("INSERT INTO team_mappings (team_name, team_id, confidence, source) "
                       "VALUES (?, ?, NULL, 'correction-rehearsal')",
                       (op["team_name"], int(op["team_id"])))
        else:
            db.execute("UPDATE team_mappings SET team_id = ? WHERE team_name = ?",
                       (int(op["team_id"]), op["team_name"]))
        counts["alias_corrections"] += 1

    for op in manifest["reference_rewiring"]:
        table = op["table"]
        column = op["column"]
        old = int(op["old_team_id"])
        new = int(op["new_team_id"])
        if not _table_exists(db.conn, table) or column not in _table_columns(db.conn, table):
            raise RefusedError(f"reference rewiring target {table}.{column} is missing")
        rows = db.fetchall(f'SELECT * FROM "{table}" WHERE "{column}" = ?', (old,))
        if len(rows) != int(op["expected_count"]):
            raise RefusedError(
                f"reference rewiring {table}.{column} expected {op['expected_count']} "
                f"row(s), found {len(rows)}"
            )
        columns = _table_columns(db.conn, table)
        for row in rows:
            beforeimages.record(str(op["entry_id"]), "reference-rewire", table,
                                {"column": column, "old_team_id": old},
                                {c: _normalize_value(v) for c, v in zip(columns, row)},
                                str(op["reason"]))
        db.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (new, old))
        counts["reference_rewiring"] += 1
    return counts


def _derived_snapshot(conn) -> Dict[str, Any]:
    solkoff: Dict[str, float] = {}
    if _table_exists(conn, "solkoff_coefficients"):
        for competition_id, team_id, value in conn.execute(
            "SELECT competition_id, team_id, solkoff_value FROM solkoff_coefficients"
        ).fetchall():
            solkoff[f"{competition_id}:{team_id}"] = float(value)
    elo: Dict[str, Dict[str, Any]] = {}
    if _table_exists(conn, "elo_ratings"):
        for team_id, rating, played in conn.execute(
            "SELECT team_id, rating, matches_played FROM elo_ratings"
        ).fetchall():
            elo[str(team_id)] = {"rating": float(rating), "matches_played": int(played)}
    standings_shape: Dict[str, int] = {}
    if _table_exists(conn, "standings"):
        for competition_id, count in conn.execute(
            "SELECT competition_id, COUNT(*) FROM standings GROUP BY competition_id "
            "ORDER BY competition_id"
        ).fetchall():
            standings_shape[str(competition_id)] = int(count)
    team_rows = int(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0])
    match_rows = int(conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
    return {"solkoff": solkoff, "elo": elo, "standings_shape": standings_shape,
            "team_rows": team_rows, "match_rows": match_rows}


class _AuditSolkoffCalculator(SolkoffCalculator):
    """Solkoff with a plan-pinned season window (never wall-clock derived)."""

    def __init__(self, db: Database, competition_id: str, start: str, end: str):
        super().__init__(db, competition_id=competition_id)
        self._start = start
        self._end = end

    def get_opponents(self, team_id: int) -> set:
        opponents = set()
        for row in self.db.fetchall(
            'SELECT away_team_id FROM matches WHERE home_team_id = ? '
            "AND status = 'FINISHED' AND competition_id = ? AND stage = 'LEAGUE_STAGE' "
            'AND "date" >= ? AND "date" < ?',
            (team_id, self.competition_id, self._start, self._end),
        ):
            opponents.add(row[0])
        for row in self.db.fetchall(
            'SELECT home_team_id FROM matches WHERE away_team_id = ? '
            "AND status = 'FINISHED' AND competition_id = ? AND stage = 'LEAGUE_STAGE' "
            'AND "date" >= ? AND "date" < ?',
            (team_id, self.competition_id, self._start, self._end),
        ):
            opponents.add(row[0])
        return opponents


def _rebuild_derived(db, beforeimages: _BeforeImages,
                     audit_bounds: Dict[str, str]) -> Dict[str, Any]:
    for competition_id in DERIVED_COMPETITIONS:
        if _table_exists(db.conn, "solkoff_coefficients"):
            for row in db.fetchall(
                "SELECT * FROM solkoff_coefficients WHERE competition_id = ?",
                (competition_id,),
            ):
                columns = _table_columns(db.conn, "solkoff_coefficients")
                beforeimages.record(f"solkoff-clear-{competition_id}", "solkoff-clear",
                                    "solkoff_coefficients",
                                    {"competition_id": competition_id},
                                    {c: _normalize_value(v) for c, v in zip(columns, row)},
                                    "stale derived coefficient cleared before rebuild")
            db.execute("DELETE FROM solkoff_coefficients WHERE competition_id = ?",
                       (competition_id,))
    if _table_exists(db.conn, "elo_ratings"):
        for row in db.fetchall("SELECT * FROM elo_ratings"):
            columns = _table_columns(db.conn, "elo_ratings")
            beforeimages.record("elo-clear", "elo-clear", "elo_ratings", {},
                                {c: _normalize_value(v) for c, v in zip(columns, row)},
                                "stale derived rating cleared before rebuild")
        db.execute("DELETE FROM elo_ratings")
    db.commit()
    solkoff_rows: Dict[str, int] = {}
    for competition_id in DERIVED_COMPETITIONS:
        _AuditSolkoffCalculator(db, competition_id, audit_bounds["start"],
                                audit_bounds["end"]).calculate_all()
        solkoff_rows[competition_id] = int(db.fetchone(
            "SELECT COUNT(*) FROM solkoff_coefficients WHERE competition_id = ?",
            (competition_id,),
        )[0])
    EloCalculator(db).calculate_all()
    elo_teams = int(db.fetchone("SELECT COUNT(*) FROM elo_ratings")[0])
    return {"solkoff_rows": solkoff_rows, "elo_teams": elo_teams}


def _delta(before: Dict[str, float], after: Dict[str, float]) -> Dict[str, Any]:
    changed = [key for key in set(before) | set(after)
               if before.get(key) != after.get(key)]
    deltas = [(key, after.get(key, 0.0) - before.get(key, 0.0)) for key in changed]
    deltas.sort(key=lambda item: (-abs(item[1]), item[0]))
    return {
        "before_rows": len(before),
        "after_rows": len(after),
        "changed_rows": len(changed),
        "max_abs_delta": round(max((abs(delta) for _, delta in deltas), default=0.0), 6),
        "sample": [{"key": key, "delta": round(delta, 6)} for key, delta in deltas[:10]],
    }


def _impact_report(before: Dict[str, Any], after: Dict[str, Any],
                   manifest: Dict[str, Any], stats: Dict[str, int]) -> Dict[str, Any]:
    elo_before = {key: value["rating"] for key, value in before["elo"].items()}
    elo_after = {key: value["rating"] for key, value in after["elo"].items()}
    qualifiers = sum(1 for _op in _iter_qualifiers(manifest))
    return {
        "team_rows": {"before": before["team_rows"], "after": after["team_rows"]},
        "match_rows": {"before": before["match_rows"], "after": after["match_rows"]},
        "operations": stats,
        "solkoff": _delta(before["solkoff"], after["solkoff"]),
        "elo": _delta(elo_before, elo_after),
        "archived_standings_shape": {
            "before": before["standings_shape"],
            "after": after["standings_shape"],
            "preserved": before["standings_shape"] == after["standings_shape"],
            "note": "diagnostic shape only; missing main-stage results mean this is "
                    "not a completed league table",
        },
        "qualifying_corrections": qualifiers,
        "metadata_discrepancies": manifest["metadata_discrepancies"],
        "pending_gates": {
            "status": "declared_not_verified",
            "main_stage_events": manifest["pending"]["main_stage_events"],
            "full_takeover_clubs_unapproved":
                manifest["pending"]["full_takeover_clubs_unapproved"],
            "note": manifest["pending"]["note"],
        },
    }


def _iter_qualifiers(manifest: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for op in manifest["match_corrections"]:
        if any(p.get("kind") == "espn-qualifier" for p in op["evidence"]):
            yield op


def _preflight_beforeimages(source: Path, inventory: Dict[str, Any],
                            manifest: Dict[str, Any],
                            merged: Dict[int, Dict[str, Any]]) -> None:
    """Check every exact beforeimage against a migrated throwaway source copy."""
    workdir = Path(tempfile.mkdtemp(prefix="espn-correction-preflight-"))
    os.chmod(workdir, 0o700)
    scratch = workdir / "migrated.db"
    try:
        shutil.copy2(str(source), str(scratch))
        os.chmod(scratch, 0o600)
        default = inventory["legacy_competition_default"]
        previous = os.environ.get("COMPETITION_ID")
        if default is not None:
            os.environ["COMPETITION_ID"] = default
        try:
            db = Database(str(scratch))
        finally:
            if previous is None:
                os.environ.pop("COMPETITION_ID", None)
            else:
                os.environ["COMPETITION_ID"] = previous
        try:
            _assert_team_beforeimages(db, manifest)
            _assert_match_beforeimages(db, merged)
        finally:
            db.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _load_bundle(source_db: str, manifest_path: str
                 ) -> Tuple[Dict[str, Any], Path, List[Dict[str, Any]],
                            Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]],
                            Dict[str, Any]]:
    path = Path(str(manifest_path))
    if not path.is_file():
        raise RefusedError(f"correction manifest {manifest_path!r} does not exist")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"correction manifest {manifest_path!r} is invalid JSON: {exc}")
    manifest = _require_object(manifest, "correction manifest")
    _verify_review(manifest, path)
    captures_dir = path.parent
    entries = _resolve_entries(manifest, captures_dir)
    main_by_id, qual_by_id = _parse_captures(entries, captures_dir)
    inventory = _inspect_source(source_db)
    merged, stats, roots = _validate_manifest(
        manifest, inventory, Path(str(source_db)), captures_dir, entries,
        main_by_id, qual_by_id,
    )
    _preflight_beforeimages(Path(str(manifest["source_path"])), inventory,
                            manifest, merged)
    return manifest, captures_dir, entries, main_by_id, qual_by_id, {
        "inventory": inventory, "merged": merged, "stats": stats, "roots": roots,
    }


def _receipt_base(manifest: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tool_revision": TOOL_REVISION,
        "profile": PROFILE,
        "profile_version": PROFILE_VERSION,
        "scope": REVIEW_SCOPE,
        "manifest_sha256": manifest_sha256(manifest),
        "review_manifest_sha256": review["manifest_sha256"],
        "source_sha256": manifest["source_sha256"],
        "pending_gates": {
            "status": "declared_not_verified",
            "main_stage_events": manifest["pending"]["main_stage_events"],
            "full_takeover_clubs_unapproved":
                manifest["pending"]["full_takeover_clubs_unapproved"],
            "note": manifest["pending"]["note"],
        },
        "audit_season_year": manifest["audit_season_year"],
        "audit_season_bounds": manifest["audit_season_bounds"],
        "metadata_discrepancies": manifest["metadata_discrepancies"],
        "rehearsal_only": True,
        "production_ready": False,
        "full_takeover": False,
        "full_takeover_guard": "pending",
    }


def _reserve_candidate(source: Path, output: Path) -> None:
    for suffix in (".wal", ".tmp", ".rehearsal-receipt.json",
                   ".rehearsal-beforeimages.jsonl"):
        _reject_private_artifact(str(output) + suffix, "rehearsal artifact")
    with open(str(source), "rb") as src:
        fd = _reserve_new_file(output)
        try:
            with os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException as exc:
            try:
                os.unlink(str(output))
            except OSError:
                pass
            raise RefusedError(f"could not materialize the rehearsal copy: {exc}")
    os.chmod(str(output), 0o600)


def _write_rejected_receipt(output: Path, receipt: Dict[str, Any],
                            exc: BaseException) -> None:
    receipt["state"] = "rejected"
    receipt["error"] = f"{type(exc).__name__}: {exc}"
    receipt["operation_counts"] = {}
    _atomic_write_exclusive(str(output) + ".rehearsal-receipt.json",
                            _canonical_json(receipt).encode())


def rehearse(source_db: str, manifest_path: str, output_db: str) -> Dict[str, Any]:
    """Build a correction-only candidate on a disposable copy."""
    source = _source_safety(source_db)
    output = _new_output_safety(output_db, "rehearsal output")
    _private_parent(output, "rehearsal output")
    manifest, captures_dir, _entries, _main, _qual, bundle = _load_bundle(
        source_db, manifest_path
    )
    review = _verify_review(manifest, manifest_path)
    source_sha = _file_sha256(source)
    beforeimages: Optional[_BeforeImages] = None
    receipt: Optional[Dict[str, Any]] = None
    db: Optional[Database] = None
    try:
        _reserve_candidate(source, output)
        if _file_sha256(source) != source_sha:
            raise RefusedError("source changed during the candidate copy; aborting")
        receipt = _receipt_base(manifest, review)
        receipt["output"] = str(output)
        try:
            beforeimages = _BeforeImages(str(output) + ".rehearsal-beforeimages.jsonl")
        except Exception as exc:  # noqa: BLE001
            raise RefusedError(f"could not create the beforeimages artifact: {exc}")
        try:
            default = bundle["inventory"]["legacy_competition_default"]
            previous = os.environ.get("COMPETITION_ID")
            if default is not None:
                os.environ["COMPETITION_ID"] = default
            try:
                db = Database(str(output))
            finally:
                if previous is None:
                    os.environ.pop("COMPETITION_ID", None)
                else:
                    os.environ["COMPETITION_ID"] = previous
            if default is not None:
                _rec._assert_legacy_attribution(db.conn, default)
            before_derived = _derived_snapshot(db.conn)
            _assert_team_beforeimages(db, manifest)
            _assert_match_beforeimages(db, bundle["merged"])
            with db.transaction():
                counts = _apply_profile(db, manifest, bundle["merged"], beforeimages)
            derived = _rebuild_derived(db, beforeimages,
                                       manifest["audit_season_bounds"])
            after_derived = _derived_snapshot(db.conn)
            db.close()
            db = None
            if _file_sha256(source) != source_sha:
                raise RefusedError("source changed during the rehearsal; aborting")
            verification = verify_candidate(str(output), manifest_path)
            receipt["operation_counts"] = counts
            receipt["derived"] = derived
            receipt["beforeimages"] = beforeimages.count
            receipt["impact"] = _impact_report(before_derived, after_derived,
                                               manifest, bundle["stats"])
            receipt["verification"] = {
                "checks": verification["checks"],
                "failures": verification["failures"],
            }
            receipt["state"] = "rehearsed" if not verification["failures"] else "rejected"
            receipt["candidate_logical_fingerprint"] = verification[
                "candidate_logical_fingerprint"
            ]
            _atomic_write_exclusive(str(output) + ".rehearsal-receipt.json",
                                    _canonical_json(receipt).encode())
            return receipt
        except Exception as exc:  # noqa: BLE001
            _write_rejected_receipt(output, receipt, exc)
            return receipt
    finally:
        if db is not None:
            db.close()
        if beforeimages is not None:
            beforeimages.close()


# ---------------------------------------------------------------------------
# independent verification
# ---------------------------------------------------------------------------

def _rebuild_expected(manifest: Dict[str, Any], inventory: Dict[str, Any],
                      merged: Dict[int, Dict[str, Any]], workdir: Path
                      ) -> Dict[str, str]:
    expected_path = workdir / "expected.db"
    source = Path(manifest["source_path"])
    shutil.copy2(str(source), str(expected_path))
    os.chmod(expected_path, 0o600)
    default = inventory["legacy_competition_default"]
    previous = os.environ.get("COMPETITION_ID")
    if default is not None:
        os.environ["COMPETITION_ID"] = default
    try:
        db = Database(str(expected_path))
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_ID", None)
        else:
            os.environ["COMPETITION_ID"] = previous
    beforeimages = _BeforeImages(str(workdir / "expected.jsonl"))
    try:
        with db.transaction():
            _apply_profile(db, manifest, merged, beforeimages)
        _rebuild_derived(db, beforeimages, manifest["audit_season_bounds"])
        return _logical_hashes(db.conn, _FINGERPRINT_TABLES)
    finally:
        db.close()
        beforeimages.close()


def _rows(conn, table: str, key_columns: Sequence[str]) -> Dict[Any, Dict[str, Any]]:
    if not _table_exists(conn, table):
        return {}
    columns = _table_columns(conn, table)
    result: Dict[Any, Dict[str, Any]] = {}
    for row in conn.execute(f'SELECT * FROM "{table}"').fetchall():
        item = {column: _normalize_value(value) for column, value in zip(columns, row)}
        key = tuple(item[column] for column in key_columns)
        key = key[0] if len(key) == 1 else key
        result[key] = item
    return result


def _migrated_source_snapshot(source: Path, default: Optional[str],
                              workdir: Path) -> Dict[str, Any]:
    path = workdir / "baseline.db"
    shutil.copy2(str(source), str(path))
    os.chmod(path, 0o600)
    previous = os.environ.get("COMPETITION_ID")
    if default is not None:
        os.environ["COMPETITION_ID"] = default
    try:
        db = Database(str(path))
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_ID", None)
        else:
            os.environ["COMPETITION_ID"] = previous
    try:
        snapshot = _derived_snapshot(db.conn)
        snapshot.update({
            "matches": _rows(db.conn, "matches", ("id",)),
            "teams": _rows(db.conn, "teams", ("id",)),
            "standings": _rows(db.conn, "standings",
                               ("competition_id", "team_id")),
            "team_redirects": _rows(db.conn, "team_redirects",
                                    ("retired_team_id",)),
            "team_mappings": _rows(db.conn, "team_mappings", ("team_name",)),
            "provider_team_mappings": _rows(
                db.conn, "provider_team_mappings", ("provider", "external_id")),
            "provider_match_mappings": _rows(
                db.conn, "provider_match_mappings", ("provider", "external_id")),
            "match_ids": {int(row[0]) for row in db.conn.execute(
                "SELECT id FROM matches").fetchall()},
            "team_ids": {int(row[0]) for row in db.conn.execute(
                "SELECT id FROM teams").fetchall()},
            "cl_standings": _cl_standings(db.conn),
        })
        return snapshot
    finally:
        db.close()


def _apply_rewire_to_rows(rows: Dict[Any, Dict[str, Any]], op: Dict[str, Any]) -> None:
    column = op["column"]
    old = int(op["old_team_id"])
    new = int(op["new_team_id"])
    for row in rows.values():
        if row.get(column) == old:
            row[column] = new


def _expected_state(baseline: Dict[str, Any], manifest: Dict[str, Any],
                    merged: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    matches = {key: dict(value) for key, value in baseline["matches"].items()}
    for match_id, entry in merged.items():
        matches[match_id] = dict(entry["after"])
    teams = {key: dict(value) for key, value in baseline["teams"].items()}
    for op in manifest["team_creations"]:
        teams[int(op["team_id"])] = {
            "id": int(op["team_id"]), "name": op["name"], "code": None, "crest": None,
        }
    for op in manifest["team_renames"]:
        teams[int(op["team_id"])]["name"] = op["name"]
    standings = {key: dict(value) for key, value in baseline["standings"].items()}
    redirects = {key: dict(value) for key, value in baseline["team_redirects"].items()}
    for op in manifest["team_redirects"]:
        redirects[int(op["retired_team_id"])] = {
            "retired_team_id": int(op["retired_team_id"]),
            "canonical_team_id": int(op["canonical_team_id"]),
        }
    aliases = {key: dict(value) for key, value in baseline["team_mappings"].items()}
    for op in manifest["alias_corrections"]:
        row = aliases.setdefault(op["team_name"], {
            "team_name": op["team_name"], "team_id": None, "confidence": None,
            "source": "correction-rehearsal",
        })
        row["team_id"] = int(op["team_id"])
    providers = {key: dict(value) for key, value in
                 baseline["provider_team_mappings"].items()}
    for root in manifest["team_roots"]:
        key = (PROVIDER_ESPN, str(root["external_id"]))
        providers[key] = {"provider": PROVIDER_ESPN,
                          "external_id": str(root["external_id"]),
                          "team_id": int(root["canonical_team_id"])}
    provider_matches = {key: dict(value) for key, value in
                        baseline["provider_match_mappings"].items()}
    state = {"matches": matches, "teams": teams, "standings": standings,
             "team_redirects": redirects, "team_mappings": aliases,
             "provider_team_mappings": providers,
             "provider_match_mappings": provider_matches}
    for op in manifest["reference_rewiring"]:
        table = op["table"]
        if table in state:
            _apply_rewire_to_rows(state[table], op)
    return state


def _cl_standings(conn) -> Dict[int, Dict[str, Any]]:
    if not _table_exists(conn, "standings"):
        return {}
    columns = _table_columns(conn, "standings")
    rows = conn.execute('SELECT * FROM standings WHERE competition_id = ? ORDER BY team_id',
                        ("CL",)).fetchall()
    return {int(row[columns.index("team_id")]):
            {column: _normalize_value(value) for column, value in zip(columns, row)}
            for row in rows}


def verify_candidate(candidate_db: str, manifest_path: str) -> Dict[str, Any]:
    """Independently rebuild and verify a correction rehearsal candidate."""
    path = Path(str(manifest_path))
    if not path.is_file():
        raise RefusedError(f"correction manifest {manifest_path!r} does not exist")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"correction manifest {manifest_path!r} is invalid JSON: {exc}")
    manifest = _require_object(manifest, "correction manifest")
    review = _verify_review(manifest, path)
    source = _rec._source_safety(manifest["source_path"])
    if _file_sha256(source) != manifest["source_sha256"]:
        raise RefusedError("pinned source hash no longer matches the manifest")
    entries = _resolve_entries(manifest, path.parent)
    main_by_id, qual_by_id = _parse_captures(entries, path.parent)
    inventory = _inspect_source(source)
    merged, stats, roots = _validate_manifest(
        manifest, inventory, source, path.parent, entries, main_by_id, qual_by_id
    )
    _reject_production(candidate_db, "candidate")
    _reject_alias(candidate_db, "candidate")
    candidate = Path(str(candidate_db))
    if not candidate.is_file():
        raise RefusedError(f"candidate {candidate_db!r} does not exist")

    workdir = Path(tempfile.mkdtemp(prefix="espn-correction-verify-"))
    os.chmod(workdir, 0o700)
    try:
        expected_hashes = _rebuild_expected(manifest, inventory, merged, workdir)
        baseline = _migrated_source_snapshot(
            source, inventory["legacy_competition_default"], workdir
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    candidate_hashes = _all_hashes(str(candidate))
    checks: Dict[str, Any] = {}
    failures: List[str] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            failures.append(name)

    check("independent_rebuild_matches", candidate_hashes == expected_hashes,
          sorted(set(candidate_hashes.items()) ^ set(expected_hashes.items())))
    expected_state = _expected_state(baseline, manifest, merged)
    conn = duckdb.connect(str(candidate), read_only=True)
    try:
        actual_state = {
            "matches": _rows(conn, "matches", ("id",)),
            "teams": _rows(conn, "teams", ("id",)),
            "standings": _rows(conn, "standings", ("competition_id", "team_id")),
            "team_redirects": _rows(conn, "team_redirects", ("retired_team_id",)),
            "team_mappings": _rows(conn, "team_mappings", ("team_name",)),
            "provider_team_mappings": _rows(
                conn, "provider_team_mappings", ("provider", "external_id")),
            "provider_match_mappings": _rows(
                conn, "provider_match_mappings", ("provider", "external_id")),
        }
        for table, expected_rows in expected_state.items():
            check(f"expected_state_{table}", actual_state[table] == expected_rows,
                  {"expected": len(expected_rows), "actual": len(actual_state[table])})
        candidate_match_ids = {int(row[0]) for row in conn.execute(
            "SELECT id FROM matches").fetchall()}
        candidate_team_ids = {int(row[0]) for row in conn.execute(
            "SELECT id FROM teams").fetchall()}
        check("match_id_set_unchanged", candidate_match_ids == baseline["match_ids"],
              {"before": len(baseline["match_ids"]), "after": len(candidate_match_ids)})
        created = {int(op["team_id"]) for op in manifest["team_creations"]}
        check("source_team_ids_retained", baseline["team_ids"] <= candidate_team_ids,
              {"source": len(baseline["team_ids"]), "candidate": len(candidate_team_ids)})
        check("created_team_count",
              len(candidate_team_ids - baseline["team_ids"]) == len(created),
              {"expected": sorted(created),
               "found": sorted(candidate_team_ids - baseline["team_ids"])})
        check("cl_standings_preserved", _cl_standings(conn) == baseline["cl_standings"],
              {"rows": len(baseline["cl_standings"])})
        dangling = conn.execute(
            "SELECT COUNT(*) FROM matches m WHERE "
            "NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = m.home_team_id) OR "
            "NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = m.away_team_id)"
        ).fetchone()[0]
        check("no_dangling_match_teams", dangling == 0, dangling)
        selfplay = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE home_team_id = away_team_id"
        ).fetchone()[0]
        check("no_self_play", selfplay == 0, selfplay)
        try:
            resolver = TeamRedirectResolver(conn)
            retired_ids = set(resolver.retired_ids())
            check("redirects_valid", True, len(retired_ids))
        except TeamIdentityError as exc:
            retired_ids = set()
            check("redirects_valid", False, str(exc))
        if retired_ids:
            placeholders = ",".join("?" * len(retired_ids))
            args = tuple(retired_ids)
            refs = conn.execute(
                f"SELECT COUNT(*) FROM matches WHERE home_team_id IN ({placeholders}) "
                f"OR away_team_id IN ({placeholders})", args + args
            ).fetchone()[0]
            for table, column in (("standings", "team_id"),
                                  ("provider_team_mappings", "team_id"),
                                  ("team_mappings", "team_id"),
                                  ("solkoff_coefficients", "team_id"),
                                  ("elo_ratings", "team_id")):
                if _table_exists(conn, table):
                    refs += conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({placeholders})',
                        args,
                    ).fetchone()[0]
            check("retired_ids_not_referenced", refs == 0, refs)
        for table, column in (("provider_team_mappings", "team_id"),
                              ("provider_match_mappings", "match_id"),
                              ("team_mappings", "team_id")):
            if not _table_exists(conn, table):
                continue
            target = "teams" if column == "team_id" else "matches"
            bad = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" x WHERE NOT EXISTS '
                f'(SELECT 1 FROM "{target}" r WHERE r.id = x."{column}")'
            ).fetchone()[0]
            check(f"no_dangling_{table}", bad == 0, bad)
        dynamic_refs: List[Tuple[str, str]] = []
        for row in conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
        ).fetchall():
            table, column = str(row[0]), str(row[1])
            if column == "team_id" or column.endswith("_team_id"):
                dynamic_refs.append((table, column))
        for table, column in sorted(set(dynamic_refs)):
            bad = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" x WHERE x."{column}" IS NOT NULL '
                f'AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = x."{column}")'
            ).fetchone()[0]
            check(f"dangling_reference_{table}_{column}", bad == 0, bad)
            if table == "team_redirects" or not retired_ids:
                continue
            ph = ",".join("?" * len(retired_ids))
            bad = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" x WHERE x."{column}" IN ({ph})',
                tuple(retired_ids),
            ).fetchone()[0]
            check(f"retired_reference_{table}_{column}", bad == 0, bad)
        for table, columns in _TEAM_REFERENCE_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            present = set(_table_columns(conn, table))
            for column in sorted(columns):
                if column not in present:
                    continue
                bad = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" x WHERE x."{column}" IS NOT NULL '
                    f'AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = x."{column}")'
                ).fetchone()[0]
                check(f"dangling_reference_{table}_{column}", bad == 0, bad)
                if table == "team_redirects":
                    continue
                if retired_ids:
                    ph = ",".join("?" * len(retired_ids))
                    bad = conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" x WHERE x."{column}" '
                        f'IN ({ph})', tuple(retired_ids)
                    ).fetchone()[0]
                    check(f"retired_reference_{table}_{column}", bad == 0, bad)
        for match_id, entry in merged.items():
            actual = _row_dict(conn, "matches", "id = ?", (match_id,))
            check(f"afterimage_{match_id}", actual == entry["after"],
                  None if actual == entry["after"] else actual)
        qualifier_rows = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE stage = 'QUALIFYING'"
        ).fetchone()[0]
        check("qualifying_rows_present",
              qualifier_rows >= manifest["expected"]["qualifying_corrections"],
              qualifier_rows)
        fingerprint_one = _fingerprint(conn)
        fingerprint_two = _fingerprint(conn)
        check("deterministic_fingerprint", fingerprint_one == fingerprint_two,
              fingerprint_one)
    finally:
        conn.close()
    if _file_sha256(source) != manifest["source_sha256"]:
        failures.append("source hash changed during verification")
        checks["source_immutable"] = {"ok": False,
                                      "detail": "source hash changed during verification"}
    else:
        checks["source_immutable"] = {"ok": True, "detail": manifest["source_sha256"]}
    receipt = _receipt_base(manifest, review)
    receipt.update({
        "candidate": str(candidate),
        "candidate_logical_fingerprint": _candidate_fingerprint(str(candidate)),
        "checks": checks,
        "failures": failures,
        "operation_counts": stats,
        "state": "rehearsed" if not failures else "rejected",
    })
    return receipt


def _all_hashes(candidate_db: str) -> Dict[str, str]:
    conn = duckdb.connect(str(candidate_db), read_only=True)
    try:
        return _logical_hashes(conn, _FINGERPRINT_TABLES)
    finally:
        conn.close()


def _candidate_fingerprint(candidate_db: str) -> str:
    return hashlib.sha256(_canonical_json(_all_hashes(candidate_db)).encode()).hexdigest()


def _fingerprint(conn) -> str:
    return hashlib.sha256(_canonical_json(
        _logical_hashes(conn, _FINGERPRINT_TABLES)
    ).encode()).hexdigest()


def inspect_source(source_db: str) -> Dict[str, Any]:
    """Public read-only source inventory helper for the private plan builder."""
    return _inspect_source(source_db)


def preview(source_db: str, manifest_path: str) -> Dict[str, Any]:
    """Validate a correction profile without writing a candidate."""
    source = _source_safety(source_db)
    manifest, _captures_dir, _entries, _main, _qual, bundle = _load_bundle(
        source_db, manifest_path
    )
    review = _verify_review(manifest, manifest_path)
    return {
        **_receipt_base(manifest, review),
        "state": "previewed",
        "source": str(source),
        "operation_counts": bundle["stats"],
        "pending_gates": {
            "status": "declared_not_verified",
            "main_stage_events": manifest["pending"]["main_stage_events"],
            "full_takeover_clubs_unapproved":
                manifest["pending"]["full_takeover_clubs_unapproved"],
            "note": manifest["pending"]["note"],
        },
        "would_build": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backend.espn_correction_rehearsal",
        description="Copy-only ESPN correction rehearsal (never an in-place apply).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    preview_parser = sub.add_parser("preview", help="validate without building")
    preview_parser.add_argument("--source", required=True)
    preview_parser.add_argument("--manifest", required=True)
    rehearse_parser = sub.add_parser("rehearse", help="build a disposable candidate")
    rehearse_parser.add_argument("--source", required=True)
    rehearse_parser.add_argument("--manifest", required=True)
    rehearse_parser.add_argument("--output-db", required=True)
    verify_parser = sub.add_parser("verify", help="independently verify a candidate")
    verify_parser.add_argument("--candidate", required=True)
    verify_parser.add_argument("--manifest", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "preview":
            print(_canonical_json(preview(args.source, args.manifest)))
            return 0
        if args.command == "rehearse":
            receipt = rehearse(args.source, args.manifest, args.output_db)
            print(_canonical_json(receipt))
            return 0 if receipt.get("state") == "rehearsed" else 1
        receipt = verify_candidate(args.candidate, args.manifest)
        print(_canonical_json(receipt))
        return 0 if receipt.get("state") == "rehearsed" else 1
    except CorrectionRehearsalError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
