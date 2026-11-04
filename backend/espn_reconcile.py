"""Offline, fail-closed ESPN legacy reconciliation tool.

This module is a *tool*, not a runtime component: it never runs inside FastAPI,
the scheduler or any request path. It reads an immutable source snapshot and a set
of pinned public ESPN response bodies, produces an inventory, validates an
explicit review manifest, and applies that manifest only to a brand-new candidate
database file. The source snapshot and any production data are never written to.

Design contract (see ``docs/superpowers/plans/2026-10-16-espn-reconciliation.md``):

* **No live work.** There is no ``--apply``/``--inplace`` mode, no network fetch
  and no environment bypass. The tool-local ESPN client reads *only* the exact
  hashed bytes named by the capture index; a missing capture raises instead of
  falling back to HTTP or an expired cache.
* **Capture-backed mutations.** Every ``adopt``/``insert`` operation is checked
  field-by-field (competition, season, participants, per-leg date/status/stage/
  round/score) against the parsed pinned event; the whole operation set is a
  partition of the invention scoped rows and captured events.
* **Independent provenance.** A legacy snapshot with no competition column may
  only be migrated when an independent, source-hash-pinned ``source.provenance.json``
  sidecar declares the configured competition. Unknown provenance refuses rather
  than guessing.
* **Explicit identity only.** The tool never guesses a club root. Every provider
  club in the scoped captures must map to a reviewed existing/new canonical id;
  root/member clusters must agree exactly with the redirects and provider roots.
* **Strict references.** ``reference_rewiring`` may only touch an allowlisted
  canonical team-id column whose ``old -> new`` pair is a declared cluster
  decision; an unexpected FK/reference table blocks the run.
* **Optional, proof-backed team metadata.** A manifest may carry an additive
  ``team_updates`` list that removes a real leading kickoff artifact from an
  existing root's display name and/or fills *missing* ``code``/``crest`` from the
  exact pinned provider record. Existing manifests without the key stay valid;
  established non-empty metadata (including CL) is never overwritten. Confirmed
  new roots may carry the same capture-proofed metadata at creation time.
* **Deterministic, independent verification.** Verification rebuilds the expected
  candidate from the source + manifest on a throwaway copy and compares logical
  fingerprints, so an unrelated mutation (e.g. a CL result) is caught even if a
  receipt were recomputed. The candidate and source are only ever read.

The logical-fingerprint format: tables are read with their columns in
``information_schema`` ordinal order, rows ordered by an explicit key, NULL
encoded as JSON ``null`` inside a canonical JSON envelope, then SHA-256.
Timestamp columns are excluded so a replay is compared logically, not by
wall-clock.
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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import duckdb

from backend.competitions import (
    PROVIDER_ESPN,
    espn_slug_for,
    is_supported,
    normalize_competition,
)
from backend.database import Database
from backend.elo_calculator import EloCalculator
from backend.espn_client import (
    ESPNClient,
    ESPNClientError,
    ESPNValidationError,
    _QUALIFYING_SLUGS,
)
from backend.espn_service import ESPNDataService
from backend.fixture_ids import INT32_MAX, INT32_MIN, provider_match_id
from backend.solkoff_calculator import SolkoffCalculator
from backend.team_identity import TeamIdentityError, TeamRedirectResolver

# Recorded in every receipt so an audit ties a candidate to the exact tool
# revision *and* the source file hash (never a bare constant).
TOOL_REVISION = "espn-reconcile/6"

MANIFEST_VERSION = 2
CAPTURE_INDEX_NAME = "captures.json"
PROVENANCE_NAME = "source.provenance.json"

SCOPED_COMPETITIONS: Tuple[str, ...] = ("EL", "UCL")
REQUIRED_SEASON_YEARS: Tuple[int, ...] = (2024, 2025, 2026)
PINNED_CURRENT_YEAR = 2026

# First instant of the new-format ("Swiss") window, matching the runtime guard.
_SCOPED_FROM = "2024-08-01"

PRODUCTION_BIND_PATHS: Tuple[str, ...] = ("/opt/ucl-solkoff/data", "/app/data")

_TIMESTAMP_COLUMNS = frozenset({
    "last_updated", "calculated_at", "updated_at", "cached_at", "expires_at",
    "collected_at",
})

_FINGERPRINT_TABLES: Tuple[str, ...] = (
    "teams", "matches", "standings", "solkoff_coefficients", "elo_ratings",
    "provider_team_mappings", "provider_match_mappings", "team_redirects",
    "team_mappings",
)

_ROOT_DECISIONS = frozenset({"reuse", "merge-reviewed-cluster", "create-confirmed-new"})
_FIXTURE_KINDS = frozenset({"adopt", "retire-duplicate", "quarantine", "qualifier", "insert"})
_TEAM_MAPPING_OPS = frozenset({"retire", "replace"})
_STANDINGS_ACTIONS = frozenset({"keep", "delete"})

# Canonical team-id reference columns. ``reference_rewiring`` may never touch
# anything else (a score/name/date column can therefore never be rewritten).
_TEAM_REFERENCE_COLUMNS: Dict[str, frozenset] = {
    "matches": frozenset({"home_team_id", "away_team_id"}),
    "standings": frozenset({"team_id"}),
    "solkoff_coefficients": frozenset({"team_id"}),
    "elo_ratings": frozenset({"team_id"}),
    "provider_team_mappings": frozenset({"team_id"}),
    "team_redirects": frozenset({"retired_team_id", "canonical_team_id"}),
    "team_mappings": frozenset({"team_id"}),
}

_MANIFEST_KEYS = frozenset({
    "version", "source_sha256", "source_path", "legacy_competition_default",
    "source_logical_hashes", "capture_index", "captures", "club_roots",
    "team_redirects", "team_mapping_operations", "fixture_operations",
    "standings_decisions", "reference_rewiring", "team_updates",
})

# ``team_updates`` is additive: an older manifest 2 without it remains valid, so
# it can never be a *required* key (and a reviewed manifest is never mutated with
# ``setdefault`` after its review hash was taken).
_REQUIRED_MANIFEST_KEYS = _MANIFEST_KEYS - frozenset({"team_updates"})

_TEAM_UPDATE_FIELDS = frozenset({"name", "code", "crest"})
_METADATA_PROOF_KEYS = ("url", "sha256", "provider_id")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# A genuine leading kickoff artifact: an optional weekday, an ISO/European/long
# date, an optional clock (with optional zone) and a separator. The remainder is
# preserved verbatim; a bare "1. FC" style club prefix never matches because a
# numeric date form needs a second numeric component.
_KICKOFF_PREFIX = re.compile(
    r"^\s*"
    r"(?:"
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"(?:\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"
    r"(?:[T\s]+\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z|[+-]\d{2}:?\d{2})?"
    r")"
    r"(?:\s*(?:[-\u2013\u2014:|,]\s*)+|\s+)"
)


class ReconcileError(RuntimeError):
    """A reconciliation precondition failed; the run must not proceed."""


class RefusedError(ReconcileError):
    """A fail-closed refusal surfaced to the CLI as a non-zero ``REFUSED`` line."""


# ---------------------------------------------------------------------------
# small primitives
# ---------------------------------------------------------------------------

def _file_sha256(path: os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, explicit ``null``."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _assert_int32(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RefusedError(f"{what} must be an integer, got {value!r}")
    if not (INT32_MIN <= value <= INT32_MAX):
        raise RefusedError(f"{what} {value} is outside the int32 range")
    return value


def _real(path: os.PathLike) -> str:
    return os.path.realpath(str(path))


def _reject_production(path: os.PathLike, label: str) -> None:
    real = _real(path)
    for base in PRODUCTION_BIND_PATHS:
        base_real = _real(base)
        if real == base_real or real.startswith(base_real + os.sep):
            raise RefusedError(
                f"refusing {label} {path!r}: it resolves inside a production bind "
                f"path ({base})"
            )


def _reject_alias(path: os.PathLike, label: str) -> None:
    """Reject symlinks / path aliases anywhere in ``path``'s components."""
    raw = os.path.abspath(str(path))
    real = os.path.realpath(str(path))
    if raw != real:
        raise RefusedError(
            f"refusing {label} {path!r}: it is a symlink or path alias "
            f"(resolves to {real!r})"
        )
    current = Path(raw)
    while True:
        if current.is_symlink():
            raise RefusedError(
                f"refusing {label} {path!r}: component {current!r} is a symlink"
            )
        if current.parent == current:
            break
        current = current.parent


def _source_safety(source_db: os.PathLike) -> Path:
    """Validate an immutable, read-only source snapshot path."""
    _reject_production(source_db, "source")
    _reject_alias(source_db, "source")
    path = Path(str(source_db))
    if not path.is_file():
        raise RefusedError(f"source {source_db!r} is not a regular file")
    if os.stat(str(path)).st_nlink > 1:
        raise RefusedError(
            f"refusing source {source_db!r}: it has multiple hard links "
            f"(another path aliases the same inode)"
        )
    for suffix in (".wal", ".tmp"):
        side = Path(str(path) + suffix)
        if side.exists() and side.stat().st_size > 0:
            raise RefusedError(
                f"refusing source {source_db!r}: an active adjacent "
                f"{suffix} sidecar exists"
            )
    return path


def _new_output_safety(output_db: os.PathLike, label: str = "output") -> Path:
    """Validate a brand-new output path; sidecar conflicts are checked by callers."""
    _reject_production(output_db, label)
    _reject_alias(output_db, label)
    path = Path(str(output_db))
    if os.path.exists(str(path)):
        raise RefusedError(
            f"refusing to write {label} {output_db!r}: target already exists "
            f"(even a partial candidate is never overwritten)"
        )
    parent = path.parent
    if not parent.is_dir():
        raise RefusedError(f"{label} parent directory {parent!r} does not exist")
    return path


def _reject_private_artifact(path: os.PathLike, label: str) -> None:
    """Reject an existing or unsafe output/report/receipt/beforeimage artifact."""
    _reject_production(path, label)
    _reject_alias(path, label)
    if os.path.exists(str(path)):
        raise RefusedError(f"refusing {label} {path!r}: an artifact already exists")


def _reserve_new_file(path: os.PathLike) -> int:
    """Atomically reserve a brand-new private file (no TOCTOU truncate)."""
    try:
        return os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RefusedError(f"refusing to create {path!r}: target already exists")


def _atomic_write_exclusive(path: os.PathLike, data: bytes) -> None:
    """Write ``data`` to a brand-new path without ever overwriting a target."""
    _reject_private_artifact(path, "artifact")
    directory = os.path.dirname(os.path.abspath(str(path)))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-reconcile-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, str(path))  # atomic, fails if the target exists
        except FileExistsError:
            raise RefusedError(f"refusing to overwrite existing artifact {path!r}")
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


class _BeforeImages:
    """Append-only private JSONL recorder for every changed/retired row."""

    def __init__(self, path: os.PathLike):
        self.path = str(path)
        self.count = 0
        self._fd = _reserve_new_file(self.path)

    def record(self, entry_id: str, operation: str, table: str,
               key: Dict[str, Any], before: Any, reason: Optional[str] = None) -> None:
        line = _canonical_json({
            "entry_id": entry_id,
            "operation": operation,
            "reason": reason,
            "table": table,
            "key": key,
            "before": before,
        })
        os.write(self._fd, (line + "\n").encode("utf-8"))
        os.fsync(self._fd)
        self.count += 1

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# capture index and pinned client
# ---------------------------------------------------------------------------

def _expected_capture_urls(competition: str, season_year: int) -> List[str]:
    slug = espn_slug_for(competition)
    year = int(season_year)
    return [
        ESPNClient._standings_url(slug, year),
        ESPNClient._scoreboard_url(slug, year),
        ESPNClient._scoreboard_url(slug, year + 1),
    ]


def _required_capture_keys() -> List[Tuple[str, int, str]]:
    keys: List[Tuple[str, int, str]] = []
    for competition in SCOPED_COMPETITIONS:
        for season_year in REQUIRED_SEASON_YEARS:
            for url in _expected_capture_urls(competition, season_year):
                keys.append((competition, season_year, url))
    return keys


def _read_capture_index(captures_dir: os.PathLike) -> List[Dict[str, Any]]:
    index_path = Path(str(captures_dir)) / CAPTURE_INDEX_NAME
    if not index_path.is_file():
        raise RefusedError(f"capture index {index_path!r} is missing")
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"capture index {index_path!r} is not valid JSON: {exc}")
    entries = payload.get("captures") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise RefusedError("capture index has no 'captures' list")
    cleaned: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RefusedError("capture index entry is not an object")
        for field in ("competition", "season_year", "url", "sha256", "path"):
            if field not in entry:
                raise RefusedError(f"capture entry {entry.get('url')!r} missing {field!r}")
        cleaned.append(dict(entry))
    return cleaned


def _capture_body_path(captures_dir: os.PathLike, entry: Dict[str, Any]) -> Path:
    """Resolve a capture body path, refusing absolute/``..``/symlink escapes."""
    rel = str(entry["path"])
    if os.path.isabs(rel):
        raise RefusedError(f"capture path {rel!r} is absolute")
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep):
        raise RefusedError(f"capture path {rel!r} escapes the captures directory")
    base = os.path.realpath(str(captures_dir))
    target = Path(base) / norm
    current = target
    base_path = Path(base)
    while base_path not in current.parents and current != base_path:
        if current.is_symlink():
            raise RefusedError(f"capture path {rel!r} has a symlink component")
        current = current.parent
    real = os.path.realpath(str(target))
    if real != base and not real.startswith(base + os.sep):
        raise RefusedError(f"capture path {rel!r} resolves outside the captures dir")
    _reject_production(real, "capture")
    return target


def _read_capture_body(captures_dir: os.PathLike, entry: Dict[str, Any]) -> bytes:
    body_path = _capture_body_path(captures_dir, entry)
    if not body_path.is_file():
        raise RefusedError(f"capture body {body_path!r} is missing")
    raw = body_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise RefusedError(
            f"capture body {entry['url']!r} hash {digest} does not match pinned "
            f"{entry['sha256']} (tampered or stale capture)"
        )
    return raw


