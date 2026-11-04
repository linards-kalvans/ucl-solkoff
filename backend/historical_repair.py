"""Offline, checksum-pinned repair tool for historical Champions League archives.

Dry-run by default; ``--apply`` performs a single transaction that replaces only
the approved CL season windows (2024/25 and 2025/26). The tool never touches the
network, the API client or the scheduler, and it never runs the application's
schema initialisation or the ``TeamMatcher`` commit path. It opens a native
DuckDB connection and scopes every write to the approved windows.

Identity rule: a source club name is reused only when it exactly matches one
existing team row. Anything else needs an explicit reviewed mapping; polluted
(kickoff-prefixed) rows are never rewritten, and a clean stable node is created
instead. Ambiguity is an error, never a fuzzy guess.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import duckdb

from backend.football_txt_parser import FootballTxtParser
from backend.fixture_ids import deterministic_match_id, effective_matchday
from backend.team_matcher import normalize_team_name, strip_kickoff
from backend.team_identity import TeamRedirectResolver

logger = logging.getLogger(__name__)

APPROVED_YEARS: Tuple[int, ...] = (2024, 2025)
COMPETITION_ID = "CL"

EXPECTED_TOTAL = 189
EXPECTED_LEAGUE = 144
EXPECTED_PLAYOFF = 16
EXPECTED_R16 = 16
EXPECTED_QF = 8
EXPECTED_SF = 4
EXPECTED_FINAL = 1
EXPECTED_CLUBS = 36
EXPECTED_GAMES_EACH = 8

PROTECTED_TABLES: Tuple[str, ...] = (
    "standings", "elo_ratings", "solkoff_coefficients", "api_cache", "team_mappings",
)

_LEAGUE_MATCHDAYS = set(range(1, 9))
_KNOCKOUT_ROUND_COUNTS = {
    "Round of 16": EXPECTED_R16,
    "Quarter-finals": EXPECTED_QF,
    "Semi-finals": EXPECTED_SF,
    "Final": EXPECTED_FINAL,
}
# Calendar months each stage is played in (Aug-May season).
_STAGE_BUCKET_MONTHS = {
    "LEAGUE": {9, 10, 11, 12, 1},
    "PLAYOFF": {2},
    "R16": {3},
    "QF": {4},
    "SF": {4, 5},
    "FINAL": {5},
}


class RepairError(Exception):
    """Any validation, identity, collision or integrity failure."""


@dataclass
class SeasonSource:
    year: int
    path: str
    expected_sha256: str


@dataclass
class PreparedSeason:
    year: int
    label: str
    path: str
    expected_sha256: str
    actual_sha256: str
    records: List[Dict[str, Any]]
    team_names: List[str]


@dataclass
class StagedMatch:
    match_id: int
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    matchday: Optional[int]
    date: str
    status: str
    stage: Optional[str]
    round: Optional[str]
    group_name: Optional[str]
    competition_id: str
    year: int

    @property
    def identity(self) -> Tuple[int, int, str]:
        return (self.home_team_id, self.away_team_id, self.date)


@dataclass
class RepairReport:
    applied: bool = False
    sources: List[Dict[str, Any]] = field(default_factory=list)
    seasons: List[Dict[str, Any]] = field(default_factory=list)
    identity: Dict[str, int] = field(default_factory=dict)
    new_teams: List[Dict[str, Any]] = field(default_factory=list)
    staged_count: int = 0
    staged_by_year: Dict[str, int] = field(default_factory=dict)
    deletions: Dict[str, int] = field(default_factory=dict)
    inserted: int = 0
    current_matches: Dict[str, int] = field(default_factory=dict)
    other_competition_counts: Dict[str, int] = field(default_factory=dict)
    protected_before: Dict[str, Optional[str]] = field(default_factory=dict)
    protected_after: Dict[str, Optional[str]] = field(default_factory=dict)
    mapping_changes: List[Dict[str, Any]] = field(default_factory=list)
    source_tables: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied": self.applied,
            "sources": self.sources,
            "seasons": self.seasons,
            "identity": self.identity,
            "new_teams": self.new_teams,
            "staged_count": self.staged_count,
            "staged_by_year": self.staged_by_year,
            "deletions": self.deletions,
            "inserted": self.inserted,
            "current_matches": self.current_matches,
            "other_competition_counts": self.other_competition_counts,
            "protected_before": self.protected_before,
            "protected_after": self.protected_after,
            "mapping_changes": self.mapping_changes,
            "source_tables": self.source_tables,
        }


# ---------------------------------------------------------------------------
# hashing / ids
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def deterministic_team_id(name: str) -> int:
    """Stable negative team id for a canonical (clean) source name."""
    digest = hashlib.sha256(("team|" + name).encode("utf-8")).hexdigest()
    return -(int(digest[:12], 16) % 2_000_000_000 + 1)


# ---------------------------------------------------------------------------
# windows / loading / validation
# ---------------------------------------------------------------------------

def season_window(year: int) -> Tuple[str, str]:
    return f"{year}-08-01", f"{year + 1}-08-01"


def season_boundary(year: int) -> str:
    """Feb 1 date that separates league phase from play-offs."""
    return f"{year + 1}-02-01"


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def _stage_bucket(match: Dict[str, Any]) -> str:
    stage = match.get("stage")
    if stage == "LEAGUE_STAGE":
        return "LEAGUE"
    if stage == "KNOCKOUT_PLAYOFF":
        return "PLAYOFF"
    if stage == "KNOCKOUT":
        if match.get("round") == "Round of 16":
            return "R16"
        if match.get("round") == "Quarter-finals":
            return "QF"
        if match.get("round") == "Semi-finals":
            return "SF"
        if match.get("round") == "Final":
            return "FINAL"
    return "UNKNOWN"


def load_season(source: SeasonSource, parser: Optional[FootballTxtParser] = None) -> PreparedSeason:
    if source.year not in APPROVED_YEARS:
        raise RepairError(
            f"year {source.year} is not approved; approved years are {APPROVED_YEARS}"
        )
    path = Path(source.path)
    if not path.is_file():
        raise RepairError(f"source file not found: {source.path}")
    raw = path.read_bytes()
    actual = sha256_bytes(raw)
    expected = (source.expected_sha256 or "").strip().lower()
    if actual.lower() != expected:
        raise RepairError(
            f"source checksum mismatch for {source.path}: expected {expected}, got {actual}"
        )
    content = raw.decode("utf-8")
    label = season_label(source.year)
    parsed = (parser or FootballTxtParser()).parse(
        content, competition=COMPETITION_ID, season=label
    )
    records = parsed.get("matches", [])
    names = sorted({m["home_team"] for m in records} | {m["away_team"] for m in records})
    return PreparedSeason(
        year=source.year,
        label=label,
        path=str(path),
        expected_sha256=expected,
        actual_sha256=actual,
        records=records,
        team_names=names,
    )


def validate_season(ps: PreparedSeason) -> Dict[str, Any]:
    """Validate the complete-season invariants; raise ``RepairError`` otherwise."""
    records = ps.records
    if len(records) != EXPECTED_TOTAL:
        raise RepairError(
            f"{ps.label}: expected {EXPECTED_TOTAL} fixtures, parsed {len(records)}"
        )

    start, end = season_window(ps.year)
    boundary = season_boundary(ps.year)

    counts: Dict[str, int] = {"LEAGUE": 0, "PLAYOFF": 0, "R16": 0, "QF": 0, "SF": 0, "FINAL": 0}
    league_teams: set = set()
    non_league_names: set = set()
    league_games: Dict[str, int] = {}
    league_fixtures: set = set()
    matchday_counts: Dict[int, int] = {}
    fixtures_by_id: Dict[int, Tuple[int, int, str]] = {}

    for m in records:
        home, away = m.get("home_team"), m.get("away_team")
        if not home or not away:
            raise RepairError(f"{ps.label}: fixture with empty team name: {m}")
        for name in (home, away):
            if strip_kickoff(name) != name:
                raise RepairError(f"{ps.label}: kickoff prefix retained in team name {name!r}")
            if name.strip() != name:
                raise RepairError(f"{ps.label}: untrimmed team name {name!r}")
        if home == away:
            raise RepairError(f"{ps.label}: self-match {home!r}")
        if not isinstance(m.get("home_score"), int) or not isinstance(m.get("away_score"), int):
            raise RepairError(f"{ps.label}: non-integer score on {home} v {away}")

        match_date = m.get("date")
        if not match_date:
            raise RepairError(f"{ps.label}: missing date on {home} v {away}")
        try:
            parsed_date = _date.fromisoformat(match_date)
        except ValueError as exc:
            raise RepairError(f"{ps.label}: invalid date {match_date!r}: {exc}")
        if not (start <= match_date < end):
            raise RepairError(f"{ps.label}: date {match_date} outside season window")

        bucket = _stage_bucket(m)
        if bucket == "UNKNOWN":
            raise RepairError(
                f"{ps.label}: unrecognised stage/round {m.get('stage')}/{m.get('round')}"
            )
        if parsed_date.month not in _STAGE_BUCKET_MONTHS[bucket]:
            raise RepairError(
                f"{ps.label}: {bucket} fixture {home} v {away} dated {match_date}"
            )
        counts[bucket] += 1

        if bucket == "LEAGUE":
            if m.get("round") not in (None, ""):
                raise RepairError(f"{ps.label}: league fixture carries round {m.get('round')!r}")
            md = m.get("matchday")
            if md not in _LEAGUE_MATCHDAYS:
                raise RepairError(f"{ps.label}: league matchday {md!r} outside 1..8")
            matchday_counts[md] = matchday_counts.get(md, 0) + 1
            if match_date >= boundary:
                raise RepairError(f"{ps.label}: league fixture dated after boundary: {match_date}")
            league_fixtures.add((home, away))
            for name in (home, away):
                league_teams.add(name)
                league_games[name] = league_games.get(name, 0) + 1
        else:
            non_league_names.add(home)
            non_league_names.add(away)
            title = {
                "PLAYOFF": ("KNOCKOUT_PLAYOFF", "Knockout Play-off"),
                "R16": ("KNOCKOUT", "Round of 16"),
                "QF": ("KNOCKOUT", "Quarter-finals"),
                "SF": ("KNOCKOUT", "Semi-finals"),
                "FINAL": ("KNOCKOUT", "Final"),
            }[bucket]
            if (m.get("stage"), m.get("round")) != title:
                raise RepairError(
                    f"{ps.label}: unexpected stage/round for {bucket}: "
                    f"{m.get('stage')}/{m.get('round')}"
                )

        # Global fixture identity uniqueness within a season.
        key = (home, away, match_date, m.get("stage"), m.get("matchday"))
        if key in fixtures_by_id.values():
            raise RepairError(f"{ps.label}: duplicate fixture {key}")
        fixtures_by_id[len(fixtures_by_id)] = key

    expected_counts = {
        "LEAGUE": EXPECTED_LEAGUE, "PLAYOFF": EXPECTED_PLAYOFF, "R16": EXPECTED_R16,
        "QF": EXPECTED_QF, "SF": EXPECTED_SF, "FINAL": EXPECTED_FINAL,
    }
    if counts != expected_counts:
        raise RepairError(f"{ps.label}: stage totals {counts} != {expected_counts}")

    if len(league_teams) != EXPECTED_CLUBS:
        raise RepairError(
            f"{ps.label}: {len(league_teams)} league clubs, expected {EXPECTED_CLUBS}"
        )
    bad_games = {n: g for n, g in league_games.items() if g != EXPECTED_GAMES_EACH}
    if bad_games:
        raise RepairError(f"{ps.label}: clubs without exactly 8 league games: {bad_games}")
    if len(league_fixtures) != EXPECTED_LEAGUE:
        raise RepairError(
            f"{ps.label}: {len(league_fixtures)} unique league fixtures, expected {EXPECTED_LEAGUE}"
        )
    outside = sorted(non_league_names - league_teams)
    if outside:
        raise RepairError(
            f"{ps.label}: knockout/play-off club(s) not in the league phase: {outside}"
        )
    if matchday_counts != {md: 18 for md in _LEAGUE_MATCHDAYS}:
        raise RepairError(f"{ps.label}: league matchday distribution {matchday_counts}")

    return {
        "year": ps.year,
        "label": ps.label,
        "expected_sha256": ps.expected_sha256,
        "actual_sha256": ps.actual_sha256,
        "total": len(records),
        "stage_counts": counts,
        "league_clubs": len(league_teams),
        "league_matchdays": {str(k): v for k, v in sorted(matchday_counts.items())},
    }


# ---------------------------------------------------------------------------
# identity resolution
# ---------------------------------------------------------------------------

def resolve_identity(conn, prepared: Sequence[PreparedSeason],
                     explicit_mapping: Optional[Dict[str, int]] = None):
    """Return ``(plan, new_teams)`` mapping every source name to a team id.

    ``plan`` maps source name -> team id. ``new_teams`` maps the names that need
    a fresh clean node -> new deterministic id.

    Reviewed retired ids are consulted through the shared redirect resolver: a
    retired row is kept as provenance but is never a candidate or a referenced
    target; a source name that only a retired alias carries resolves to its
    unique active root (ambiguity raises), and an explicit mapping onto a retired
    id resolves to its active root. On a legacy schema without
    ``team_redirects`` this is a pure identity lookup.
    """
    explicit_mapping = dict(explicit_mapping or {})
    resolver = TeamRedirectResolver(conn)
    raw_existing = {
        tid: name for tid, name in conn.execute("SELECT id, name FROM teams").fetchall()
    }
    # Active rows only are identity candidates; retired rows resolve to a root.
    existing = {
        tid: name for tid, name in raw_existing.items()
        if resolver.canonical_id(tid) == tid
    }
    by_name: Dict[str, List[int]] = {}
    for tid, name in existing.items():
        by_name.setdefault(name, []).append(tid)

    # A source name carried only by retained retired rows must resolve to that
    # row's active root, not allocate a twin. Collected separately so a unique
    # active root is preferred before any new node is created.
    retired_roots_by_name: Dict[str, List[int]] = {}
    for tid, name in raw_existing.items():
        if tid in resolver.retired_ids():
            retired_roots_by_name.setdefault(name, []).append(
                resolver.canonical_id(tid)
            )

    for name, tid in explicit_mapping.items():
        root = resolver.canonical_id(tid)
        if root not in raw_existing:
            raise RepairError(f"explicit mapping {name!r} -> {tid} references unknown team")
        explicit_mapping[name] = root

    # Reserved ids include retired rows so a fresh deterministic id can never
    # collide with (and overwrite) a retained retired row.
    reserved = set(raw_existing)
    plan: Dict[str, int] = {}
    new_teams: Dict[str, int] = {}

    all_names = sorted({n for ps in prepared for n in ps.team_names})
    for name in all_names:
        if name in explicit_mapping:
            plan[name] = explicit_mapping[name]
            continue
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            plan[name] = candidates[0]
            continue
        if len(candidates) > 1:
            raise RepairError(
                f"ambiguous identity for {name!r}: existing ids {sorted(candidates)}; "
                "provide an explicit reviewed mapping"
            )
        # No active row carries the name. If retained retired aliases do, resolve
        # to their unique active root instead of allocating a duplicate club.
        retired_roots = sorted(set(retired_roots_by_name.get(name, [])))
        if len(retired_roots) == 1:
            plan[name] = retired_roots[0]
            continue
        if len(retired_roots) > 1:
            raise RepairError(
                f"ambiguous identity for {name!r}: retired aliases resolve to "
                f"{retired_roots}; provide an explicit reviewed mapping"
            )
        # No exact match: a clean node with the same casefolded name would be an
        # ambiguity (variant spelling), everything else means polluted-only.
        folded = strip_kickoff(name).casefold()
        clean_variant = [
            tid for tid, raw in existing.items()
            if raw == strip_kickoff(raw) and raw.casefold() == folded
        ]
        if clean_variant:
            raise RepairError(
                f"ambiguous identity for {name!r}: clean existing teams {sorted(clean_variant)}; "
                "provide an explicit reviewed mapping"
            )
        tid = deterministic_team_id(name)
        while tid in reserved:
            tid = deterministic_team_id(name + "|" + str(tid))
        reserved.add(tid)
        new_teams[name] = tid
        plan[name] = tid

    return plan, new_teams


# ---------------------------------------------------------------------------
# staging / collision detection
# ---------------------------------------------------------------------------

def stage_fixtures(prepared: Sequence[PreparedSeason],
                   plan: Dict[str, int]) -> List[StagedMatch]:
    staged: List[StagedMatch] = []
    seen: Dict[int, StagedMatch] = {}
    for ps in prepared:
        for m in ps.records:
            hid = plan[m["home_team"]]
            aid = plan[m["away_team"]]
            # Shared with the import path so a post-repair re-import owns the
            # exact same fixture id (no duplicates on mapping fix).
            matchday = effective_matchday(m.get("matchday"), m.get("stage"), m.get("round"))
            mid = deterministic_match_id(
                COMPETITION_ID, ps.label, hid, aid, m["date"], m.get("stage"), matchday
            )
            row = StagedMatch(
                match_id=mid,
                home_team_id=hid,
                away_team_id=aid,
                home_score=m["home_score"],
                away_score=m["away_score"],
                matchday=matchday,
                date=m["date"],
                status="FINISHED",
                stage=m.get("stage"),
                round=m.get("round"),
                group_name=m.get("group_name"),
                competition_id=COMPETITION_ID,
                year=ps.year,
            )
            prior = seen.get(mid)
            if prior is not None:
                if prior.identity != row.identity:
                    raise RepairError(
                        f"staged fixture id collision {mid}: {prior.identity} vs {row.identity}"
                    )
                continue
            seen[mid] = row
            staged.append(row)
    return staged


def check_existing_collisions(conn, staged: Sequence[StagedMatch],
                              prepared: Sequence[PreparedSeason]) -> set:
    """Refuse any staged id already owned outside the approved windows.

    Rows inside the approved windows are allowed (they are deleted first). Any
    row outside them — even with the same identity — is a hard error so the
    INSERT can never collide and no unrelated row can be overwritten.
    """
    window_ids: set = set()
    for ps in prepared:
        start, end = season_window(ps.year)
        rows = conn.execute(
            'SELECT id FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
            [COMPETITION_ID, start, end],
        ).fetchall()
        window_ids.update(r[0] for r in rows)

    by_id = {m.match_id: m for m in staged}
    if not by_id:
        return window_ids
    ids = list(by_id)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f'SELECT id FROM matches WHERE id IN ({placeholders})',
        ids,
    ).fetchall()
    for (rid,) in rows:
        if rid not in window_ids:
            raise RepairError(
                f"fixture id {rid} is already owned by a row outside the approved windows"
            )
    return window_ids


# ---------------------------------------------------------------------------
# fingerprints / independent table
# ---------------------------------------------------------------------------

def protected_fingerprints(conn,
                           excluded_mapping_keys: Optional[Iterable[str]] = None
                           ) -> Dict[str, Optional[str]]:
    """Fingerprint protected tables.

    ``team_mappings`` is included but the intentionally scoped repair keys are
    excluded, so the fingerprint proves every *other* mapping row is untouched.
    """
    excluded = set(excluded_mapping_keys or ())
    fingerprints: Dict[str, Optional[str]] = {}
    for table in PROTECTED_TABLES:
        exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        if not exists:
            fingerprints[table] = None
            continue
        cols = [d[0] for d in conn.execute(f'DESCRIBE "{table}"').fetchall()]
        if table == "team_mappings" and excluded:
            placeholders = ",".join("?" for _ in excluded)
            rows = conn.execute(
                f'SELECT * FROM "{table}" WHERE team_name NOT IN ({placeholders}) ORDER BY 1',
                list(excluded),
            ).fetchall()
        else:
            rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        payload = repr((cols, rows)).encode("utf-8")
        fingerprints[table] = sha256_bytes(payload)
    return fingerprints


def source_league_table(ps: PreparedSeason, plan: Dict[str, int]) -> Dict[int, Dict[str, Any]]:
    """Standings computed independently from source names (points/GF/GA/Solkoff)."""
    teams: Dict[int, Dict[str, Any]] = {}
    opponents: Dict[int, set] = {}

    for m in ps.records:
        if _stage_bucket(m) != "LEAGUE":
            continue
        hid, aid = plan[m["home_team"]], plan[m["away_team"]]
        hs, as_ = m["home_score"], m["away_score"]
        for tid in (hid, aid):
            teams.setdefault(tid, {"played": 0, "won": 0, "drawn": 0, "lost": 0,
                                    "gf": 0, "ga": 0, "points": 0})
            opponents.setdefault(tid, set())
        home, away = teams[hid], teams[aid]
        home["played"] += 1
        away["played"] += 1
        home["gf"] += hs
        home["ga"] += as_
        away["gf"] += as_
        away["ga"] += hs
        if hs > as_:
            home["won"] += 1
            home["points"] += 3
            away["lost"] += 1
        elif hs < as_:
            away["won"] += 1
            away["points"] += 3
            home["lost"] += 1
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["points"] += 1
            away["points"] += 1
        opponents[hid].add(aid)
        opponents[aid].add(hid)

    for tid, team in teams.items():
        team["gd"] = team["gf"] - team["ga"]
    ppg = {
        tid: (t["points"] / t["played"] if t["played"] else 0.0)
        for tid, t in teams.items()
    }
    for tid, team in teams.items():
        ppgs = [ppg[o] for o in opponents[tid] if o in teams and teams[o]["played"] > 0]
        team["solkoff"] = round(sum(ppgs) / len(ppgs), 3) if ppgs else 0.0
    return teams


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _apply(conn, prepared: Sequence[PreparedSeason], new_teams: Dict[str, int],
           plan: Dict[str, int], scoped_keys: Dict[str, int],
           staged: Sequence[StagedMatch], report: RepairReport) -> None:
    before = protected_fingerprints(conn, scoped_keys)

    conn.execute("BEGIN TRANSACTION")
    try:
        for ps in prepared:
            start, end = season_window(ps.year)
            count = conn.execute(
                'SELECT COUNT(*) FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
                [COMPETITION_ID, start, end],
            ).fetchone()[0]
            report.deletions[str(ps.year)] = count
            conn.execute(
                'DELETE FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
                [COMPETITION_ID, start, end],
            )

        for name, tid in sorted(new_teams.items()):
            conn.execute(
                "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
                [tid, name, None, None],
            )

        has_mappings = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'team_mappings'"
        ).fetchone()[0]
        if has_mappings:
            # Scoped upsert: only the canonical source-name keys are rewritten to
            # the chosen clean id; every unrelated mapping key is left untouched.
            existing = {
                key: tid for key, tid in conn.execute(
                    "SELECT team_name, team_id FROM team_mappings"
                ).fetchall()
            }
            changes = []
            for key, tid in sorted(scoped_keys.items()):
                prior = existing.get(key)
                if prior != tid:
                    changes.append({"key": key, "before": prior, "after": tid})
                conn.execute(
                    """
                    INSERT INTO team_mappings (team_name, team_id, confidence, source)
                    VALUES (?, ?, 1.0, 'historical_repair')
                    ON CONFLICT (team_name) DO UPDATE SET
                        team_id = excluded.team_id,
                        confidence = excluded.confidence,
                        source = excluded.source
                    """,
                    [key, tid],
                )
            report.mapping_changes = changes

        for row in staged:
            conn.execute(
                """
                INSERT INTO matches (
                    id, home_team_id, away_team_id, home_score, away_score,
                    matchday, "date", status, stage, "round", group_name, competition_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.match_id, row.home_team_id, row.away_team_id, row.home_score,
                 row.away_score, row.matchday, row.date, row.status, row.stage,
                 row.round, row.group_name, row.competition_id],
            )

        after = protected_fingerprints(conn, scoped_keys)
        if after != before:
            raise RepairError("protected table fingerprints changed during repair")

        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    report.applied = True
    report.inserted = len(staged)
    report.protected_after = after