class PinnedESPNClient(ESPNClient):
    """ESPN client that serves only manifest-pinned capture bytes."""

    def __init__(self, entries: Sequence[Dict[str, Any]], captures_dir: os.PathLike):
        super().__init__(cache=None)
        self._by_url: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            self._by_url[str(entry["url"])] = dict(entry)
        self.captures_dir = Path(str(captures_dir))
        self.served: List[str] = []

    def _request(self, url: str) -> Dict[str, Any]:  # type: ignore[override]
        entry = self._by_url.get(url)
        if entry is None:
            raise RefusedError(
                f"no pinned capture for ESPN URL {url!r}; refusing network fallback"
            )
        raw = _read_capture_body(self.captures_dir, entry)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise RefusedError(f"pinned capture for {url!r} is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise RefusedError(f"pinned capture for {url!r} is not a JSON object")
        self.served.append(url)
        return data


def _capture_status(entries: Sequence[Dict[str, Any]],
                    captures_dir: os.PathLike) -> Dict[str, Any]:
    by_key = {(e["competition"], int(e["season_year"]), e["url"]): e for e in entries}
    present: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for competition, season_year, url in _required_capture_keys():
        entry = by_key.get((competition, season_year, url))
        if entry is None:
            missing.append({"competition": competition, "season_year": season_year,
                            "url": url, "reason": "absent from index"})
            continue
        try:
            _read_capture_body(captures_dir, entry)
        except RefusedError as exc:
            missing.append({"competition": competition, "season_year": season_year,
                            "url": url, "reason": str(exc)})
            continue
        present.append({"competition": competition, "season_year": season_year,
                        "url": url, "sha256": entry["sha256"]})
    return {"complete": not missing, "present": present, "missing": missing}


def _validate_capture_index(entries: Sequence[Dict[str, Any]],
                            captures_dir: os.PathLike) -> None:
    """Refuse conflicting duplicate capture URLs (adjacent calendar pulls).

    The same URL legitimately appears under two adjacent season entries; every
    occurrence must pin the identical hash (and bytes), never a last-wins body.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        url = str(entry["url"])
        current = seen.get(url)
        if current is None:
            seen[url] = entry
            continue
        if current["sha256"] != entry["sha256"]:
            raise RefusedError(
                f"conflicting duplicate capture URL {url!r}: "
                f"{current['sha256']} != {entry['sha256']}"
            )
        try:
            first = _read_capture_body(captures_dir, current)
            second = _read_capture_body(captures_dir, entry)
        except RefusedError:
            continue
        if first != second:
            raise RefusedError(
                f"duplicate capture URL {url!r} has different bytes across entries"
            )


def _is_qualifying_slug(slug: Any) -> bool:
    return slug in _QUALIFYING_SLUGS or (
        isinstance(slug, str) and slug.startswith("qualifying")
    )


# A stateless parser reused for the runtime client's exact semantics (league
# identity check, per-event season filter and match parser).
_PARSER = ESPNClient(cache=None)


def _qualifying_detail(event, competition_id, season_year, url, sha256):
    """Extract one qualifying event, applying the same placeholder policy."""
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]
    try:
        status = _PARSER._map_status(comp.get("status") or event.get("status"))
    except ESPNValidationError as exc:
        raise RefusedError(f"pinned qualifying event has an unknown status: {exc}")
    home = away = None
    for competitor in comp.get("competitors") or []:
        team = competitor.get("team") or {}
        if not team.get("id"):
            continue
        if competitor.get("homeAway") == "home":
            home = competitor
        elif competitor.get("homeAway") == "away":
            away = competitor
    if home is None or away is None:
        if status == "FINISHED":
            raise RefusedError(
                f"pinned FINISHED qualifying event {event.get('id')!r} has an "
                f"unresolved team"
            )
        return None

    def _score(side):
        value = side.get("score")
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    leg = comp.get("leg")
    return {
        "url": url, "sha256": sha256,
        "competition": competition_id, "season_year": season_year,
        "slug": (event.get("season") or {}).get("slug"),
        "event_id": str(event.get("id")),
        "date": event.get("date") or comp.get("date"),
        "status": status,
        "leg": leg.get("value") if isinstance(leg, dict) else None,
        "home_external": str(home["team"]["id"]),
        "away_external": str(away["team"]["id"]),
        "home_score": _score(home), "away_score": _score(away),
    }


def _events_for_entry(entry: Dict[str, Any], captures_dir: os.PathLike
                      ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Parse one pinned scoreboard body into (main, qualifying) event maps.

    Reuses the runtime client's league-identity check, per-event season filter and
    match parser, so the tool never reimplements partial semantics. Each event is
    filtered on its own ``event.season.year`` against the requested season, which
    matters because a ``dates=<year>`` body mixes the requested and adjacent
    seasons.
    """
    url = str(entry["url"])
    if not url.split("?")[0].endswith("scoreboard"):
        return {}, {}
    raw = _read_capture_body(captures_dir, entry)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RefusedError(f"pinned scoreboard {url!r} is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise RefusedError(f"pinned scoreboard {url!r} is not a JSON object")
    competition = normalize_competition(entry["competition"])
    season_year = int(entry["season_year"])
    slug = espn_slug_for(competition)
    try:
        events = _PARSER._filter_events(season_year, payload, slug)
        matches, _diags = _PARSER._parse_matches(competition, season_year, events)
    except (ESPNValidationError, ESPNClientError) as exc:
        raise RefusedError(f"pinned scoreboard {url!r} rejected: {exc}")
    main: Dict[str, Dict[str, Any]] = {}
    for match in matches:
        main[str(match["externalEventId"])] = {
            "url": url, "sha256": entry["sha256"],
            "competition": competition, "season_year": season_year,
            "event_id": str(match["externalEventId"]),
            "date": match["date"], "status": match["status"],
            "stage": match["stage"], "round": match["round"], "leg": match["leg"],
            "home_external": match["home"]["externalTeamId"],
            "away_external": match["away"]["externalTeamId"],
            "home_score": match["homeScore"], "away_score": match["awayScore"],
        }
    qualifying: Dict[str, Dict[str, Any]] = {}
    for event in events:
        if not _is_qualifying_slug((event.get("season") or {}).get("slug")):
            continue
        detail = _qualifying_detail(event, competition, season_year, url,
                                    entry["sha256"])
        if detail is not None:
            qualifying[detail["event_id"]] = detail
    return main, qualifying


def _semantic_signature(detail: Dict[str, Any]) -> Tuple:
    return (
        detail["competition"], detail["season_year"],
        detail.get("home_external"), detail.get("away_external"),
        str(detail.get("date") or ""), detail.get("status"),
        detail.get("stage", detail.get("slug")), detail.get("leg"),
        detail.get("home_score"), detail.get("away_score"),
    )


def _parse_events(entries: Sequence[Dict[str, Any]], captures_dir: os.PathLike
                  ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Aggregate every pinned scoreboard body, refusing semantic id conflicts.

    The same event id appearing twice with a different club pair / date / stage /
    leg / status / score / season / competition is refused rather than silently
    overwritten (order-independent). FINISHED events with an unresolved team are
    refused; non-finished TBD events are skipped consistently.
    """
    _validate_capture_index(entries, captures_dir)
    main: Dict[str, Dict[str, Any]] = {}
    qualifying: Dict[str, Dict[str, Any]] = {}
    signatures: Dict[str, Tuple] = {}
    for entry in entries:
        entry_main, entry_qual = _events_for_entry(entry, captures_dir)
        for detail in list(entry_main.values()) + list(entry_qual.values()):
            event_id = detail["event_id"]
            signature = _semantic_signature(detail)
            existing = signatures.get(event_id)
            if existing is not None:
                if existing != signature:
                    raise RefusedError(
                        f"conflicting duplicate event id {event_id!r}: {existing} "
                        f"!= {signature} (same id, different fixture)"
                    )
                # Identical duplicate: keep a deterministic (sorted) proof source
                # so a valid correction proof is accepted regardless of index order.
                chosen = main.get(event_id) or qualifying.get(event_id)
                if (detail["url"], detail["sha256"]) < (chosen["url"],
                                                        chosen["sha256"]):
                    chosen["url"] = detail["url"]
                    chosen["sha256"] = detail["sha256"]
                continue
            signatures[event_id] = signature
            if detail.get("stage"):
                main[event_id] = detail
            else:
                qualifying[event_id] = detail
    return main, qualifying


def _captured_event_details(entries: Sequence[Dict[str, Any]],
                            captures_dir: os.PathLike) -> Dict[str, Dict[str, Any]]:
    """Public helper: main-stage captured events, tolerant of a missing body."""
    try:
        main, _ = _parse_events(entries, captures_dir)
    except RefusedError:
        return {}
    return main


# ---------------------------------------------------------------------------
# capture-proofed team metadata
# ---------------------------------------------------------------------------

def _strip_kickoff_prefix(value: Any) -> Optional[str]:
    """Return ``value`` without an actual leading kickoff artifact, or ``None``.

    The remainder (the established club name) is preserved verbatim. A value with
    no real leading date/clock prefix is never rewritten, so an arbitrary club
    substitution can never pass as a "cleanup".
    """
    if not isinstance(value, str):
        return None
    match = _KICKOFF_PREFIX.match(value)
    if match is None:
        return None
    remainder = value[match.end():].strip()
    if not remainder or remainder == value:
        return None
    return remainder


def _validate_metadata_proof_shape(proof: Any, what: str) -> Dict[str, str]:
    if not isinstance(proof, dict):
        raise RefusedError(f"{what} metadata_proof must be an object")
    _reject_unknown_keys(proof, _METADATA_PROOF_KEYS, f"{what} metadata_proof")
    _require_keys(proof, _METADATA_PROOF_KEYS, f"{what} metadata_proof")
    cleaned: Dict[str, str] = {}
    for key in _METADATA_PROOF_KEYS:
        value = proof.get(key)
        if not isinstance(value, str) or _looks_placeholder(value):
            raise RefusedError(f"{what} metadata_proof.{key} must be a real string")
        cleaned[key] = value
    if not _SHA256_HEX.match(cleaned["sha256"]):
        raise RefusedError(f"{what} metadata_proof.sha256 is not a sha256 hex digest")
    return cleaned


def _capture_role(entry: Dict[str, Any], what: str) -> str:
    """Prove a capture's actual URL role/competition/season from its entry."""
    competition = normalize_competition(entry["competition"])
    if competition not in SCOPED_COMPETITIONS:
        raise RefusedError(f"{what} capture competition {competition!r} is not scoped")
    season_year = int(entry["season_year"])
    expected = _expected_capture_urls(competition, season_year)
    url = str(entry["url"])
    if url == expected[0]:
        return "standings"
    if url in expected[1:]:
        return "scoreboard"
    raise RefusedError(
        f"{what} capture URL {url!r} is not a required {competition} "
        f"{season_year} standings/scoreboard capture (wrong role/competition/season)"
    )


def _raw_team_crest(team: Dict[str, Any]) -> Optional[str]:
    logos = team.get("logos")
    if isinstance(logos, list):
        for logo in logos:
            if isinstance(logo, dict) and logo.get("href"):
                return str(logo["href"])
    logo = team.get("logo")
    if isinstance(logo, str) and logo:
        return logo
    return None


def _provider_team_from_body(raw: bytes, entry: Dict[str, Any], role: str,
                             provider_id: str, captures_dir: os.PathLike,
                             what: str) -> Optional[Dict[str, Optional[str]]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RefusedError(f"{what} pinned capture is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise RefusedError(f"{what} pinned capture is not a JSON object")
    records: List[Dict[str, Optional[str]]] = []
    if role == "standings":
        for child in data.get("children") or []:
            if not isinstance(child, dict):
                continue
            standings = child.get("standings") or {}
            for item in standings.get("entries") or []:
                team = (item or {}).get("team") or {}
                if str(team.get("id")) != provider_id:
                    continue
                records.append({
                    "name": team.get("displayName"),
                    "code": team.get("abbreviation"),
                    "crest": _raw_team_crest(team),
                })
    else:
        # Only events this pinned body actually contributes (league identity and
        # per-event season filter already validated by the shared parser).
        main, qualifying = _events_for_entry(entry, captures_dir)
        valid_ids = set(main) | set(qualifying)
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            if str(event.get("id")) not in valid_ids:
                continue
            for competition in event.get("competitions") or []:
                for competitor in (competition or {}).get("competitors") or []:
                    team = (competitor or {}).get("team") or {}
                    if str(team.get("id")) != provider_id:
                        continue
                    records.append({
                        "name": team.get("displayName"),
                        "code": team.get("abbreviation"),
                        "crest": _raw_team_crest(team),
                    })
    if not records:
        return None
    distinct = {_canonical_json(record) for record in records}
    if len(distinct) > 1:
        raise RefusedError(
            f"{what} metadata proof provider {provider_id!r} has conflicting "
            f"records in the pinned capture"
        )
    return records[0]


def _provider_team_record(manifest: Dict[str, Any],
                          entries: Sequence[Dict[str, Any]],
                          captures_dir: os.PathLike, proof: Any,
                          what: str) -> Dict[str, Optional[str]]:
    """Resolve a capture-proofed provider team record (never an HTTP fetch)."""
    cleaned = _validate_metadata_proof_shape(proof, what)
    url = cleaned["url"]
    sha256 = cleaned["sha256"]
    provider_id = cleaned["provider_id"]
    # One scoreboard URL is legitimately shared by two adjacent season entries;
    # every claim that pins these exact bytes is tried (and its role/season
    # validated) so a valid fetched season is never rejected because an adjacent
    # empty season was listed first.
    claims = [
        entry for entry in manifest["captures"]
        if str(entry["url"]) == url and str(entry["sha256"]) == sha256
    ]
    if not claims:
        raise RefusedError(
            f"{what} metadata proof does not name a pinned manifest capture"
        )
    actual: Optional[Dict[str, Any]] = None
    for candidate in entries:
        if str(candidate["url"]) != url:
            continue
        if str(candidate["sha256"]) != sha256:
            raise RefusedError(
                f"{what} metadata proof hash does not match the pinned capture body"
            )
        if actual is None:
            actual = candidate
    if actual is None:
        raise RefusedError(f"{what} metadata proof capture is absent from the index")
    raw = _read_capture_body(captures_dir, actual)
    records: List[Dict[str, Optional[str]]] = []
    for claim in claims:
        role = _capture_role(claim, what)
        scoped_entry = dict(actual)
        scoped_entry["competition"] = claim["competition"]
        scoped_entry["season_year"] = claim["season_year"]
        record = _provider_team_from_body(
            raw, scoped_entry, role, provider_id, captures_dir, what
        )
        if record is not None:
            records.append(record)
    if not records:
        raise RefusedError(
            f"{what} metadata proof provider {provider_id!r} is not present in "
            f"the pinned capture"
        )
    distinct = {_canonical_json(record) for record in records}
    if len(distinct) > 1:
        raise RefusedError(
            f"{what} metadata proof provider {provider_id!r} has conflicting "
            f"records across pinned capture claims"
        )
    return records[0]


# ---------------------------------------------------------------------------
# logical fingerprints
# ---------------------------------------------------------------------------

def _table_columns(conn, table: str) -> List[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", (table,)
    ).fetchall())


def _pk_columns(conn, table: str) -> Optional[List[str]]:
    try:
        rows = conn.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() "
            "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
            (table,),
        ).fetchall()
    except duckdb.Error:
        return None
    if not rows:
        return []
    names = rows[0][0]
    if isinstance(names, str):
        return [names]
    return list(names)


def _order_columns(conn, table: str) -> List[str]:
    pk = _pk_columns(conn, table)
    if pk:
        return pk
    return _table_columns(conn, table)


def _logical_hashes(conn, tables: Iterable[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for table in tables:
        if not _table_exists(conn, table):
            continue
        columns = [c for c in _table_columns(conn, table) if c not in _TIMESTAMP_COLUMNS]
        order = [c for c in _order_columns(conn, table) if c in columns] or columns
        select = ", ".join(f'"{c}"' for c in columns)
        order_by = ", ".join(f'"{c}"' for c in order) or "1"
        rows = conn.execute(
            f'SELECT {select} FROM "{table}" ORDER BY {order_by}'
        ).fetchall()
        payload = [[_normalize_value(v) for v in row] for row in rows]
        envelope = {"table": table, "columns": columns, "rows": payload}
        result[table] = hashlib.sha256(_canonical_json(envelope).encode()).hexdigest()
    return result


def _reference_columns(conn) -> List[Dict[str, str]]:
    """Foreign keys pointing at ``teams`` (the reference inventory to guard)."""
    refs: List[Dict[str, str]] = []
    try:
        rows = conn.execute(
            "SELECT table_name, constraint_column_names, referenced_table "
            "FROM duckdb_constraints() WHERE constraint_type = 'FOREIGN KEY'"
        ).fetchall()
    except duckdb.Error:
        return refs
    for table, columns, referenced in rows:
        if referenced != "teams":
            continue
        cols = list(columns) if not isinstance(columns, str) else [columns]
        for column in cols:
            refs.append({"table": table, "column": column})
    return refs


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def _provenance_paths(source_path: Path, extra_dir: Optional[Path]) -> List[Path]:
    candidates = [source_path.parent / PROVENANCE_NAME]
    if extra_dir is not None:
        candidates.append(Path(str(extra_dir)) / PROVENANCE_NAME)
    return candidates


def _read_provenance(source_path: Path, source_sha: str,
                     extra_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Read a source-hash-pinned deployment provenance sidecar, or ``None``."""
    for path in _provenance_paths(source_path, extra_dir):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RefusedError(f"provenance {path!r} is not valid JSON: {exc}")
        if not isinstance(payload, dict):
            raise RefusedError(f"provenance {path!r} is not an object")
        unknown = [k for k in payload if k not in
                   ("version", "source_sha256", "configured_competition",
                    "evidence", "deployment")]
        if unknown:
            raise RefusedError(f"provenance {path!r} has unknown keys {sorted(unknown)}")
        for field in ("version", "source_sha256", "configured_competition", "evidence"):
            if field not in payload:
                raise RefusedError(f"provenance {path!r} missing {field!r}")
        if payload["source_sha256"] != source_sha:
            raise RefusedError(
                f"provenance {path!r} pins a different source hash; refusing "
                f"(stale or wrong snapshot)"
            )
        if not is_supported(payload["configured_competition"]):
            raise RefusedError(
                f"provenance {path!r} declares unsupported competition "
                f"{payload['configured_competition']!r}"
            )
        evidence = payload["evidence"]
        if not isinstance(evidence, list) or not evidence or any(
            _looks_placeholder(e) for e in evidence
        ):
            raise RefusedError(f"provenance {path!r} lacks non-placeholder evidence")
        return payload
    return None


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def _snapshot_keyed(conn) -> bool:
    for table in ("standings", "solkoff_coefficients"):
        if not _table_exists(conn, table):
            return False
        if "competition_id" not in _table_columns(conn, table):
            return False
        if set(_pk_columns(conn, table) or []) != {"competition_id", "team_id"}:
            return False
    return True


def _migrated_source_scratch(source_path: Path, default_competition: Optional[str]
                             ) -> Tuple[str, Dict[str, str], List[Dict[str, str]]]:
    """Privately copy + migrate the source; return (scratch, hashes, ref columns)."""
    tmp = tempfile.mkdtemp(prefix="espn-reconcile-inv-")
    os.chmod(tmp, 0o700)
    scratch = Path(tmp) / "migrated.db"
    shutil.copy2(str(source_path), str(scratch))
    os.chmod(str(scratch), 0o600)
    previous = os.environ.get("COMPETITION_ID")
    if default_competition is not None:
        os.environ["COMPETITION_ID"] = default_competition
    try:
        db = Database(str(scratch))
        try:
            hashes = _logical_hashes(db.conn, _FINGERPRINT_TABLES)
            refs = _reference_columns(db.conn)
        finally:
            db.close()
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_ID", None)
        else:
            os.environ["COMPETITION_ID"] = previous
    return str(scratch), hashes, refs


def inventory(source_db: str, captures_dir: str) -> Dict[str, Any]:
    """Read-only inventory of a source snapshot and its pinned captures."""
    source = _source_safety(source_db)
    captures_path = Path(str(captures_dir))
    if not captures_path.is_dir():
        raise RefusedError(f"captures directory {captures_dir!r} does not exist")

    source_sha = _file_sha256(source)
    entries = _read_capture_index(captures_path)
    status = _capture_status(entries, captures_path)

    provider_ids, main_event_ids, qualifying_event_ids = _inventory_scope(
        entries, captures_path
    )
    event_ids = main_event_ids | qualifying_event_ids

    conn = duckdb.connect(str(source), read_only=True)
    try:
        tables = sorted(
            row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        )
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }
        schema = {"tables": tables,
                  "columns": {t: _table_columns(conn, t) for t in tables}}
        team_ids = [int(r[0]) for r in conn.execute("SELECT id FROM teams").fetchall()] \
            if _table_exists(conn, "teams") else []
        scoped_rows_raw = _scoped_unmanaged_rows(conn)
        keyed = _snapshot_keyed(conn)
    finally:
        conn.close()

    if keyed:
        legacy_default: Optional[str] = None
        migration_required = False
    else:
        migration_required = True
        provenance = _read_provenance(source, source_sha, captures_path)
        legacy_default = (
            normalize_competition(provenance["configured_competition"])
            if provenance else None
        )
        if provenance is None:
            return {
                "tool_revision": TOOL_REVISION,
                "source": str(source),
                "source_sha256": source_sha,
                "schema_version": MANIFEST_VERSION,
                "schema": schema,
                "counts": counts,
                "team_ids": team_ids,
                "scoped_competitions": list(SCOPED_COMPETITIONS),
                "scoped_from": _SCOPED_FROM,
                "scoped_unmanaged_rows": scoped_rows_raw,
                "provider_team_ids": sorted(provider_ids),
                "provider_event_ids": sorted(event_ids),
                "provider_main_event_ids": sorted(main_event_ids),
                "provider_qualifying_event_ids": sorted(qualifying_event_ids),
                "capture_status": status,
                "snapshot_keyed": keyed,
                "migration_required": True,
                "provenance": None,
                "legacy_competition_default": None,
                "source_logical_hashes": None,
                "reference_columns": [],
                "can_build": False,
                "pending_reason": (
                    "legacy snapshot has no competition column and no "
                    "source-hash-pinned source.provenance.json; refusing to guess "
                    "a migration default"
                ),
            }

    scratch, migrated_hashes, refs = _migrated_source_scratch(
        source, legacy_default if migration_required else None
    )
    # The scoped/unmanaged partition is computed on the MIGRATED schema so
    # unattributed (NULL competition) and unknown-date 2024+ rows are visible
    # rather than silently ignored by the legacy column set.
    try:
        mconn = duckdb.connect(scratch, read_only=True)
        try:
            scoped_rows = _scoped_unmanaged_rows(mconn)
        finally:
            mconn.close()
    finally:
        shutil.rmtree(Path(scratch).parent, ignore_errors=True)

    return {
        "tool_revision": TOOL_REVISION,
        "source": str(source),
        "source_sha256": source_sha,
        "schema_version": MANIFEST_VERSION,
        "schema": schema,
        "counts": counts,
        "team_ids": team_ids,
        "scoped_competitions": list(SCOPED_COMPETITIONS),
        "scoped_from": _SCOPED_FROM,
        "scoped_unmanaged_rows": scoped_rows,
        "provider_team_ids": sorted(provider_ids),
        "provider_event_ids": sorted(event_ids),
        "provider_main_event_ids": sorted(main_event_ids),
        "provider_qualifying_event_ids": sorted(qualifying_event_ids),
        "capture_status": status,
        "snapshot_keyed": keyed,
        "migration_required": migration_required,
        "provenance": (None if not migration_required else {
            "configured_competition": legacy_default,
        }),
        "legacy_competition_default": legacy_default,
        "source_logical_hashes": migrated_hashes,
        "reference_columns": refs,
        "can_build": True,
        "pending_reason": None,
    }


_SCOPED_WHERE = (
    "(m.competition_id IN (?, ?) OR m.competition_id IS NULL) "
    "AND (m.\"date\" IS NULL OR m.\"date\" >= ?) "
    "AND m.stage IS DISTINCT FROM 'QUALIFYING' "
    "AND NOT EXISTS (SELECT 1 FROM provider_match_mappings p "
    "WHERE p.provider = ? AND p.match_id = m.id)"
)


def _scoped_unmanaged_rows(conn) -> List[Dict[str, Any]]:
    """Every in-scope row that is not yet mapped or reviewed QUALIFYING.

    Scope = a 2024+ (or unknown-date) row that is EL/UCL *or* unattributed
    (``competition_id IS NULL``). An unattributed 2024+ row must be explicitly
    classified or the candidate is refused; it is never guessed into CL.
    """
    if not _table_exists(conn, "matches"):
        return []
    cols = set(_table_columns(conn, "matches"))
    if not {"competition_id", "date"} <= cols:
        return []
    if not _table_exists(conn, "provider_match_mappings"):
        return []
    query = (
        "SELECT m.id, m.competition_id, m.\"date\", m.home_team_id, "
        "m.away_team_id, m.stage FROM matches m "
        f"WHERE {_SCOPED_WHERE} ORDER BY m.id"
    )
    rows = []
    for row in conn.execute(
        query, (*SCOPED_COMPETITIONS, _SCOPED_FROM, PROVIDER_ESPN)
    ).fetchall():
        rows.append({"match_id": int(row[0]), "competition_id": row[1],
                     "date": row[2], "home_team_id": int(row[3]),
                     "away_team_id": int(row[4]), "stage": row[5]})
    return rows


def _standings_provider_ids(raw: bytes) -> set:
    """Provider club ids from one pinned standings body (scoped season only)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return set()
    ids: set = set()
    for child in data.get("children") or []:
        standings = (child or {}).get("standings") or {}
        for entry in standings.get("entries") or []:
            team = (entry or {}).get("team") or {}
            if team.get("id"):
                ids.add(str(team["id"]))
    return ids


def _inventory_scope(entries: Sequence[Dict[str, Any]], captures_dir: os.PathLike
                     ) -> Tuple[set, set, set]:
    """Provider club ids + main/qualifying event ids, using the SHARED parser.

    Only scoped, validated standings and season-filtered main/qualifying events
    contribute (never an adjacent-season or pre-2023 tail). Unreadable/incomplete
    captures are skipped so inventory can report them as pending.
    """
    _validate_capture_index(entries, captures_dir)
    provider_ids: set = set()
    main_ids: set = set()
    qualifying_ids: set = set()
    for entry in entries:
        try:
            raw = _read_capture_body(captures_dir, entry)
        except RefusedError:
            # A missing/unreadable body is reported as an incomplete capture by
            # the status report; it never silently narrows the scope.
            continue
        if str(entry["url"]).split("?")[0].endswith("standings"):
            provider_ids |= _standings_provider_ids(raw)
            continue
        # The body is readable, so a parse rejection is a real schema failure:
        # propagate it rather than under-reporting a "complete" inventory.
        main, qualifying = _events_for_entry(entry, captures_dir)
        for detail in main.values():
            main_ids.add(detail["event_id"])
            provider_ids.add(detail["home_external"])
            provider_ids.add(detail["away_external"])
        for detail in qualifying.values():
            qualifying_ids.add(detail["event_id"])
            provider_ids.add(detail["home_external"])
            provider_ids.add(detail["away_external"])
    return provider_ids, main_ids, qualifying_ids


# ---------------------------------------------------------------------------
# manifest validation
# ---------------------------------------------------------------------------

def _require_keys(obj: Dict[str, Any], required: Iterable[str], what: str) -> None:
    missing = [k for k in required if k not in obj]
    if missing:
        raise RefusedError(f"{what} is missing required key(s): {sorted(missing)}")


def _reject_unknown_keys(obj: Dict[str, Any], allowed: Iterable[str], what: str) -> None:
    unknown = [k for k in obj if k not in allowed]
    if unknown:
        raise RefusedError(f"{what} has unknown key(s): {sorted(unknown)}")


def _looks_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    text = value.strip().lower()
    return text in {"", "<", ">", "todo", "tbd", "...", "placeholder"} or (
        text.startswith("<") and text.endswith(">")
    )


def _require_int(obj: Dict[str, Any], key: str, what: str) -> int:
    return _assert_int32(obj.get(key), f"{what}.{key}")


def _require_str(obj: Dict[str, Any], key: str, what: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise RefusedError(f"{what}.{key} must be a string")
    return value


def _require_list(obj: Dict[str, Any], key: str, what: str) -> list:
    value = obj.get(key)
    if not isinstance(value, list):
        raise RefusedError(f"{what}.{key} must be a list")
    return value


def _expected_beforeimage(op: Dict[str, Any], what: str) -> Optional[Dict[str, Any]]:
    if "expected_beforeimage" not in op:
        raise RefusedError(f"{what} requires an exact expected_beforeimage")
    value = op["expected_beforeimage"]
    if value is None:
        raise RefusedError(f"{what} expected_beforeimage may not be null")
    if not isinstance(value, dict):
        raise RefusedError(f"{what} expected_beforeimage must be an object")
    return value


def validate_manifest(manifest: Dict[str, Any], inventory: Dict[str, Any]) -> None:
    """Strictly validate a review manifest against a fresh inventory.

    Raises :class:`RefusedError`; returns ``None`` on success.
    """
    if not isinstance(manifest, dict):
        raise RefusedError("manifest must be a JSON object")
    _reject_unknown_keys(manifest, _MANIFEST_KEYS, "manifest")
    _require_keys(manifest, _REQUIRED_MANIFEST_KEYS, "manifest")

    if manifest["version"] != MANIFEST_VERSION:
        raise RefusedError(
            f"manifest version {manifest['version']!r} != {MANIFEST_VERSION}"
        )
    if manifest["source_sha256"] != inventory["source_sha256"]:
        raise RefusedError(
            "manifest source_sha256 does not match the source snapshot "
            "(stale or wrong manifest)"
        )
    if not isinstance(manifest["source_path"], str) or not manifest["source_path"]:
        raise RefusedError("manifest source_path must be a non-empty string")

    if not inventory.get("can_build"):
        raise RefusedError(
            f"inventory cannot build: {inventory.get('pending_reason')}"
        )
    if manifest["legacy_competition_default"] != inventory["legacy_competition_default"]:
        raise RefusedError(
            f"legacy_competition_default {manifest['legacy_competition_default']!r} "
            f"must match the inventory value "
            f"{inventory['legacy_competition_default']!r}"
        )
    if manifest["source_logical_hashes"] != inventory["source_logical_hashes"]:
        raise RefusedError(
            "manifest source_logical_hashes do not match the migrated source "
            "(stale manifest)"
        )

    # An FK into teams that is not an allowlisted canonical reference blocks us.
    for ref in inventory.get("reference_columns") or []:
        allowed = _TEAM_REFERENCE_COLUMNS.get(ref["table"])
        if allowed is None or ref["column"] not in allowed:
            raise RefusedError(
                f"unexpected team reference column {ref['table']}.{ref['column']} "
                f"is not a known canonical reference; run blocked"
            )

    declared: Dict[Tuple[str, int, str], str] = {}
    declared_pairs: Dict[Tuple[str, str], List[Tuple[str, int]]] = {}
    for entry in manifest["captures"]:
        if not isinstance(entry, dict):
            raise RefusedError("captures entries must be objects")
        _reject_unknown_keys(entry, ("competition", "season_year", "url", "sha256"),
                             "capture entry")
        _require_keys(entry, ("competition", "season_year", "url", "sha256"),
                      "capture entry")
        declared[(normalize_competition(entry["competition"]),
                  int(entry["season_year"]), entry["url"])] = entry["sha256"]
        declared_pairs.setdefault((str(entry["url"]), str(entry["sha256"])),
                                  []).append((normalize_competition(
                                      entry["competition"]), int(entry["season_year"])))
    for competition, season_year, url in _required_capture_keys():
        key = (competition, season_year, url)
        if key not in declared:
            raise RefusedError(
                f"manifest omits required capture {competition} {season_year} "
                f"{url!r} (partial source coverage)"
            )
        expected = None
        for present in inventory["capture_status"]["present"]:
            if (present["competition"], present["season_year"], present["url"]) == key:
                expected = present["sha256"]
        if expected is None:
            raise RefusedError(
                f"manifest references capture {competition} {season_year} {url!r} "
                f"that is not present/verified"
            )
        if declared[key] != expected:
            raise RefusedError(
                f"manifest capture hash for {competition} {season_year} {url!r} "
                f"does not match the verified capture"
            )

    provider_ids = set(inventory["provider_team_ids"])
    known_team_ids = set(inventory["team_ids"])
    reserved: set = set(known_team_ids)
    root_by_provider: Dict[str, int] = {}
    member_to_root: Dict[int, int] = {}
    declared_roots: Dict[int, str] = {}

    for root in manifest["club_roots"]:
        if not isinstance(root, dict):
            raise RefusedError("club_roots entries must be objects")
        _reject_unknown_keys(
            root,
            ("provider", "external_id", "canonical_team_id", "member_team_ids",
             "decision", "evidence", "expected_beforeimages", "name", "code",
             "crest", "metadata_proof"),
            "club root",
        )
        _require_keys(
            root,
            ("provider", "external_id", "canonical_team_id", "member_team_ids",
             "decision", "evidence"),
            "club root",
        )
        if root["provider"] != PROVIDER_ESPN:
            raise RefusedError(f"club root provider {root['provider']!r} unsupported")
        root_beforeimages = root.get("expected_beforeimages", [])
        if not isinstance(root_beforeimages, list):
            raise RefusedError("club root expected_beforeimages must be a list")
        for item in root_beforeimages:
            if not isinstance(item, dict):
                raise RefusedError("club root expected_beforeimages entries must be objects")
            _reject_unknown_keys(item, ("table", "key", "before"), "root beforeimage")
            _require_keys(item, ("table", "key", "before"), "root beforeimage")
            if not isinstance(item["key"], dict) or not item["key"]:
                raise RefusedError("root beforeimage key must be a non-empty column map")
        external_id = str(root["external_id"])
        if _looks_placeholder(external_id):
            raise RefusedError(f"club root has placeholder external_id {external_id!r}")
        if external_id not in provider_ids:
            raise RefusedError(
                f"club root external_id {external_id!r} is not in the scoped captures"
            )
        if external_id in root_by_provider:
            raise RefusedError(f"provider club {external_id!r} has multiple roots")
        decision = root["decision"]
        if decision not in _ROOT_DECISIONS:
            raise RefusedError(f"club root decision {decision!r} is not reviewed")
        evidence = _require_list(root, "evidence", f"club root {external_id}")
        if not evidence or any(_looks_placeholder(e) for e in evidence):
            raise RefusedError(f"club root {external_id!r} lacks non-placeholder evidence")
        metadata_proof = root.get("metadata_proof")
        if metadata_proof is not None:
            if decision != "create-confirmed-new":
                raise RefusedError(
                    f"club root {external_id!r} metadata_proof is only valid for a "
                    f"confirmed-new root (existing roots use team_updates)"
                )
            cleaned_proof = _validate_metadata_proof_shape(
                metadata_proof, f"club root {external_id}"
            )
            if (cleaned_proof["url"], cleaned_proof["sha256"]) not in declared_pairs:
                raise RefusedError(
                    f"club root {external_id!r} metadata proof does not name a "
                    f"pinned manifest capture"
                )
        for field in ("code", "crest"):
            if field not in root:
                continue
            if decision != "create-confirmed-new":
                raise RefusedError(
                    f"club root {external_id!r} {field} is only valid for a "
                    f"confirmed-new root (existing roots use team_updates)"
                )
            value = root[field]
            if not isinstance(value, str) or _looks_placeholder(value):
                raise RefusedError(
                    f"club root {external_id!r} {field} must be a real string"
                )
            if metadata_proof is None:
                raise RefusedError(
                    f"club root {external_id!r} {field} requires capture-backed "
                    f"metadata_proof"
                )
        canonical = _assert_int32(root["canonical_team_id"], "canonical_team_id")
        members = [_assert_int32(m, "member_team_id")
                   for m in _require_list(root, "member_team_ids", f"club root {external_id}")]
        if not members or canonical not in members:
            raise RefusedError(
                f"club root {external_id!r} canonical {canonical} must be a member"
            )
        if decision in ("reuse", "merge-reviewed-cluster"):
            if canonical not in known_team_ids:
                raise RefusedError(
                    f"club root {external_id!r} reuses unknown canonical team {canonical}"
                )
            for member in members:
                if member not in known_team_ids:
                    raise RefusedError(
                        f"club root {external_id!r} member {member} is not a known team"
                    )
        else:  # create-confirmed-new
            if canonical in reserved:
                raise RefusedError(
                    f"new canonical root {canonical} for {external_id!r} collides with "
                    f"an existing/reserved team id"
                )
            if canonical == 0:
                raise RefusedError("new canonical root may not be 0")
            if _looks_placeholder(root.get("name")):
                raise RefusedError(
                    f"new canonical root {canonical} for {external_id!r} needs a "
                    f"reviewed display name"
                )
        reserved.add(canonical)
        declared_roots[canonical] = decision
        root_by_provider[external_id] = canonical
        for member in members:
            existing = member_to_root.get(member)
            if existing is not None and existing != canonical:
                raise RefusedError(
                    f"team {member} is a member of two different roots "
                    f"({existing} and {canonical}); cross-root ownership"
                )
            member_to_root[member] = canonical

    unreviewed = provider_ids - set(root_by_provider)
    if unreviewed:
        raise RefusedError(
            f"scoped captures contain unreviewed provider club(s): {sorted(unreviewed)}"
        )

    redirect_pairs: List[Tuple[int, int]] = []
    for entry in manifest["team_redirects"]:
        if not isinstance(entry, dict):
            raise RefusedError("team_redirects entries must be objects")
        _reject_unknown_keys(
            entry, ("retired_team_id", "canonical_team_id", "reason", "entry_id"),
            "team redirect",
        )
        _require_keys(
            entry, ("retired_team_id", "canonical_team_id", "reason", "entry_id"),
            "team redirect",
        )
        retired = _require_int(entry, "retired_team_id", "team redirect")
        canonical = _require_int(entry, "canonical_team_id", "team redirect")
        if retired == canonical:
            raise RefusedError(f"team redirect {retired} points at itself")
        if _looks_placeholder(entry["reason"]) or _looks_placeholder(entry["entry_id"]):
            raise RefusedError("team redirect lacks a reviewed reason/entry_id")
        if retired not in known_team_ids or canonical not in known_team_ids:
            raise RefusedError(f"team redirect {retired}->{canonical} references a missing team")
        if member_to_root.get(canonical) != canonical:
            raise RefusedError(
                f"team redirect {retired}->{canonical} target is not a reviewed root"
            )
        if member_to_root.get(retired) != canonical:
            raise RefusedError(
                f"team redirect {retired}->{canonical} is not an unclaimed member of "
                f"that cluster"
            )
        redirect_pairs.append((retired, canonical))
    if len({r for r, _ in redirect_pairs}) != len(redirect_pairs):
        raise RefusedError("team_redirects repeat a retired id")

    team_columns = set((inventory.get("schema") or {}).get("columns", {}).get(
        "teams", []))
    update_targets: set = set()
    update_entry_ids: set = set()
    team_updates = manifest.get("team_updates")
    if team_updates is None:
        team_updates = []
    if not isinstance(team_updates, list):
        raise RefusedError("manifest team_updates must be a list when present")
    for update in team_updates:
        if not isinstance(update, dict):
            raise RefusedError("team_updates entries must be objects")
        keys = ("canonical_team_id", "entry_id", "reason", "expected_beforeimage",
                "fields", "metadata_proof")
        _reject_unknown_keys(update, keys, "team update")
        _require_keys(update, keys, "team update")
        target = _require_int(update, "canonical_team_id", "team update")
        if target not in declared_roots:
            raise RefusedError(
                f"team update target {target} is not a declared club-root canonical"
            )
        if target in update_targets:
            raise RefusedError(f"team update targets root {target} more than once")
        update_targets.add(target)
        entry_id = update["entry_id"]
        reason = update["reason"]
        if _looks_placeholder(entry_id) or _looks_placeholder(reason):
            raise RefusedError(f"team update {target} lacks a reviewed reason/entry_id")
        if entry_id in update_entry_ids:
            raise RefusedError(f"team update entry_id {entry_id!r} is duplicated")
        update_entry_ids.add(entry_id)
        fields = update["fields"]
        if not isinstance(fields, dict) or not fields:
            raise RefusedError(f"team update {target} needs a non-empty fields object")
        _reject_unknown_keys(fields, _TEAM_UPDATE_FIELDS, "team update fields")
        for field, value in fields.items():
            if not isinstance(value, str) or _looks_placeholder(value):
                raise RefusedError(
                    f"team update {target} field {field!r} must be a real string"
                )
        cleaned_proof = _validate_metadata_proof_shape(
            update["metadata_proof"], f"team update {target}"
        )
        if (cleaned_proof["url"], cleaned_proof["sha256"]) not in declared_pairs:
            raise RefusedError(
                f"team update {target} metadata proof does not name a pinned "
                f"manifest capture"
            )
        before = update["expected_beforeimage"]
        if declared_roots[target] == "create-confirmed-new":
            if before is not None:
                raise RefusedError(
                    f"team update {target} targets a confirmed-new root, so its "
                    f"expected_beforeimage must be null"
                )
            continue
        if not isinstance(before, dict):
            raise RefusedError(
                f"team update {target} requires the exact full source team beforeimage"
            )
        if team_columns and set(before) != team_columns:
            raise RefusedError(
                f"team update {target} beforeimage must carry the full teams row "
                f"columns {sorted(team_columns)}"
            )
        if "name" in fields and fields["name"] == before.get("name"):
            raise RefusedError(
                f"team update {target} name would not change the source value"
            )
        for field in ("code", "crest"):
            if field not in fields:
                continue
            if before.get(field) not in (None, ""):
                raise RefusedError(
                    f"team update {target} may only fill a missing {field}; the "
                    f"source value {before.get(field)!r} is protected"
                )

    scoped_ids = {row["match_id"] for row in inventory.get("scoped_unmanaged_rows") or []}
    seen_match_ids: set = set()
    seen_event_ids: set = set()

    for op in manifest["fixture_operations"]:
        if not isinstance(op, dict):
            raise RefusedError("fixture_operations entries must be objects")
        kind = op.get("kind")
        if kind not in _FIXTURE_KINDS:
            raise RefusedError(f"fixture operation {kind!r} is not reviewed")
        what = f"fixture operation {kind!r}"
        allowed = ["kind", "reason", "entry_id"]
        required = ["kind", "reason", "entry_id"]
        if kind in ("adopt", "insert"):
            event_fields = ["external_event_id", "competition_id", "season_year",
                            "home_team_id", "away_team_id", "stage", "round",
                            "date", "status", "home_score", "away_score"]
            allowed += event_fields
            required += event_fields
        if kind in ("adopt", "retire-duplicate", "quarantine", "qualifier"):
            allowed += ["match_id", "expected_beforeimage"]
            required += ["match_id", "expected_beforeimage"]
        if kind == "adopt":
            allowed += ["evidence"]
        if kind == "qualifier":
            allowed += ["qualifying"]
            required += ["qualifying"]
        _reject_unknown_keys(op, allowed, what)
        _require_keys(op, required, what)
        if _looks_placeholder(op.get("reason")) or _looks_placeholder(op.get("entry_id")):
            raise RefusedError(f"{what} lacks a reviewed reason/entry_id")

        if kind in ("adopt", "insert"):
            event_id = _require_str(op, "external_event_id", what)
            if event_id in seen_event_ids:
                raise RefusedError(f"event {event_id!r} has multiple fixture targets")
            seen_event_ids.add(event_id)
            _require_int(op, "home_team_id", what)
            _require_int(op, "away_team_id", what)
            if op["home_team_id"] == op["away_team_id"]:
                raise RefusedError(f"{what} {event_id!r} pairs a team with itself")
            if kind == "adopt":
                _validate_correction_evidence_shape(op, inventory)
        if kind in ("adopt", "retire-duplicate", "quarantine", "qualifier"):
            match_id = _require_int(op, "match_id", what)
            if match_id in seen_match_ids:
                raise RefusedError(f"match {match_id} has multiple fixture operations")
            seen_match_ids.add(match_id)
            if match_id not in scoped_ids:
                raise RefusedError(
                    f"{what} targets match {match_id} which is not an inventoried "
                    f"scoped unmanaged row (never CL/pre-2024)"
                )
            _expected_beforeimage(op, what)
        if kind == "qualifier":
            qual = op.get("qualifying")
            if not isinstance(qual, dict):
                raise RefusedError(f"{what} requires a qualifying evidence object")
            qual_keys = ("url", "sha256", "event_id", "home_external", "away_external",
                         "date", "round", "home_score", "away_score", "season_year")
            _reject_unknown_keys(qual, qual_keys, "qualifying evidence")
            _require_keys(qual, qual_keys, "qualifying evidence")

    if seen_match_ids != scoped_ids:
        raise RefusedError(
            f"scoped row partition incomplete: classified {sorted(seen_match_ids)} "
            f"but inventory holds {sorted(scoped_ids)}"
        )

    captured_main = set(inventory.get("provider_main_event_ids") or [])
    if seen_event_ids != captured_main:
        raise RefusedError(
            f"captured event partition incomplete: targeted {sorted(seen_event_ids)} "
            f"but captures hold {sorted(captured_main)}"
        )

    for op in manifest["team_mapping_operations"]:
        if not isinstance(op, dict):
            raise RefusedError("team_mapping_operations entries must be objects")
        if op.get("op") not in _TEAM_MAPPING_OPS:
            raise RefusedError(f"team mapping op {op.get('op')!r} is not reviewed")
        keys = ["op", "team_name", "reason", "entry_id", "expected_beforeimage"]
        if op["op"] == "replace":
            keys.append("team_id")
        _require_keys(op, keys, "team mapping op")
        _reject_unknown_keys(op, keys, "team mapping op")
        if _looks_placeholder(op.get("team_name")) or _looks_placeholder(op.get("reason")):
            raise RefusedError("team mapping op lacks a reviewed name/reason")
        if _looks_placeholder(op.get("entry_id")):
            raise RefusedError("team mapping op lacks an entry_id")
        _expected_beforeimage(op, "team mapping op")
        if op["op"] == "replace":
            _require_int(op, "team_id", "team mapping op")

    losers: set = set()
    for decision in manifest["standings_decisions"]:
        if not isinstance(decision, dict):
            raise RefusedError("standings_decisions entries must be objects")
        action = decision.get("action")
        if action not in _STANDINGS_ACTIONS:
            raise RefusedError(f"standings decision {action!r} is not reviewed")
        keys = ["action", "competition_id", "team_id", "reason", "entry_id",
                "expected_beforeimage"]
        if action == "keep":
            keys.append("loser_team_id")
        else:
            keys.append("keep_team_id")
        _require_keys(decision, keys, "standings decision")
        _reject_unknown_keys(decision, keys, "standings decision")
        if _looks_placeholder(decision.get("reason")):
            raise RefusedError("standings decision lacks a reviewed reason")
        _expected_beforeimage(decision, "standings decision")
        winner = _require_int(decision, "keep_team_id" if action == "delete" else "team_id",
                              "standings decision")
        loser = _require_int(decision, "team_id" if action == "delete" else "loser_team_id",
                             "standings decision")
        if member_to_root.get(loser) != winner:
            raise RefusedError(
                f"standings decision loser {loser} is not a retired member of winner "
                f"{winner}'s reviewed cluster"
            )
        if loser in losers:
            raise RefusedError(f"standings loser {loser} decided twice")
        losers.add(loser)

    for rewire in manifest["reference_rewiring"]:
        if not isinstance(rewire, dict):
            raise RefusedError("reference_rewiring entries must be objects")
        keys = ("table", "column", "old_team_id", "new_team_id", "reason", "entry_id")
        _require_keys(rewire, keys, "reference rewiring")
        _reject_unknown_keys(rewire, keys, "reference rewiring")
        if _looks_placeholder(rewire.get("reason")) or _looks_placeholder(rewire.get("entry_id")):
            raise RefusedError("reference rewiring lacks a reviewed reason/entry_id")
        table = _require_str(rewire, "table", "reference rewiring")
        column = _require_str(rewire, "column", "reference rewiring")
        if not _IDENTIFIER.match(table) or not _IDENTIFIER.match(column):
            raise RefusedError(
                f"reference rewiring {table}.{column} is not a plain identifier"
            )
        allowed = _TEAM_REFERENCE_COLUMNS.get(table)
        if allowed is None or column not in allowed:
            raise RefusedError(
                f"reference rewiring may not touch {table}.{column}; only canonical "
                f"team-id columns are allowed"
            )
        old = _require_int(rewire, "old_team_id", "reference rewiring")
        new = _require_int(rewire, "new_team_id", "reference rewiring")
        if old == new:
            raise RefusedError("reference rewiring is a no-op")
        if member_to_root.get(old) != new:
            raise RefusedError(
                f"reference rewiring {old}->{new} is not a declared cluster decision"
            )


# ---------------------------------------------------------------------------
# review receipt
# ---------------------------------------------------------------------------

def review_receipt_path(manifest_path: os.PathLike) -> Path:
    manifest = Path(str(manifest_path))
    return manifest.parent / (manifest.stem + ".review.json")


def manifest_sha256(manifest: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()


def _verify_review(manifest: Dict[str, Any], manifest_path: os.PathLike) -> None:
    path = review_receipt_path(manifest_path)
    if not path.is_file():
        raise RefusedError(
            f"review receipt {path!r} is missing; an unreviewed manifest cannot "
            f"be applied"
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"review receipt {path!r} is not valid JSON: {exc}")
    if receipt.get("manifest_sha256") != manifest_sha256(manifest):
        raise RefusedError(
            "review receipt does not pin this manifest (an entry changed after "
            "review); re-review required"
        )


def _load_bundle(manifest_path: os.PathLike) -> Tuple[Dict[str, Any], Path, List[Dict[str, Any]]]:
    path = Path(str(manifest_path))
    if not path.is_file():
        raise RefusedError(f"manifest {manifest_path!r} does not exist")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefusedError(f"manifest {manifest_path!r} is not valid JSON: {exc}")
    _verify_review(manifest, path)
    captures_dir = path.parent
    entries = _read_capture_index(captures_dir)
    if manifest.get("capture_index") != CAPTURE_INDEX_NAME:
        raise RefusedError(
            f"manifest capture_index {manifest.get('capture_index')!r} must be "
            f"{CAPTURE_INDEX_NAME!r}"
        )
    return manifest, captures_dir, entries


# ---------------------------------------------------------------------------
# build candidate
# ---------------------------------------------------------------------------

def _row_dict(conn, table: str, where: str, params: Sequence[Any]) -> Optional[Dict[str, Any]]:
    row = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', params).fetchone()
    if row is None:
        return None
    cols = _table_columns(conn, table)
    return {c: _normalize_value(v) for c, v in zip(cols, row)}


def _assert_beforeimage(actual: Optional[Dict[str, Any]],
                        expected: Optional[Dict[str, Any]], what: str) -> None:
    norm_expected = None if expected is None else {
        k: _normalize_value(v) for k, v in expected.items()
    }
    if actual != norm_expected:
        raise RefusedError(
            f"{what}: exact beforeimage mismatch (expected {norm_expected!r}, "
            f"found {actual!r})"
        )


def _assert_root_beforeimages(db, manifest: Dict[str, Any]) -> None:
    """Enforce any reviewed ``expected_beforeimages`` against the migrated source.

    Each entry is ``{table, key, before}`` where ``key`` is a primary-key column
    map and ``before`` is the exact expected row (or ``null`` for an absent row).
    """
    for root in manifest["club_roots"]:
        entries = root.get("expected_beforeimages") or []
        for item in entries:
            if not isinstance(item, dict):
                raise RefusedError("root expected_beforeimages entries must be objects")
            _reject_unknown_keys(item, ("table", "key", "before"),
                                 "root beforeimage")
            _require_keys(item, ("table", "key", "before"), "root beforeimage")
            table = item["table"]
            if not isinstance(table, str) or not _IDENTIFIER.match(table):
                raise RefusedError(f"root beforeimage table {table!r} is not valid")
            if not _table_exists(db.conn, table):
                raise RefusedError(f"root beforeimage table {table!r} does not exist")
            columns = set(_table_columns(db.conn, table))
            key = item["key"]
            if not isinstance(key, dict) or not key or not set(key) <= columns:
                raise RefusedError(
                    f"root beforeimage {table} key {key!r} is not a real column map"
                )
            where = " AND ".join(f'"{c}" = ?' for c in key)
            actual = _row_dict(db.conn, table, where, tuple(key.values()))
            _assert_beforeimage(actual, item["before"],
                                f"club root {root['external_id']} beforeimage")


def _resolve_provider_root(db, external_id: str) -> int:
    row = db.fetchone(
        "SELECT team_id FROM provider_team_mappings WHERE provider = ? AND external_id = ?",
        (PROVIDER_ESPN, str(external_id)),
    )
    if not row:
        raise RefusedError(f"no reviewed provider mapping for ESPN club {external_id!r}")
    canonical = TeamRedirectResolver(db).canonical_id(int(row[0]))
    if canonical is None or not db.fetchone("SELECT 1 FROM teams WHERE id = ?", (canonical,)):
        raise RefusedError(f"provider {external_id!r} resolves to missing team")
    return int(canonical)


def _provider_root_map(manifest: Dict[str, Any]) -> Dict[str, int]:
    return {str(r["external_id"]): int(r["canonical_team_id"])
            for r in manifest["club_roots"]}


def _validate_fixture_event(op: Dict[str, Any], detail: Dict[str, Any],
                            roots: Dict[str, int], *, adopt: bool) -> None:
    """Field-by-field capture backing for one adopt/insert operation."""
    what = f"fixture operation {op['kind']!r} {op['external_event_id']!r}"
    if normalize_competition(op["competition_id"]) != detail["competition"]:
        raise RefusedError(f"{what}: competition does not match the pinned event")
    if int(op["season_year"]) != detail["season_year"]:
        raise RefusedError(f"{what}: season_year does not match the pinned event")
    if op["stage"] != detail.get("stage"):
        raise RefusedError(f"{what}: stage {op['stage']!r} != pinned {detail.get('stage')!r}")
    if op.get("round") != detail.get("round"):
        raise RefusedError(f"{what}: round {op.get('round')!r} != pinned {detail.get('round')!r}")
    if op["status"] != detail["status"]:
        raise RefusedError(f"{what}: status {op['status']!r} != pinned {detail['status']!r}")
    if op.get("home_score") != detail["home_score"] or op.get("away_score") != detail["away_score"]:
        raise RefusedError(
            f"{what}: score {op.get('home_score')}-{op.get('away_score')} contradicts "
            f"pinned {detail['home_score']}-{detail['away_score']}"
        )
    if detail["home_external"] is None or detail["away_external"] is None:
        raise RefusedError(f"{what}: pinned event has an unresolved participant")
    expected_home = roots.get(detail["home_external"])
    expected_away = roots.get(detail["away_external"])
    if expected_home is None or expected_away is None:
        raise RefusedError(f"{what}: pinned participant has no reviewed club root")
    if (op["home_team_id"], op["away_team_id"]) != (expected_home, expected_away):
        raise RefusedError(
            f"{what}: participants ({op['home_team_id']},{op['away_team_id']}) do not "
            f"match pinned club roots ({expected_home},{expected_away})"
        )
    # The manifest may NEVER override the pinned capture date. Every adopt/insert
    # must carry the pinned event's exact date; correcting an existing row's old
    # date to that pinned date still needs structured evidence, but the declared
    # date itself is fixed by the capture.
    if op["date"] != detail["date"]:
        raise RefusedError(
            f"{what}: date {op['date']!r} != pinned {detail['date']!r} "
            f"(a manifest may not override the captured date)"
        )


_CORRECTION_EVIDENCE_KEYS = ("url", "sha256", "event_id", "season_year",
                             "competition_id", "correction_reason",
                             "expected_beforeimage")


def _validate_correction_evidence_shape(op: Dict[str, Any],
                                        inventory: Dict[str, Any]) -> None:
    """Validate structured, capture-pinned adopt correction evidence shape."""
    evidence = op.get("evidence")
    if evidence is None:
        return
    if not isinstance(evidence, list) or not evidence:
        raise RefusedError("adopt evidence must be a non-empty list of objects")
    present = {(p["url"], p["sha256"]) for p in inventory["capture_status"]["present"]}
    captured = set(inventory.get("provider_main_event_ids") or [])
    for item in evidence:
        if not isinstance(item, dict):
            raise RefusedError(
                "adopt evidence must be structured objects, not free-form strings"
            )
        _reject_unknown_keys(item, _CORRECTION_EVIDENCE_KEYS, "correction evidence")
        _require_keys(item, _CORRECTION_EVIDENCE_KEYS, "correction evidence")
        if (item["url"], item["sha256"]) not in present:
            raise RefusedError("correction evidence does not name a pinned capture")
        if str(item["event_id"]) != str(op["external_event_id"]):
            raise RefusedError("correction evidence event_id mismatch")
        if int(item["season_year"]) != int(op["season_year"]):
            raise RefusedError("correction evidence season_year mismatch")
        if normalize_competition(item["competition_id"]) != normalize_competition(
            op["competition_id"]
        ):
            raise RefusedError("correction evidence competition mismatch")
        if str(item["event_id"]) not in captured:
            raise RefusedError("correction evidence event is not a captured main event")
        if _looks_placeholder(item["correction_reason"]):
            raise RefusedError("correction evidence lacks a reviewed reason")
        if not isinstance(item["expected_beforeimage"], dict):
            raise RefusedError("correction evidence needs a precise beforeimage")


def _require_correction_evidence(op: Dict[str, Any], detail: Dict[str, Any],
                                 actual: Optional[Dict[str, Any]],
                                 event_id: str) -> None:
    """Require structured evidence that pins this captured event and prior row."""
    evidence = op.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise RefusedError(
            f"adopt {event_id!r} corrects a FINISHED score/date/participant and "
            f"requires capture-pinned correction evidence"
        )
    for item in evidence:
        if not isinstance(item, dict):
            raise RefusedError("correction evidence must be objects, not free text")
        if (str(item.get("event_id")) != str(event_id)
                or item.get("url") != detail["url"]
                or item.get("sha256") != detail["sha256"]
                or int(item.get("season_year", -1)) != detail["season_year"]
                or normalize_competition(item.get("competition_id"))
                != detail["competition"]):
            raise RefusedError(
                "correction evidence does not pin this exact captured event"
            )
        if _looks_placeholder(item.get("correction_reason")):
            raise RefusedError("correction evidence lacks a reviewed reason")
        _assert_beforeimage(actual, item.get("expected_beforeimage"),
                            "correction evidence")


def _resolve_root_metadata(manifest: Dict[str, Any],
                           entries: Sequence[Dict[str, Any]],
                           captures_dir: Path) -> Dict[int, Dict[str, str]]:
    """Capture-proofed creation metadata for confirmed-new roots."""
    resolved: Dict[int, Dict[str, str]] = {}
    for root in manifest["club_roots"]:
        if root["decision"] != "create-confirmed-new":
            continue
        canonical = int(root["canonical_team_id"])
        proof = root.get("metadata_proof")
        if proof is None:
            resolved[canonical] = {}
            continue
        what = f"club root {root['external_id']}"
        cleaned_proof = _validate_metadata_proof_shape(proof, what)
        declared_external = str(root["external_id"])
        if cleaned_proof["provider_id"] != declared_external:
            raise RefusedError(
                f"{what} metadata proof provider "
                f"{cleaned_proof['provider_id']!r} does not match the declared "
                f"root external_id {declared_external!r}"
            )
        record = _provider_team_record(manifest, entries, captures_dir, proof, what)
        if root.get("name") != record.get("name"):
            raise RefusedError(
                f"{what} reviewed name {root.get('name')!r} does not match the "
                f"pinned provider record {record.get('name')!r}"
            )
        metadata = {"name": str(root["name"])}
        for field in ("code", "crest"):
            if field not in root:
                continue
            if root[field] != record.get(field):
                raise RefusedError(
                    f"{what} {field} {root[field]!r} contradicts pinned provider "
                    f"value {record.get(field)!r}"
                )
            metadata[field] = str(root[field])
        resolved[canonical] = metadata
    return resolved


def _resolve_team_updates(db, manifest: Dict[str, Any],
                          entries: Sequence[Dict[str, Any]],
                          captures_dir: Path,
                          root_metadata: Dict[int, Dict[str, str]]
                          ) -> Dict[int, Dict[str, Any]]:
    """Prove every update (target, beforeimage, provider record, after values)."""
    resolved: Dict[int, Dict[str, Any]] = {}
    root_by_canonical = {
        int(root["canonical_team_id"]): root for root in manifest["club_roots"]
    }
    for update in manifest.get("team_updates") or []:
        target = int(update["canonical_team_id"])
        root = root_by_canonical[target]
        what = f"team update {target}"
        actual = _row_dict(db.conn, "teams", "id = ?", (target,))
        is_new = root["decision"] == "create-confirmed-new"
        if is_new:
            if actual is not None:
                raise RefusedError(
                    f"{what} targets a confirmed-new root but a source team row "
                    f"already exists"
                )
        else:
            _assert_beforeimage(actual, update["expected_beforeimage"], what)
            if actual is None:
                raise RefusedError(f"{what} target team row is missing")
        cleaned_proof = _validate_metadata_proof_shape(
            update["metadata_proof"], what
        )
        declared_external = str(root["external_id"])
        if cleaned_proof["provider_id"] != declared_external:
            raise RefusedError(
                f"{what} metadata proof provider "
                f"{cleaned_proof['provider_id']!r} does not match the "
                f"declared root external_id {declared_external!r}"
            )
        record = _provider_team_record(
            manifest, entries, captures_dir, update["metadata_proof"], what
        )
        after: Dict[str, str] = {}
        for field, value in update["fields"].items():
            if field == "name":
                stripped = _strip_kickoff_prefix(actual.get("name")) if actual else None
                provider_name = record.get("name")
                if value != provider_name and value != stripped:
                    raise RefusedError(
                        f"{what} name {value!r} is neither the actual kickoff-prefix "
                        f"cleanup {stripped!r} nor the pinned provider spelling "
                        f"{provider_name!r}"
                    )
            else:
                provider_value = record.get(field)
                if not provider_value:
                    raise RefusedError(
                        f"{what} pinned provider record has no usable {field}"
                    )
                if value != provider_value:
                    raise RefusedError(
                        f"{what} {field} {value!r} contradicts pinned provider value "
                        f"{provider_value!r}"
                    )
                if actual is not None and actual.get(field) not in (None, ""):
                    raise RefusedError(
                        f"{what} may only fill a missing {field}; the source value "
                        f"{actual.get(field)!r} is protected"
                    )
                if (root_metadata.get(target) or {}).get(field):
                    raise RefusedError(
                        f"{what} {field} is already supplied by the new-root "
                        f"creation metadata"
                    )
            after[field] = value
        resolved[target] = {
            "entry_id": str(update["entry_id"]),
            "reason": str(update["reason"]),
            "before": actual,
            "after": after,
        }
    return resolved


def _apply_resolved_team_updates(db, resolved: Dict[int, Dict[str, Any]],
                                 beforeimages: "_BeforeImages") -> int:
    """Apply proved updates only after creation, keeping every other field intact."""
    for target in sorted(resolved):
        item = resolved[target]
        current = _row_dict(db.conn, "teams", "id = ?", (target,))
        if current is None:
            raise RefusedError(f"team update {target} target team row is missing")
        if item["before"] is not None:
            _assert_beforeimage(current, item["before"], f"team update {target}")
        else:
            for field in ("code", "crest"):
                if field in item["after"] and current.get(field) not in (None, ""):
                    raise RefusedError(
                        f"team update {target} may only fill a missing {field}"
                    )
        beforeimages.record(
            item["entry_id"], "team-update", "teams", {"id": target}, current,
            item["reason"],
        )
        assignments = ", ".join(f'"{field}" = ?' for field in item["after"])
        db.execute(
            f"UPDATE teams SET {assignments} WHERE id = ?",
            (*item["after"].values(), target),
        )
    return len(resolved)


def _apply_manifest(db, manifest: Dict[str, Any], captures_dir: Path,
                    entries: Sequence[Dict[str, Any]],
                    beforeimages: _BeforeImages) -> Dict[str, Any]:
    main_events, qualifying_events = _parse_events(entries, captures_dir)
    roots = _provider_root_map(manifest)
    root_map = _reviewed_root_resolution(manifest)
    counts = {
        "club_roots": 0, "redirects": 0, "provider_mappings": 0,
        "team_mapping_ops": 0, "fixture_ops": 0, "standings_decisions": 0,
        "reference_rewiring": 0, "team_updates": 0,
    }

    # Reviewed source beforeimages are enforced before any mutation: a root whose
    # declared affected rows do not match the migrated source blocks the run.
    _assert_root_beforeimages(db, manifest)
    # Every optional team update is proved (target root, exact beforeimage, pinned
    # provider record, after values) before the first write.
    root_metadata = _resolve_root_metadata(manifest, entries, captures_dir)
    resolved_updates = _resolve_team_updates(
        db, manifest, entries, captures_dir, root_metadata
    )

    for root in manifest["club_roots"]:
        if root["decision"] != "create-confirmed-new":
            continue
        canonical = int(root["canonical_team_id"])
        metadata = root_metadata.get(canonical) or {}
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
            (canonical,
             metadata.get("name") or root.get("name")
             or f"ESPN club {root['external_id']}",
             metadata.get("code"), metadata.get("crest")),
        )
    for root in manifest["club_roots"]:
        db.execute(
            "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
            "VALUES (?, ?, ?) ON CONFLICT (provider, external_id) "
            "DO UPDATE SET team_id = excluded.team_id",
            (PROVIDER_ESPN, str(root["external_id"]), int(root["canonical_team_id"])),
        )
        counts["club_roots"] += 1

    counts["team_updates"] = _apply_resolved_team_updates(
        db, resolved_updates, beforeimages
    )

    for entry in manifest["team_redirects"]:
        retired = int(entry["retired_team_id"])
        canonical = int(entry["canonical_team_id"])
        existing = db.fetchone(
            "SELECT canonical_team_id FROM team_redirects WHERE retired_team_id = ?",
            (retired,),
        )
        if existing and int(existing[0]) != canonical:
            raise RefusedError(
                f"team_redirects already maps {retired} to {existing[0]}, manifest "
                f"says {canonical}"
            )
        if not existing:
            beforeimages.record(
                str(entry["entry_id"]), "redirect", "team_redirects",
                {"retired_team_id": retired}, None, str(entry["reason"]),
            )
            db.execute(
                "INSERT INTO team_redirects (retired_team_id, canonical_team_id) "
                "VALUES (?, ?)",
                (retired, canonical),
            )
        counts["redirects"] += 1

    for op in manifest["team_mapping_operations"]:
        db.execute(
            "CREATE TABLE IF NOT EXISTS team_mappings (team_name TEXT PRIMARY KEY, "
            "team_id INTEGER, confidence REAL, source TEXT)"
        )
        name = op["team_name"]
        actual = _row_dict(db.conn, "team_mappings", "team_name = ?", (name,))
        _assert_beforeimage(actual, op.get("expected_beforeimage"), "team mapping op")
        if op["op"] == "retire":
            if actual:
                beforeimages.record(str(op["entry_id"]), "team_mapping-retire",
                                    "team_mappings", {"team_name": name}, actual,
                                    str(op["reason"]))
                db.execute("DELETE FROM team_mappings WHERE team_name = ?", (name,))
        else:
            target = int(op["team_id"])
            if not db.fetchone("SELECT 1 FROM teams WHERE id = ?", (target,)):
                raise RefusedError(f"team mapping replace target {target} missing")
            if actual:
                beforeimages.record(str(op["entry_id"]), "team_mapping-replace",
                                    "team_mappings", {"team_name": name}, actual,
                                    str(op["reason"]))
                db.execute("UPDATE team_mappings SET team_id = ? WHERE team_name = ?",
                           (target, name))
        counts["team_mapping_ops"] += 1

    for op in manifest["fixture_operations"]:
        kind = op["kind"]
        if kind in ("adopt", "insert"):
            event_id = str(op["external_event_id"])
            detail = main_events.get(event_id)
            if detail is None:
                raise RefusedError(
                    f"fixture operation references uncaptured event {event_id!r}"
                )
            _validate_fixture_event(op, detail, roots, adopt=(kind == "adopt"))
            home = int(op["home_team_id"])
            away = int(op["away_team_id"])
            for team_id in (home, away):
                if not db.fetchone("SELECT 1 FROM teams WHERE id = ?", (team_id,)):
                    raise RefusedError(
                        f"fixture operation {event_id!r} references missing team {team_id}"
                    )
            if kind == "adopt":
                match_id = int(op["match_id"])
                actual = _row_dict(db.conn, "matches", "id = ?", (match_id,))
                _assert_beforeimage(actual, op["expected_beforeimage"], "fixture adopt")
                # A FINISHED legacy row whose score the capture contradicts, or a
                # changed home/away/date, needs structured capture-pinned evidence,
                # never a silent (or free-text) overwrite.
                corrected = bool(actual) and (
                    str(actual.get("status")).upper() == "FINISHED"
                    and (actual.get("home_score"), actual.get("away_score"))
                    != (detail["home_score"], detail["away_score"])
                    or (actual.get("home_team_id"), actual.get("away_team_id"))
                    != (home, away)
                    or actual.get("date") != op["date"]
                )
                if corrected:
                    _require_correction_evidence(op, detail, actual, event_id)
                beforeimages.record(str(op["entry_id"]), "fixture-adopt", "matches",
                                    {"id": match_id}, actual, str(op["reason"]))
                db.execute(
                    "UPDATE matches SET home_team_id = ?, away_team_id = ?, stage = ?, "
                    "\"round\" = ?, \"date\" = ?, home_score = ?, away_score = ?, "
                    "status = ?, competition_id = ? WHERE id = ?",
                    (home, away, op["stage"], op.get("round"), op["date"],
                     detail["home_score"], detail["away_score"], op["status"],
                     normalize_competition(op["competition_id"]), match_id),
                )
            else:
                match_id = provider_match_id(PROVIDER_ESPN, event_id)
                if db.fetchone("SELECT 1 FROM matches WHERE id = ?", (match_id,)):
                    raise RefusedError(f"fixture id collision at {match_id} for {event_id!r}")
                db.execute(
                    "INSERT INTO matches (id, home_team_id, away_team_id, home_score, "
                    "away_score, matchday, \"date\", status, stage, \"round\", "
                    "group_name, competition_id) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, "
                    "NULL, ?)",
                    (match_id, home, away, detail["home_score"], detail["away_score"],
                     op["date"], op["status"], op["stage"], op.get("round"),
                     normalize_competition(op["competition_id"])),
                )
            existing_map = db.fetchone(
                "SELECT match_id FROM provider_match_mappings "
                "WHERE provider = ? AND external_id = ?", (PROVIDER_ESPN, event_id),
            )
            if existing_map and int(existing_map[0]) != match_id:
                raise RefusedError(f"event {event_id!r} already maps to {existing_map[0]}")
            clash = db.fetchone(
                "SELECT external_id FROM provider_match_mappings "
                "WHERE provider = ? AND match_id = ?", (PROVIDER_ESPN, match_id),
            )
            if clash and str(clash[0]) != event_id:
                raise RefusedError(f"match {match_id} already claimed by event {clash[0]!r}")
            db.execute(
                "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
                "VALUES (?, ?, ?) ON CONFLICT (provider, external_id) "
                "DO UPDATE SET match_id = excluded.match_id",
                (PROVIDER_ESPN, event_id, match_id),
            )
        elif kind == "qualifier":
            match_id = int(op["match_id"])
            actual = _row_dict(db.conn, "matches", "id = ?", (match_id,))
            _assert_beforeimage(actual, op["expected_beforeimage"], "fixture qualifier")
            attribution = _validate_qualifier_evidence(
                op, actual, qualifying_events, root_map
            )
            beforeimages.record(str(op["entry_id"]), "fixture-qualifier", "matches",
                                {"id": match_id}, actual, str(op["reason"]))
            # A NULL-competition qualifying row is attributed to the pinned
            # event's competition; the row is preserved, never deleted.
            db.execute(
                'UPDATE matches SET stage = ?, "round" = ?, competition_id = ? '
                "WHERE id = ?",
                ("QUALIFYING", op["qualifying"]["round"], attribution, match_id),
            )
        else:  # retire-duplicate / quarantine
            match_id = int(op["match_id"])
            referenced = db.fetchone(
                "SELECT external_id FROM provider_match_mappings WHERE match_id = ?",
                (match_id,),
            )
            if referenced:
                raise RefusedError(
                    f"match {match_id} is referenced by provider event "
                    f"{referenced[0]!r}; rewire it before retiring"
                )
            actual = _row_dict(db.conn, "matches", "id = ?", (match_id,))
            _assert_beforeimage(actual, op["expected_beforeimage"], f"fixture {kind}")
            beforeimages.record(str(op["entry_id"]), f"fixture-{kind}", "matches",
                                {"id": match_id}, actual, str(op["reason"]))
            db.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        counts["fixture_ops"] += 1

    for decision in manifest["standings_decisions"]:
        competition_id = normalize_competition(decision["competition_id"])
        if decision["action"] == "delete":
            winner = int(decision["keep_team_id"])
            loser = int(decision["team_id"])
        else:
            winner = int(decision["team_id"])
            loser = int(decision["loser_team_id"])
        actual = _row_dict(
            db.conn, "standings",
            "competition_id = ? AND team_id = ?", (competition_id, loser),
        )
        _assert_beforeimage(actual, decision["expected_beforeimage"], "standings decision")
        if actual is None:
            raise RefusedError(
                f"standings decision loser {loser} has no row in {competition_id}"
            )
        if not db.fetchone(
            "SELECT 1 FROM standings WHERE competition_id = ? AND team_id = ?",
            (competition_id, winner),
        ):
            raise RefusedError(
                f"standings decision winner {winner} has no row in {competition_id}"
            )
        beforeimages.record(str(decision["entry_id"]), "standings-keep", "standings",
                            {"competition_id": competition_id, "team_id": loser},
                            actual, str(decision["reason"]))
        db.execute(
            "DELETE FROM standings WHERE competition_id = ? AND team_id = ?",
            (competition_id, loser),
        )
        counts["standings_decisions"] += 1

    for rewire in manifest["reference_rewiring"]:
        table = rewire["table"]
        column = rewire["column"]
        old = int(rewire["old_team_id"])
        new = int(rewire["new_team_id"])
        if not _table_exists(db.conn, table):
            raise RefusedError(f"reference rewiring table {table!r} does not exist")
        if column not in _table_columns(db.conn, table):
            raise RefusedError(f"reference rewiring column {table}.{column} does not exist")
        if column not in _TEAM_REFERENCE_COLUMNS.get(table, frozenset()):
            raise RefusedError(f"reference rewiring column {table}.{column} not allowed")
        rows = db.fetchall(f'SELECT * FROM "{table}" WHERE "{column}" = ?', (old,))
        cols = _table_columns(db.conn, table)
        for row in rows:
            beforeimages.record(
                str(rewire["entry_id"]), "reference-rewire", table,
                {"column": column, "old_team_id": old},
                {c: _normalize_value(v) for c, v in zip(cols, row)},
                str(rewire["reason"]),
            )
        db.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (new, old))
        counts["reference_rewiring"] += 1

    return counts


def _norm_round(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _reviewed_root_resolution(manifest: Dict[str, Any]) -> Dict[Any, int]:
    """Reviewed ``provider id -> root`` and ``member team id -> root`` mapping."""
    resolution: Dict[Any, int] = {}
    for root in manifest["club_roots"]:
        canonical = int(root["canonical_team_id"])
        resolution[("p", str(root["external_id"]))] = canonical
        for member in root["member_team_ids"]:
            resolution[("t", int(member))] = canonical
    return resolution


def _validate_qualifier_evidence(op, actual, qualifying_events,
                                 root_map: Optional[Dict[Any, int]] = None) -> str:
    """Prove a qualifier claim from an independently hashed qualifying event.

    The named pinned body must actually contain a qualifying-stage event with the
    same club pair, date, score and round as the claim; the legacy row must match
    that evidenced event. A free-text claim with no proof refuses.
    """
    qual = op["qualifying"]
    event = qualifying_events.get(str(qual["event_id"]))
    if event is None:
        raise RefusedError(
            "qualifier claim has no independently hashed qualifying event in the "
            "pinned captures"
        )
    if event["url"] != qual["url"] or event["sha256"] != qual["sha256"]:
        raise RefusedError("qualifier evidence is not pinned by that exact body hash")
    if int(qual["season_year"]) != event["season_year"]:
        raise RefusedError(
            f"qualifier evidence season {qual['season_year']!r} != pinned "
            f"{event['season_year']}"
        )
    if event["competition"] not in SCOPED_COMPETITIONS:
        raise RefusedError("qualifier evidence competition is not an EL/UCL capture")
    if actual is not None:
        row_comp = actual.get("competition_id")
        row_comp = normalize_competition(row_comp) if row_comp else None
        # NULL is attributable from the pinned event; a non-NULL foreign
        # competition is never overwritten.
        if row_comp is not None and row_comp != event["competition"]:
            raise RefusedError(
                f"qualifier evidence competition {event['competition']!r} contradicts a "
                f"foreign row competition {row_comp!r}"
            )
    if _norm_round(event.get("slug")) != _norm_round(qual["round"]):
        raise RefusedError(
            f"qualifier evidence round {qual['round']!r} does not match pinned stage "
            f"{event.get('slug')!r}"
        )
    if (event.get("home_external"), event.get("away_external")) != (
        str(qual["home_external"]), str(qual["away_external"])
    ):
        raise RefusedError("qualifier evidence participant mismatch")
    if str(event.get("date") or "") != str(qual["date"] or ""):
        raise RefusedError("qualifier evidence date mismatch")
    if (event.get("home_score"), event.get("away_score")) != (
        qual["home_score"], qual["away_score"]
    ):
        raise RefusedError("qualifier evidence score mismatch")
    if actual is None:
        raise RefusedError("qualifier target row is missing")
    if (str(actual.get("date") or "") != str(qual["date"] or "")
            or actual.get("home_score") != qual["home_score"]
            or actual.get("away_score") != qual["away_score"]):
        raise RefusedError(
            "legacy qualifier row does not match its evidenced event date/score"
        )
    # The actual legacy participants must resolve, through the reviewed roots, to
    # the SAME clubs as the pinned event, preserving home/away orientation. A row
    # with the same date/score but different clubs can never be hidden QUALIFYING.
    if root_map is None:
        raise RefusedError(
            "qualifier evidence requires a reviewed provider/member root resolution"
        )
    expected_home = root_map.get(("p", str(event["home_external"])))
    expected_away = root_map.get(("p", str(event["away_external"])))
    actual_home = root_map.get(("t", int(actual.get("home_team_id"))))
    actual_away = root_map.get(("t", int(actual.get("away_team_id"))))
    if None in (expected_home, expected_away, actual_home, actual_away):
        raise RefusedError(
            "qualifier evidence participant has no reviewed root resolution"
        )
    if (actual_home, actual_away) != (expected_home, expected_away):
        raise RefusedError(
            "legacy qualifier row resolves to different clubs than the pinned event"
        )
    return event["competition"]


def _write_standings_from_capture(db, competition_id: str, season_year: int,
                                  dataset: Dict[str, Any],
                                  beforeimages: _BeforeImages) -> int:
    standings = (dataset.get("standings") or {}).get("teams") or []
    if not standings:
        raise RefusedError(f"pinned {competition_id} {season_year} standings empty")
    team_ids = {t["externalTeamId"]: _resolve_provider_root(db, t["externalTeamId"])
                for t in standings}
    for row in db.fetchall(
        "SELECT * FROM standings WHERE competition_id = ?", (competition_id,)
    ):
        cols = _table_columns(db.conn, "standings")
        beforeimages.record(
            f"espn-standings-rebuild-{competition_id}", "standings-rebuild", "standings",
            {"competition_id": competition_id},
            {c: _normalize_value(v) for c, v in zip(cols, row)},
            "capture-derived standings rebuild",
        )
    db.execute("DELETE FROM standings WHERE competition_id = ?", (competition_id,))
    now = datetime.utcnow().isoformat()
    for team in standings:
        db.execute(
            "INSERT INTO standings (competition_id, team_id, position, played, won, "
            "drawn, lost, goals_for, goals_against, goal_difference, points, "
            "last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (competition_id, team_ids[team["externalTeamId"]], team.get("position"),
             team.get("played", 0), team.get("won", 0), team.get("draw", 0),
             team.get("lost", 0), team.get("goalsFor", 0), team.get("goalsAgainst", 0),
             team.get("goalDifference", 0), team.get("points", 0), now),
        )
    return len(standings)


def _derive_candidate(db, manifest, captures_dir: Path,
                      entries: Sequence[Dict[str, Any]],
                      beforeimages: _BeforeImages) -> Dict[str, Any]:
    client = PinnedESPNClient(entries, captures_dir)
    standings_written: Dict[str, int] = {}
    for competition_id in SCOPED_COMPETITIONS:
        dataset = client.fetch_season(competition_id, PINNED_CURRENT_YEAR)
        ESPNDataService(db, client=client)._validate_dataset(
            dataset, list(dataset.get("diagnostics") or [])
        )
        standings_written[competition_id] = _write_standings_from_capture(
            db, competition_id, PINNED_CURRENT_YEAR, dataset, beforeimages
        )
        db.commit()

    # Clear stale derived rows before rebuilding from the retained corpus so no
    # retired/orphan rating or coefficient can survive.
    for competition_id in SCOPED_COMPETITIONS:
        for row in db.fetchall(
            "SELECT * FROM solkoff_coefficients WHERE competition_id = ?",
            (competition_id,),
        ):
            cols = _table_columns(db.conn, "solkoff_coefficients")
            beforeimages.record(
                f"solkoff-clear-{competition_id}", "solkoff-clear",
                "solkoff_coefficients", {"competition_id": competition_id},
                {c: _normalize_value(v) for c, v in zip(cols, row)},
                "stale derived coefficient cleared before rebuild",
            )
        db.execute("DELETE FROM solkoff_coefficients WHERE competition_id = ?",
                   (competition_id,))
    for row in db.fetchall("SELECT * FROM elo_ratings"):
        cols = _table_columns(db.conn, "elo_ratings")
        beforeimages.record("elo-clear", "elo-clear", "elo_ratings", {},
                            {c: _normalize_value(v) for c, v in zip(cols, row)},
                            "stale derived rating cleared before rebuild")
    db.execute("DELETE FROM elo_ratings")
    db.commit()

    solkoff: Dict[str, int] = {}
    for competition_id in SCOPED_COMPETITIONS:
        SolkoffCalculator(db, competition_id=competition_id).calculate_all()
        solkoff[competition_id] = int(db.fetchone(
            "SELECT COUNT(*) FROM solkoff_coefficients WHERE competition_id = ?",
            (competition_id,),
        )[0])
    EloCalculator(db).calculate_all()
    elo_teams = int(db.fetchone("SELECT COUNT(*) FROM elo_ratings")[0])
    return {"standings_written": standings_written, "solkoff_rows": solkoff,
            "elo_teams": elo_teams}


def _apply_all(db, manifest: Dict[str, Any], captures_dir: Path,
               entries: Sequence[Dict[str, Any]],
               beforeimages: _BeforeImages) -> Dict[str, Any]:
    with db.transaction():
        counts = _apply_manifest(db, manifest, captures_dir, entries, beforeimages)
    counts["derived"] = _derive_candidate(db, manifest, captures_dir, entries,
                                          beforeimages)
    return counts


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------

def _receipt_base(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tool_revision": TOOL_REVISION,
        "manifest_version": MANIFEST_VERSION,
        "manifest_sha256": manifest_sha256(manifest),
        "source_sha256": manifest["source_sha256"],
        "source_file_sha256": manifest["source_sha256"],
        "captures": [
            {"competition": e["competition"], "season_year": e["season_year"],
             "url": e["url"], "sha256": e["sha256"]}
            for e in manifest["captures"]
        ],
    }


def _reserve_candidate(source_path: Path, output: Path) -> int:
    """Reserve candidate + check sidecar conflicts before creating anything."""
    receipt_path = Path(str(output) + ".receipt.json")
    before_path = Path(str(output) + ".beforeimages.jsonl")
    for path, label in ((receipt_path, "receipt"), (before_path, "beforeimages")):
        _reject_private_artifact(path, label)
    with open(str(source_path), "rb") as src:
        fd = _reserve_new_file(output)
        try:
            with os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException as exc:
            # Only ever remove the partial file this call just created; the
            # source and any sibling artifacts are untouched.
            try:
                os.unlink(str(output))
            except OSError:
                pass
            raise RefusedError(f"could not materialize the candidate copy: {exc}")
    os.chmod(str(output), 0o600)
    return 0


def _write_rejected_receipt(output: Path, receipt: Dict[str, Any],
                            exc: BaseException) -> None:
    """Record a rejected receipt after the candidate file already exists."""
    receipt["state"] = "rejected"
    receipt["error"] = f"{type(exc).__name__}: {exc}"
    receipt["operation_counts"] = {}
    _atomic_write_exclusive(str(output) + ".receipt.json",
                            _canonical_json(receipt).encode())


def build_candidate(source_db: str, manifest_path: str, output_db: str) -> Dict[str, Any]:
    """Build a verified candidate database from a reviewed manifest.

    Refuses (raises) on any path/manifest/precondition violation. A failure inside
    a build/derived phase returns a ``rejected`` receipt; the source is never
    written to and the output is never publishable.
    """
    source = _source_safety(source_db)
    output = _new_output_safety(output_db, "candidate output")
    manifest, captures_dir, entries = _load_bundle(manifest_path)

    source_sha = _file_sha256(source)
    if source_sha != manifest["source_sha256"]:
        raise RefusedError(
            "manifest source_sha256 does not match the source snapshot (stale hash)"
        )
    if os.path.realpath(manifest["source_path"]) != os.path.realpath(str(source)):
        raise RefusedError("manifest source_path does not match the --source argument")

    inv = inventory(str(source), str(captures_dir))
    validate_manifest(manifest, inv)

    workdir = Path(tempfile.mkdtemp(prefix="espn-reconcile-build-"))
    os.chmod(str(workdir), 0o700)
    scratch = workdir / "migrated.db"
    beforeimages: Optional[_BeforeImages] = None
    receipt: Optional[Dict[str, Any]] = None
    try:
        shutil.copy2(str(source), str(scratch))
        os.chmod(str(scratch), 0o600)
        if _file_sha256(source) != source_sha:
            raise RefusedError("source changed during copy; aborting")

        default = inv["legacy_competition_default"]
        previous = os.environ.get("COMPETITION_ID")
        if default is not None:
            os.environ["COMPETITION_ID"] = default
        try:
            migrated = Database(str(scratch))
        finally:
            if previous is None:
                os.environ.pop("COMPETITION_ID", None)
            else:
                os.environ["COMPETITION_ID"] = previous
        try:
            got = _logical_hashes(migrated.conn, _FINGERPRINT_TABLES)
            if default is not None:
                _assert_legacy_attribution(migrated.conn, default)
        finally:
            migrated.close()
        if got != manifest["source_logical_hashes"]:
            raise RefusedError(
                "migrated-source logical fingerprints do not match the manifest "
                "(manifest is stale relative to the snapshot)"
            )

        _reserve_candidate(scratch, output)
        receipt = _receipt_base(manifest)
        receipt["output"] = str(output)
        try:
            beforeimages = _BeforeImages(str(output) + ".beforeimages.jsonl")
        except Exception as exc:  # artifact creation failure -> safe refusal
            raise RefusedError(f"could not create the beforeimages artifact: {exc}")
        receipt["beforeimages_path"] = str(beforeimages.path)

        # Once the candidate file exists, every later failure must still leave a
        # rejected receipt (unless receipt creation itself fails, a safe refusal).
        try:
            db = Database(str(output))
        except Exception as exc:  # noqa: BLE001
            _write_rejected_receipt(output, receipt, exc)
            return receipt
        try:
            try:
                counts = _apply_all(db, manifest, captures_dir, entries, beforeimages)
            except Exception as exc:  # noqa: BLE001 - never leave a bare error
                _write_rejected_receipt(output, receipt, exc)
                return receipt
            db.close()
            try:
                verification = verify_candidate(str(output), manifest_path)
            except Exception as exc:  # noqa: BLE001
                _write_rejected_receipt(output, receipt, exc)
                return receipt
            receipt["operation_counts"] = counts
            receipt["beforeimages"] = beforeimages.count
            receipt["verification"] = verification
            receipt["state"] = "verified" if verification["verified"] else "rejected"
            receipt["candidate_logical_fingerprint"] = _candidate_fingerprint(str(output))
        finally:
            db.close()
        _atomic_write_exclusive(str(output) + ".receipt.json",
                                _canonical_json(receipt).encode())
        return receipt
    finally:
        if beforeimages is not None:
            beforeimages.close()
        shutil.rmtree(workdir, ignore_errors=True)


def _assert_legacy_attribution(conn, default: str) -> None:
    """Every migrated legacy snapshot row carries the recorded competition."""
    for table in ("standings", "solkoff_coefficients"):
        if not _table_exists(conn, table):
            continue
        bad = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE competition_id IS DISTINCT FROM ?',
            (default,),
        ).fetchone()[0]
        if bad:
            raise RefusedError(
                f"migrated {table} has {bad} row(s) not attributed to {default!r}"
            )


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def _candidate_fingerprint(candidate_db: str) -> str:
    conn = duckdb.connect(str(candidate_db), read_only=True)
    try:
        hashes = _logical_hashes(conn, _FINGERPRINT_TABLES)
    finally:
        conn.close()
    return hashlib.sha256(_canonical_json(hashes).encode()).hexdigest()


def _all_hashes(candidate_db: str) -> Dict[str, str]:
    conn = duckdb.connect(str(candidate_db), read_only=True)
    try:
        return _logical_hashes(conn, _FINGERPRINT_TABLES)
    finally:
        conn.close()


def _team_update_checks(conn, manifest: Dict[str, Any]) -> List[str]:
    """Only declared fields changed; every protected column kept its beforevalue."""
    problems: List[str] = []
    for update in manifest.get("team_updates") or []:
        target = int(update["canonical_team_id"])
        row = _row_dict(conn, "teams", "id = ?", (target,))
        if row is None:
            problems.append(f"team update {target}: target row missing")
            continue
        fields = update["fields"]
        before = update.get("expected_beforeimage")
        if isinstance(before, dict):
            for column, value in before.items():
                if column in fields:
                    continue
                if row.get(column) != value:
                    problems.append(
                        f"team update {target}: protected column {column!r} changed"
                    )
        for field, value in fields.items():
            if row.get(field) != value:
                problems.append(
                    f"team update {target}: {field} is {row.get(field)!r}, "
                    f"manifest says {value!r}"
                )
    return problems


def _new_root_metadata_checks(conn, manifest: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for root in manifest["club_roots"]:
        if root["decision"] != "create-confirmed-new":
            continue
        target = int(root["canonical_team_id"])
        row = _row_dict(conn, "teams", "id = ?", (target,))
        if row is None:
            problems.append(f"new root {target}: team row missing")
            continue
        if row.get("name") != root.get("name"):
            problems.append(f"new root {target}: name does not match the review")
        for field in ("code", "crest"):
            if field in root and row.get(field) != root[field]:
                problems.append(
                    f"new root {target}: {field} does not match the reviewed record"
                )
    return problems


def _unrelated_team_rows_preserved(candidate_db: str,
                                   manifest: Dict[str, Any]) -> List[str]:
    """Teams outside declared updates/new roots keep their exact source row."""
    source = Path(manifest["source_path"])
    allowed = {
        int(update["canonical_team_id"])
        for update in manifest.get("team_updates") or []
    }
    allowed |= {
        int(root["canonical_team_id"])
        for root in manifest["club_roots"]
        if root["decision"] == "create-confirmed-new"
    }
    src_conn = duckdb.connect(str(source), read_only=True)
    cand_conn = duckdb.connect(str(candidate_db), read_only=True)
    try:
        if _table_columns(src_conn, "teams") != _table_columns(cand_conn, "teams"):
            return ["teams schema differs from the source snapshot"]
        src_rows = {
            int(row[0]): tuple(_normalize_value(v) for v in row)
            for row in src_conn.execute("SELECT * FROM teams").fetchall()
        }
        cand_rows = {
            int(row[0]): tuple(_normalize_value(v) for v in row)
            for row in cand_conn.execute("SELECT * FROM teams").fetchall()
        }
    finally:
        src_conn.close()
        cand_conn.close()
    problems = []
    for team_id, row in src_rows.items():
        if team_id in allowed:
            continue
        if cand_rows.get(team_id) != row:
            problems.append(
                f"team {team_id} changed outside any declared team update"
            )
    return problems


def _check(checks: Dict[str, Any], name: str, ok: bool, detail: Any = None) -> bool:
    checks[name] = {"ok": bool(ok), "detail": detail}
    return bool(ok)


def verify_candidate(candidate_db: str, manifest_path: str) -> Dict[str, Any]:
    """Independently verify a candidate against its manifest, offline.

    Rebuilds the expected candidate from the pinned source + manifest on a
    throwaway copy and compares logical fingerprints; also re-runs structural
    checks. Verifies read-only and never mutates the candidate or source.
    """
    manifest, captures_dir, entries = _load_bundle(manifest_path)

    source = Path(manifest["source_path"])
    if not source.is_file():
        raise RefusedError(f"pinned source {str(source)!r} is missing; cannot verify")
    _source_safety(source)
    if _file_sha256(source) != manifest["source_sha256"]:
        raise RefusedError("pinned source hash no longer matches the manifest")

    _reject_production(candidate_db, "candidate")
    _reject_alias(candidate_db, "candidate")
    if not Path(str(candidate_db)).is_file():
        raise RefusedError(f"candidate {candidate_db!r} does not exist")

    # Throwaway independent rebuild from source + manifest.
    inv = inventory(str(source), str(captures_dir))
    validate_manifest(manifest, inv)
    workdir = Path(tempfile.mkdtemp(prefix="espn-reconcile-verify-"))
    os.chmod(str(workdir), 0o700)
    expected_path = workdir / "expected.db"
    expected_beforeimages = _BeforeImages(str(workdir / "expected.jsonl"))
    try:
        shutil.copy2(str(source), str(expected_path))
        os.chmod(str(expected_path), 0o600)
        default = inv["legacy_competition_default"]
        previous = os.environ.get("COMPETITION_ID")
        if default is not None:
            os.environ["COMPETITION_ID"] = default
        try:
            migrated = Database(str(expected_path))
        finally:
            if previous is None:
                os.environ.pop("COMPETITION_ID", None)
            else:
                os.environ["COMPETITION_ID"] = previous
        migrated.close()
        expected_db = Database(str(expected_path))
        try:
            _apply_all(expected_db, manifest, captures_dir, entries,
                       expected_beforeimages)
        finally:
            expected_db.close()
        expected_hashes = _all_hashes(str(expected_path))
    finally:
        expected_beforeimages.close()
        shutil.rmtree(workdir, ignore_errors=True)

    candidate_hashes = _all_hashes(str(candidate_db))
    checks: Dict[str, Any] = {}
    failures: List[str] = []
    if not _check(checks, "independent_rebuild_matches",
                  candidate_hashes == expected_hashes,
                  sorted(set(candidate_hashes.items()) ^ set(expected_hashes.items()))):
        failures.append("candidate does not match an independent source+manifest rebuild")

    conn = duckdb.connect(str(candidate_db), read_only=True)
    try:
        dangling = conn.execute(
            "SELECT COUNT(*) FROM matches m WHERE "
            "NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = m.home_team_id) OR "
            "NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = m.away_team_id)"
        ).fetchone()[0]
        if not _check(checks, "no_dangling_match_teams", dangling == 0, dangling):
            failures.append("dangling match team references")
        for table, column in (("provider_team_mappings", "team_id"),
                              ("provider_match_mappings", "match_id")):
            if not _table_exists(conn, table):
                continue
            ref = "teams" if column == "team_id" else "matches"
            bad = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" x WHERE NOT EXISTS '
                f'(SELECT 1 FROM "{ref}" r WHERE r.id = x."{column}")'
            ).fetchone()[0]
            if not _check(checks, f"no_dangling_{table}", bad == 0, bad):
                failures.append(f"dangling {table}")
        selfplay = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE home_team_id = away_team_id"
        ).fetchone()[0]
        if not _check(checks, "no_self_play", selfplay == 0, selfplay):
            failures.append("self-play fixtures present")
        try:
            resolver = TeamRedirectResolver(conn)
            retired_ids = set(resolver.retired_ids())
            _check(checks, "redirects_valid", True, len(retired_ids))
        except TeamIdentityError as exc:
            retired_ids = set()
            _check(checks, "redirects_valid", False, str(exc))
            failures.append("invalid team_redirects")
        if retired_ids:
            ph = ",".join("?" * len(retired_ids))
            args = tuple(retired_ids)
            refs = conn.execute(
                f"SELECT COUNT(*) FROM matches WHERE home_team_id IN ({ph}) "
                f"OR away_team_id IN ({ph})", args + args,
            ).fetchone()[0]
            refs += conn.execute(
                f"SELECT COUNT(*) FROM standings WHERE team_id IN ({ph})", args
            ).fetchone()[0]
            refs += conn.execute(
                f"SELECT COUNT(*) FROM provider_team_mappings WHERE team_id IN ({ph})",
                args,
            ).fetchone()[0]
            if not _check(checks, "retired_ids_not_referenced", refs == 0, refs):
                failures.append("retired ids still referenced")
        duplicates = conn.execute(
            "SELECT COUNT(*) FROM (SELECT provider, match_id FROM "
            "provider_match_mappings GROUP BY provider, match_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if not _check(checks, "no_duplicate_event_targets", duplicates == 0, duplicates):
            failures.append("duplicate provider event targets")
        unmanaged = _count_unmanaged(conn)
        if not _check(checks, "no_unmanaged_scoped_rows", unmanaged == 0, unmanaged):
            failures.append(f"{unmanaged} unmanaged scoped row(s) remain")
        rogue = _rogue_event_mappings(conn, manifest, entries, captures_dir)
        if not _check(checks, "no_rogue_event_mappings", not rogue, rogue[:20]):
            failures.append("rogue event mapping(s) on a scoped main-stage row")
        update_problems = _team_update_checks(conn, manifest)
        if not _check(checks, "team_updates_declared_fields_only",
                      not update_problems, update_problems[:20]):
            failures.append("a team update changed a protected/undeclared field")
        root_problems = _new_root_metadata_checks(conn, manifest)
        if not _check(checks, "new_root_metadata_matches_review",
                      not root_problems, root_problems[:20]):
            failures.append("new-root metadata does not match the reviewed record")
        unrelated = _unrelated_team_rows_preserved(candidate_db, manifest)
        if not _check(checks, "unrelated_team_rows_preserved",
                      not unrelated, unrelated[:20]):
            failures.append("a team row changed outside any declared update")
        fp1 = _candidate_fingerprint(candidate_db)
        fp2 = _candidate_fingerprint(candidate_db)
        _check(checks, "deterministic_fingerprint", fp1 == fp2, fp1)
    finally:
        conn.close()

    return {
        **_receipt_base(manifest),
        "candidate": str(candidate_db),
        "candidate_logical_fingerprint": _candidate_fingerprint(candidate_db),
        "checks": checks,
        "failures": failures,
        "verified": not failures,
        "state": "verified" if not failures else "rejected",
    }


def _count_unmanaged(conn) -> int:
    if not _table_exists(conn, "provider_match_mappings"):
        return 0
    return int(conn.execute(
        f"SELECT COUNT(*) FROM matches m WHERE {_SCOPED_WHERE}",
        (*SCOPED_COMPETITIONS, _SCOPED_FROM, PROVIDER_ESPN),
    ).fetchone()[0])


def _rogue_event_mappings(conn, manifest, entries, captures_dir) -> List[str]:
    """ESPN mappings on a scoped EL/UCL main-stage row must be a real captured
    event pointing at the correct row.

    Preserved (``QUALIFYING``) and pre-2024 rows are outside coverage and exempt;
    any other ESPN mapping whose event is not a captured main event, or whose row
    participants disagree with the reviewed roots, is rejected.
    """
    if not _table_exists(conn, "provider_match_mappings"):
        return []
    try:
        captured_main, _qual = _parse_events(entries, captures_dir)
    except RefusedError as exc:
        return [f"captures no longer parse: {exc}"]
    root_map = _reviewed_root_resolution(manifest)
    rogue: List[str] = []
    for external_id, match_id in conn.execute(
        "SELECT external_id, match_id FROM provider_match_mappings WHERE provider = ?",
        (PROVIDER_ESPN,),
    ).fetchall():
        external_id = str(external_id)
        row = conn.execute(
            "SELECT competition_id, \"date\", stage, home_team_id, away_team_id "
            "FROM matches WHERE id = ?", (match_id,),
        ).fetchone()
        if row is None:
            rogue.append(f"{external_id}: points at missing match {match_id}")
            continue
        comp, date, stage, home, away = row
        # NULL-competition (unattributed) rows and unknown dates are IN scope:
        # a mapped unattributed row must be explicitly proved by a captured event,
        # never exempted merely because its own competition is NULL.
        in_window = (
            (comp in SCOPED_COMPETITIONS or comp is None)
            and (date is None or str(date) >= _SCOPED_FROM)
            and (stage is None or str(stage).upper() != "QUALIFYING")
        )
        if not in_window:
            continue
        detail = captured_main.get(external_id)
        if detail is None:
            rogue.append(f"{external_id}: not a captured main event")
            continue
        expected_home = root_map.get(("p", detail["home_external"]))
        expected_away = root_map.get(("p", detail["away_external"]))
        actual_home = root_map.get(("t", int(home)))
        actual_away = root_map.get(("t", int(away)))
        if None in (expected_home, expected_away, actual_home, actual_away):
            rogue.append(
                f"{external_id}: participant has no reviewed root resolution"
            )
            continue
        if (actual_home, actual_away) != (expected_home, expected_away):
            rogue.append(
                f"{external_id}: row resolves to ({actual_home},{actual_away}) != "
                f"expected ({expected_home},{expected_away})"
            )
    return rogue


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backend.espn_reconcile",
        description="Offline, fail-closed ESPN legacy reconciliation tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="inventory a source snapshot + captures")
    inv.add_argument("--source", required=True)
    inv.add_argument("--captures", required=True)
    inv.add_argument("--report-dir", required=True)
    build = sub.add_parser("build-candidate", help="build a candidate database")
    build.add_argument("--source", required=True)
    build.add_argument("--manifest", required=True)
    build.add_argument("--output-db", required=True)
    verify = sub.add_parser("verify", help="verify a candidate database")
    verify.add_argument("--candidate", required=True)
    verify.add_argument("--manifest", required=True)
    return parser


def _report_dir(path: os.PathLike) -> Path:
    _reject_production(path, "report-dir")
    _reject_alias(path, "report-dir")
    p = Path(str(path))
    if p.exists():
        if not p.is_dir():
            raise RefusedError(f"report-dir {path!r} exists and is not a directory")
        return p
    p.mkdir(mode=0o700, parents=False)
    os.chmod(str(p), 0o700)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            report = inventory(args.source, args.captures)
            report_dir = _report_dir(args.report_dir)
            _atomic_write_exclusive(report_dir / "inventory.json",
                                    _canonical_json(report).encode())
            print(_canonical_json(report))
            return 0
        if args.command == "build-candidate":
            receipt = build_candidate(args.source, args.manifest, args.output_db)
            print(_canonical_json(receipt))
            return 0 if receipt.get("state") == "verified" else 1
        receipt = verify_candidate(args.candidate, args.manifest)
        print(_canonical_json(receipt))
        return 0 if receipt.get("verified") else 1
    except ReconcileError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