# ---------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------

def _current_match_counts(conn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for year in APPROVED_YEARS:
        start, end = season_window(year)
        counts[f"{year}/{str(year + 1)[-2:]}"] = conn.execute(
            'SELECT COUNT(*) FROM matches WHERE competition_id = ? AND "date" >= ? AND "date" < ?',
            [COMPETITION_ID, start, end],
        ).fetchone()[0]
    return counts


def _other_competition_counts(conn) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT competition_id, COUNT(*) FROM matches GROUP BY competition_id ORDER BY competition_id"
    ).fetchall()
    return {(r[0] or "NULL"): r[1] for r in rows}


def run_repair(db_path: str, sources: Sequence[SeasonSource],
               explicit_mapping: Optional[Dict[str, int]] = None,
               apply: bool = False) -> RepairReport:
    """Validate and (optionally) apply the scoped repair. Dry-run by default."""
    years = [s.year for s in sources]
    if len(set(years)) != len(years):
        raise RepairError("duplicate season years in sources")
    for year in years:
        if year not in APPROVED_YEARS:
            raise RepairError(f"year {year} is not approved; approved years are {APPROVED_YEARS}")
    if not sources:
        raise RepairError("no sources supplied")

    prepared = [load_season(s) for s in sources]

    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        report = RepairReport()
        report.sources = [
            {"year": ps.year, "path": ps.path,
             "expected_sha256": ps.expected_sha256, "actual_sha256": ps.actual_sha256}
            for ps in prepared
        ]
        for ps in prepared:
            report.seasons.append(validate_season(ps))

        plan, new_teams = resolve_identity(conn, prepared, explicit_mapping)
        staged = stage_fixtures(prepared, plan)
        check_existing_collisions(conn, staged, prepared)

        # Normalized mapping keys touched by this scoped repair; all other
        # team_mappings rows must stay byte-identical.
        scoped_keys = {normalize_team_name(name): tid for name, tid in plan.items()}

        report.identity = plan
        report.new_teams = [{"id": tid, "name": name}
                            for name, tid in sorted(new_teams.items())]
        report.staged_count = len(staged)
        report.staged_by_year = {
            str(ps.year): sum(1 for m in staged if m.year == ps.year) for ps in prepared
        }
        report.current_matches = _current_match_counts(conn)
        report.other_competition_counts = _other_competition_counts(conn)
        report.protected_before = protected_fingerprints(conn, scoped_keys)
        report.source_tables = {
            str(ps.year): [
                {"teamId": tid, **stats} for tid, stats in sorted(source_league_table(ps, plan).items())
            ]
            for ps in prepared
        }

        if apply:
            _apply(conn, prepared, new_teams, plan, scoped_keys, staged, report)
        return report
    finally:
        conn.close()


def parse_source_arg(value: str) -> SeasonSource:
    parts = value.split("=")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("source must be YEAR=PATH=SHA256")
    year_str, path, sha = parts
    try:
        year = int(year_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid year in {value!r}")
    return SeasonSource(year=year, path=path, expected_sha256=sha)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline scoped repair of historical CL archives (dry-run by default)."
    )
    parser.add_argument("--db", required=True, help="path to the DuckDB database")
    parser.add_argument(
        "--source", action="append", required=True, type=parse_source_arg,
        help="YEAR=PATH=SHA256 (repeatable; approved years 2024 and 2025)",
    )
    parser.add_argument("--mapping", help="JSON file with explicit source-name -> team-id map")
    parser.add_argument("--out", help="write the JSON report to this path")
    parser.add_argument("--apply", action="store_true", help="perform the repair transaction")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    explicit_mapping = None
    if args.mapping:
        explicit_mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    try:
        report = run_repair(args.db, args.source, explicit_mapping, apply=args.apply)
    except RepairError as exc:
        print(f"REPAIR REFUSED: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(
            f"report written to {args.out} "
            f"(applied={report.applied}, staged={report.staged_count}, "
            f"new_teams={len(report.new_teams)})"
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
