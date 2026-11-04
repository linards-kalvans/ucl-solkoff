"""Transactional ESPN ingestion: provider IDs -> canonical teams/matches.

External payloads are fetched and validated *before* any database work. A single
transaction then persists provider identity mappings, new teams, match rows and
(for the current season only) the competition's live standings snapshot. Nothing
is written partially, and archived ingestion never touches live standings or
other competitions.

Team identity is conservative: an ESPN club reuses an existing canonical team by
exact cleaned name (or an explicit reviewed alias); ambiguous matches fail for
review. ESPN's own numeric IDs are never canonical. New clubs receive bounded,
collision-checked negative IDs.

Fixture identity is persistent: ``(provider, event_id) -> match_id``. A mapped
event is re-checked against the stored row (competition, canonical home/away,
stage/round and both dates inside the season window) before any update, so a
feed that reuses an id for a different fixture can never overwrite the wrong
row. A first import adopts a unique existing same competition/season/pair/
stage/round fixture and applies the current ESPN score/status/date in the same
transaction; a scheduled legacy fixture therefore becomes finished on first sync.
Date changes inside the season are allowed (reschedules); a second event cannot
silently claim a row already claimed by another event.

Legacy-takeover guard (fail closed): a competition that still holds new-format
(>=2024 season window) rows ESPN has not claimed is *contaminated* — importing
into it would silently create parallel canonical identities/fixtures. While
contaminated, an incoming club without a persisted provider mapping or a unique
exact/reviewed-alias canonical identity raises :class:`ESPNIdentityError`. A
fixture that cannot be adopted exactly while any unclaimed row remains in that
competition-season is also refused rather than duplicated: the unclaimed row may
be the same real fixture represented by older polluted canonical team IDs, so
the canonical pair cannot be trusted to detect the duplicate. Pre-2024 archives
are intentionally left to the legacy Elo path and are never gated. Fully
ESPN-mapped competitions remain importable and can add genuinely new scheduled
fixtures; there is no bypass flag.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.competitions import (
    FIRST_LEAGUE_PHASE_YEAR,
    PROVIDER_ESPN,
    league_matches_for,
)
from backend.espn_client import (
    ESPNClient,
    ESPNValidationError,
    EXPECTED_STANDINGS_TEAMS,
)
from backend.fixture_ids import INT32_MIN, provider_match_id
from backend.season_service import current_season_year, season_bounds

logger = logging.getLogger(__name__)

# Knockout fixtures for a 36-club league phase: play-off 16, R16 16, QF 8, SF 4,
# final 1 = 45. Used only to detect a truncated completed season, never to
# fabricate fixtures.
EXPECTED_KNOCKOUT_FIXTURES = 45

# First instant of the new-format ("Swiss") window. Legacy takeover gating only
# considers rows at/after this date; pre-2024 archives intentionally remain on
# the legacy Elo path and are never guarded.
_LEGACY_GUARD_FROM, _ = season_bounds(FIRST_LEAGUE_PHASE_YEAR)

_RECORD_FIELDS = (
    "played", "won", "draw", "lost",
    "goalsFor", "goalsAgainst", "goalDifference", "points",
)

_PUNCT = re.compile(r"[^0-9a-z]+")


def clean_team_name(name: Optional[str]) -> str:
    """Conservative name key: casefold + collapse punctuation/space only.

    Deliberately does NOT strip club suffixes (``United``/``City``) or run fuzzy
    matching; those cause cross-club collisions.
    """
    if not name:
        return ""
    return _PUNCT.sub(" ", name.casefold()).strip()


def _in_season(date: Optional[str], season_year: int) -> bool:
    """True when ``date`` parses and falls inside the season's Aug-Aug window."""
    if not date:
        return False
    try:
        parsed = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    day = parsed.date().isoformat()
    start, end = season_bounds(season_year)
    return start <= day < end


class ESPNIdentityError(RuntimeError):
    """An ESPN identity could not be resolved safely (ambiguity/collision)."""


class ESPNDataService:
    """Identity resolution and transactional persistence for ESPN EL/UCL data."""

    def __init__(
        self,
        db,
        client: Optional[ESPNClient] = None,
        provider: str = PROVIDER_ESPN,
        aliases: Optional[Dict[str, str]] = None,
    ):
        self.db = db
        self.provider = provider
        self.aliases = {clean_team_name(k): v for k, v in (aliases or {}).items()}
        # Identity audit counters, reset per persistence run.
        self._new_teams = 0
        self._reused_teams = 0
        self._new_matches = 0
        self._adopted_matches = 0
        self._updated_matches = 0
        # Competition id set by :meth:`_persist` when it still holds unmanaged
        # new-format rows; ``None`` means a clean/fully-mapped import.
        self._legacy_identity_guard: Optional[str] = None
        # Lazily built retired-team redirect resolver (shared with the rest of
        # the runtime) so provider mappings and exact matches never resolve to a
        # reviewed retired canonical id.
        self._team_identity = None
        if client is not None:
            self.client = client
        else:
            from backend.api_cache import APICache
            self.client = ESPNClient(cache=APICache(db))

    def _resolver(self):
        """Retired-team redirect resolver for this database."""
        if self._team_identity is None:
            from backend.team_identity import TeamRedirectResolver
            self._team_identity = TeamRedirectResolver(self.db)
        return self._team_identity


    def sync_all(self, competition_id: str) -> Dict[str, Any]:
        """Sync the current season for an ESPN competition."""
        return self.sync_season(competition_id, current_season_year())

    def sync_season(self, competition_id: str, season_year: int) -> Dict[str, Any]:
        """Fetch, validate and persist one ESPN competition-season atomically."""
        dataset = self.client.fetch_season(competition_id, season_year)
        diagnostics = list(dataset.get("diagnostics") or [])
        try:
            self._validate_dataset(dataset, diagnostics)
        except ESPNValidationError:
            # Standings and fixtures parsed individually but disagree (or a
            # record is inconsistent): evict this season's cached payloads so a
            # rejected dataset is not re-served from cache next sync.
            self._invalidate_season_cache(competition_id, season_year)
            raise
        counts = self._persist(dataset)
        counts["diagnostics"] = diagnostics
        return counts

    def _invalidate_season_cache(self, competition_id: str, season_year: int) -> None:
        """Drop this season's cached ESPN payloads after a rejected dataset.

        Defensive: the client may not cache at all (offline test doubles), and a
        failure to evict must never mask the original validation error.
        """
        invalidate = getattr(self.client, "invalidate_season", None)
        if not callable(invalidate):
            return
        try:
            invalidate(competition_id, season_year)
        except Exception:  # pragma: no cover - never mask the original error
            logger.warning(
                "failed to invalidate ESPN cache for %s %s",
                competition_id, season_year, exc_info=True,
            )

    # -- Validation (pre-transaction) ------------------------------------

    def _validate_dataset(
        self, dataset: Dict[str, Any], diagnostics: List[str]
    ) -> None:
        competition_id = dataset["competition_id"]
        season_year = dataset["season_year"]
        league_matches = dataset["league_matches"] or league_matches_for(competition_id)
        standings = (dataset.get("standings") or {}).get("teams") or []
        matches = dataset.get("matches") or []

        if len(standings) != EXPECTED_STANDINGS_TEAMS:
            raise ESPNValidationError(
                f"{competition_id} {season_year} standings has {len(standings)} "
                f"teams, expected {EXPECTED_STANDINGS_TEAMS}"
            )

        standing_ids = set()
        for team in standings:
            ext = team.get("externalTeamId")
            if not ext or not team.get("name"):
                raise ESPNValidationError(
                    f"{competition_id} {season_year} standings team lacks id/name"
                )
            if ext in standing_ids:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} duplicate standings team {ext}"
                )
            standing_ids.add(ext)

        league = [m for m in matches if m["stage"] == "LEAGUE_STAGE"]
        expected_league_fixtures = EXPECTED_STANDINGS_TEAMS * league_matches // 2
        expected_total = expected_league_fixtures + EXPECTED_KNOCKOUT_FIXTURES
        completed = season_year < current_season_year()

        # No fixture may pair a club with itself.
        for m in matches:
            if m["home"]["externalTeamId"] == m["away"]["externalTeamId"]:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} fixture "
                    f"{m['externalEventId']} pairs a team with itself"
                )

        # Duplicate league pairs indicate a truncated/duplicated feed.
        seen_pairs = set()
        for m in league:
            key = frozenset((m["home"]["externalTeamId"], m["away"]["externalTeamId"]))
            if key in seen_pairs:
                raise ESPNValidationError(
                    f"duplicate league fixture {key} in {competition_id} {season_year}"
                )
            seen_pairs.add(key)

        # League events must reference only known clubs, and every club that has
        # played must appear in the stored league fixtures (vacuously true for a
        # valid all-zero current table).
        league_ids = set()
        for m in league:
            league_ids.add(m["home"]["externalTeamId"])
            league_ids.add(m["away"]["externalTeamId"])
        if not league_ids <= standing_ids:
            raise ESPNValidationError(
                f"{competition_id} {season_year} league fixture references an "
                f"unknown club: {sorted(league_ids - standing_ids)}"
            )
        covered = {t["externalTeamId"] for t in standings if t.get("played", 0) > 0}
        if not covered <= league_ids:
            raise ESPNValidationError(
                f"{competition_id} {season_year} clubs with played games have no "
                f"league fixture: {sorted(covered - league_ids)}"
            )

        # Per-team records must match the finished league fixtures for every
        # season (played, W/D/L, goals and points), not just the game count.
        records = self._league_records(league)
        zero = {field: 0 for field in _RECORD_FIELDS}
        for team in standings:
            expected = records.get(team["externalTeamId"], dict(zero))
            for field in _RECORD_FIELDS:
                if team.get(field) != expected.get(field):
                    raise ESPNValidationError(
                        f"{competition_id} {season_year} team "
                        f"{team['externalTeamId']} {field}={team.get(field)} but "
                        f"finished league fixtures give {expected.get(field)}"
                    )

        if completed:
            if len(league) != expected_league_fixtures:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} has {len(league)} league "
                    f"fixtures, expected {expected_league_fixtures}"
                )
            if len(matches) != expected_total:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} has {len(matches)} fixtures, "
                    f"expected {expected_total}"
                )
            unfinished = [m for m in matches if m["status"] != "FINISHED"]
            if unfinished:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} is completed but has "
                    f"{len(unfinished)} unfinished fixtures"
                )
        else:
            if len(league) > expected_league_fixtures:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} has {len(league)} league "
                    f"fixtures, expected at most {expected_league_fixtures}"
                )
            if len(matches) > expected_total:
                raise ESPNValidationError(
                    f"{competition_id} {season_year} has {len(matches)} fixtures, "
                    f"expected at most {expected_total}"
                )

    @staticmethod
    def _league_records(
        league: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, int]]:
        """Per-club record from the FINISHED league fixtures only."""
        records: Dict[str, Dict[str, int]] = {}
        for m in league:
            if m["status"] != "FINISHED":
                continue
            if m.get("homeScore") is None or m.get("awayScore") is None:
                continue
            for side in ("home", "away"):
                ext = m[side]["externalTeamId"]
                rec = records.setdefault(ext, {
                    "played": 0, "won": 0, "draw": 0, "lost": 0,
                    "goalsFor": 0, "goalsAgainst": 0,
                    "goalDifference": 0, "points": 0,
                })
                if side == "home":
                    gf, ga = m["homeScore"], m["awayScore"]
                else:
                    gf, ga = m["awayScore"], m["homeScore"]
                rec["played"] += 1
                rec["goalsFor"] += gf
                rec["goalsAgainst"] += ga
                if gf > ga:
                    rec["won"] += 1
                elif gf < ga:
                    rec["lost"] += 1
                else:
                    rec["draw"] += 1
        for rec in records.values():
            rec["goalDifference"] = rec["goalsFor"] - rec["goalsAgainst"]
            rec["points"] = 3 * rec["won"] + rec["draw"]
        return records

    # -- Persistence ------------------------------------------------------

    def _persist(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        competition_id = dataset["competition_id"]
        season_year = dataset["season_year"]
        standings = (dataset.get("standings") or {}).get("teams") or []
        matches = dataset.get("matches") or []

        # Persistence-boundary guard: the live snapshot is replaced only for a
        # complete, fully-resolvable table, so a partial FakeClient dataset can
        # never leave a half-empty standings table.
        if len(standings) != EXPECTED_STANDINGS_TEAMS:
            raise ESPNValidationError(
                f"refusing to persist {len(standings)} standings rows for "
                f"{competition_id} {season_year}"
            )

        self._new_teams = 0
        self._reused_teams = 0
        self._new_matches = 0
        self._adopted_matches = 0
        self._updated_matches = 0

        # Legacy-takeover gate, evaluated on the pre-write state. A competition
        # that still holds unmanaged new-format rows must not gain new canonical
        # identities or duplicate fixtures blindly; the guard fails closed until
        # an explicit identity/fixture reconciliation has run.
        self._legacy_identity_guard = (
            competition_id
            if self._unmanaged_legacy_present(competition_id)
            else None
        )

        # One transaction, whole-lock: external payloads were fetched/validated
        # before this call, so no HTTP happens under the lock. Holding the shared
        # lock across BEGIN..COMMIT/ROLLBACK stops a competing thread's commit
        # (e.g. an APICache write) from committing a partial import.
        with self.db.transaction():
            team_ids: Dict[str, int] = {}
            for team in standings:
                ext = team.get("externalTeamId")
                if not ext or not team.get("name"):
                    raise ESPNValidationError(
                        f"standings team {ext!r} lacks an id/name for {competition_id}"
                    )
                team_ids[ext] = self._resolve_team(team, team_ids)
            for m in matches:
                for side in ("home", "away"):
                    ref = m[side]
                    if ref["externalTeamId"] not in team_ids:
                        team_ids[ref["externalTeamId"]] = self._resolve_team(
                            ref, team_ids
                        )
            if any(t.get("externalTeamId") not in team_ids for t in standings):
                raise ESPNValidationError(
                    "standings identity resolution incomplete; refusing to replace"
                )

            for m in matches:
                self._resolve_match(m, team_ids, competition_id, season_year)

            standings_written = 0
            if season_year == current_season_year():
                standings_written = self._replace_standings(
                    competition_id, standings, team_ids
                )

        league = sum(1 for m in matches if m["stage"] == "LEAGUE_STAGE")
        return {
            "competition_id": competition_id,
            "season_year": season_year,
            "teams": len(team_ids),
            "teamsCreated": self._new_teams,
            "teamsReused": self._reused_teams,
            "standings": standings_written,
            "matches": len(matches),
            "matchesNew": self._new_matches,
            "matchesAdopted": self._adopted_matches,
            "matchesUpdated": self._updated_matches,
            "league_matches": league,
            "knockout_matches": len(matches) - league,
        }

    def _resolve_team(self, ref: Dict[str, Any], cache: Dict[str, int]) -> int:
        external_id = ref["externalTeamId"]
        if external_id in cache:
            return cache[external_id]

        row = self.db.fetchone(
            "SELECT team_id FROM provider_team_mappings "
            "WHERE provider = ? AND external_id = ?",
            (self.provider, external_id),
        )
        if row:
            mapped_id = int(row[0])
            # A persisted provider mapping is itself a reference: canonicalize it
            # through reviewed redirects so a mapping written before a club was
            # retired cannot resurrect the retired id. The mapping row is
            # normalised to the root in the same transaction.
            team_id = self._resolver().canonical_id(mapped_id)
            # Persistence-boundary guard: a mapping must resolve to a real
            # canonical team. Foreign keys normally prevent a dangling row, but
            # an explicit check keeps the boundary consistent even if a legacy
            # row predates enforcement.
            exists = self.db.fetchone("SELECT 1 FROM teams WHERE id = ?", (team_id,))
            if not exists:
                raise ESPNIdentityError(
                    f"provider {self.provider} external_id {external_id} maps to "
                    f"missing canonical team {team_id}"
                )
            if team_id != mapped_id:
                self.db.execute(
                    "UPDATE provider_team_mappings SET team_id = ? "
                    "WHERE provider = ? AND external_id = ?",
                    (team_id, self.provider, external_id),
                )
            self._reused_teams += 1
            return team_id

        canonical = self._match_canonical_team(ref.get("name"))
        if canonical is None:
            if self._legacy_identity_guard:
                raise ESPNIdentityError(
                    f"refusing to create canonical team {ref.get('name')!r} for "
                    f"provider {self.provider} external_id {external_id}: "
                    f"{self._legacy_identity_guard} still holds unmanaged legacy "
                    f"new-format fixtures; an explicit legacy identity audit/"
                    f"reconciliation is required first. Persist a "
                    f"provider_team_mappings row (provider={self.provider!r}, "
                    f"external_id={external_id!r}) or add a unique exact/reviewed "
                    f"alias for this club before re-running the ESPN import."
                )
            canonical = self._create_team(ref)
            self._new_teams += 1
        else:
            self._reused_teams += 1
            self._fill_missing_metadata(canonical, ref)

        self.db.execute(
            "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (provider, external_id) DO UPDATE SET team_id = excluded.team_id",
            (self.provider, external_id, canonical),
        )
        return canonical

    def _match_canonical_team(self, name: Optional[str]) -> Optional[int]:
        target = clean_team_name(name)
        if not target:
            return None
        alias = self.aliases.get(target)
        if alias:
            target = clean_team_name(alias)
        rows = self.db.fetchall("SELECT id, name FROM teams")
        matched = [int(tid) for tid, existing in rows if clean_team_name(existing) == target]
        # Canonicalize through reviewed redirects so a retired row's name maps to
        # its active root; several rows resolving to the same root are one club.
        resolver = self._resolver()
        canonical = sorted({resolver.canonical_id(tid) for tid in matched})
        if len(canonical) > 1:
            raise ESPNIdentityError(
                f"ambiguous canonical team for {name!r}: ids {canonical}"
            )
        return canonical[0] if canonical else None

    def _unmanaged_legacy_present(self, competition_id: str) -> bool:
        """True when the competition holds unclaimed new-format (>=2024) rows.

        "Unmanaged" means no ``provider_match_mappings`` row for this provider.
        The window starts at the first league-phase season, so pre-2024 archives
        (deliberately kept on the legacy Elo path) never gate an import.

        Reviewed ``QUALIFYING`` rows are excluded precisely: they are legitimate
        pre-main-stage fixtures ESPN does not cover, so their presence must not
        permanently gate a takeover. ``IS DISTINCT FROM`` keeps every other value
        guarded, including a NULL/unknown stage.
        """
        row = self.db.fetchone(
            """
            SELECT COUNT(*) FROM matches m
            WHERE m.competition_id = ?
              AND m."date" >= ?
              AND m.stage IS DISTINCT FROM 'QUALIFYING'
              AND NOT EXISTS (
                  SELECT 1 FROM provider_match_mappings pmm
                  WHERE pmm.provider = ? AND pmm.match_id = m.id
              )
            """,
            (competition_id, _LEGACY_GUARD_FROM, self.provider),
        )
        return bool(row and row[0])

    def _unmanaged_season_rows(
        self, competition_id: str, season_year: int
    ) -> List[Any]:
        """Unclaimed rows for this competition-season.

        Used to refuse a *potential duplicate*: an unclaimed row may be the same
        real fixture represented by older polluted canonical team IDs, so the
        canonical pair cannot be trusted to detect the duplicate. While any such
        row remains, a fixture that could not be adopted exactly must be
        reconciled manually, never guessed at by creating a new row.

        Reviewed ``QUALIFYING`` rows are excluded exactly (``IS DISTINCT FROM``)
        so an evidenced pre-main-stage tie neither blocks nor is misread as an
        unclaimed main-stage duplicate; NULL/unknown stages stay listed.
        """
        start, end = season_bounds(season_year)
        return self.db.fetchall(
            """
            SELECT m.id, m.stage FROM matches m
            WHERE m.competition_id = ? AND m."date" >= ? AND m."date" < ?
              AND m.stage IS DISTINCT FROM 'QUALIFYING'
              AND NOT EXISTS (
                  SELECT 1 FROM provider_match_mappings pmm
                  WHERE pmm.provider = ? AND pmm.match_id = m.id
              )
            ORDER BY m.id
            """,
            (competition_id, start, end, self.provider),
        )

    def _create_team(self, ref: Dict[str, Any]) -> int:
        existing_ids = {int(r[0]) for r in self.db.fetchall("SELECT id FROM teams")}
        min_id = min(existing_ids) if existing_ids else 0
        new_id = -(abs(min_id) + 1000 + len(existing_ids))
        while new_id < INT32_MIN or new_id in existing_ids:
            new_id += 1
        self.db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
            (new_id, ref.get("name"), ref.get("abbreviation"), ref.get("crest")),
        )
        logger.info("ESPN created canonical team %s for %r", new_id, ref.get("name"))
        return new_id

    def _fill_missing_metadata(self, team_id: int, ref: Dict[str, Any]) -> None:
        row = self.db.fetchone("SELECT code, crest FROM teams WHERE id = ?", (team_id,))
        if not row:
            return
        code, crest = row[0], row[1]
        updates = {}
        if not code and ref.get("abbreviation"):
            updates["code"] = ref["abbreviation"]
        if not crest and ref.get("crest"):
            updates["crest"] = ref["crest"]
        if updates:
            assignments = ", ".join(f"{col} = ?" for col in updates)
            self.db.execute(
                f"UPDATE teams SET {assignments} WHERE id = ?",
                tuple(updates.values()) + (team_id,),
            )

    def _resolve_match(
        self,
        m: Dict[str, Any],
        team_ids: Dict[str, int],
        competition_id: str,
        season_year: int,
    ) -> int:
        external_id = m["externalEventId"]
        home_id = team_ids[m["home"]["externalTeamId"]]
        away_id = team_ids[m["away"]["externalTeamId"]]

        if not _in_season(m.get("date"), season_year):
            raise ESPNIdentityError(
                f"event {external_id} date {m.get('date')!r} is outside "
                f"{competition_id} season {season_year}"
            )

        row = self.db.fetchone(
            "SELECT match_id FROM provider_match_mappings "
            "WHERE provider = ? AND external_id = ?",
            (self.provider, external_id),
        )
        if row:
            match_id = int(row[0])
            stored = self.db.fetchone(
                'SELECT home_team_id, away_team_id, competition_id, stage, '
                'COALESCE("round", \'\'), "date" FROM matches WHERE id = ?',
                (match_id,),
            )
            if not stored:
                raise ESPNIdentityError(
                    f"mapping for provider {self.provider} event {external_id} "
                    f"points at missing match {match_id}"
                )
            s_home, s_away, s_comp, s_stage, s_round, s_date = (
                int(stored[0]), int(stored[1]), stored[2], stored[3],
                stored[4], stored[5],
            )
            if (s_home, s_away) != (home_id, away_id):
                raise ESPNIdentityError(
                    f"event {external_id} now maps to teams ({home_id},{away_id}) "
                    f"but stored row {match_id} is ({s_home},{s_away})"
                )
            if s_comp != competition_id:
                raise ESPNIdentityError(
                    f"event {external_id} competition {competition_id} != stored "
                    f"{s_comp!r}"
                )
            if s_stage != m["stage"]:
                raise ESPNIdentityError(
                    f"event {external_id} stage {m['stage']} != stored {s_stage!r}"
                )
            if s_round != (m["round"] or ""):
                raise ESPNIdentityError(
                    f"event {external_id} round {m['round']!r} != stored {s_round!r}"
                )
            if not _in_season(s_date, season_year):
                raise ESPNIdentityError(
                    f"stored row {match_id} date {s_date!r} is outside "
                    f"{competition_id} season {season_year}"
                )
            self.db.execute(
                'UPDATE matches SET home_score = ?, away_score = ?, status = ?, '
                '"date" = ? WHERE id = ?',
                (m["homeScore"], m["awayScore"], m["status"], m["date"], match_id),
            )
            self._updated_matches += 1
            return match_id

        start, end = season_bounds(season_year)
        round_value = m["round"] or ""
        existing = self.db.fetchall(
            'SELECT id FROM matches WHERE competition_id = ? AND home_team_id = ? '
            'AND away_team_id = ? AND stage = ? '
            "AND COALESCE(\"round\", '') = ? AND \"date\" >= ? AND \"date\" < ?",
            (competition_id, home_id, away_id, m["stage"], round_value, start, end),
        )
        if len(existing) > 1:
            raise ESPNIdentityError(
                f"ambiguous existing fixture for event {external_id}: "
                f"{sorted(int(r[0]) for r in existing)}"
            )
        if len(existing) == 1:
            match_id = int(existing[0][0])
            claimed = self.db.fetchone(
                "SELECT external_id FROM provider_match_mappings "
                "WHERE provider = ? AND match_id = ?",
                (self.provider, match_id),
            )
            if claimed and str(claimed[0]) != external_id:
                raise ESPNIdentityError(
                    f"existing fixture {match_id} already claimed by provider "
                    f"event {claimed[0]}"
                )
            # Apply the current ESPN result in the same (first) transaction so a
            # scheduled legacy row becomes finished immediately, not on a later
            # repeat sync.
            self.db.execute(
                'UPDATE matches SET home_score = ?, away_score = ?, status = ?, '
                '"date" = ? WHERE id = ?',
                (m["homeScore"], m["awayScore"], m["status"], m["date"], match_id),
            )
            self._adopted_matches += 1
        else:
            # Legacy-takeover gate: any unclaimed row left in this
            # competition-season means this import would duplicate existing
            # fixture data. Such a row may be the same fixture with older
            # polluted canonical team IDs, so the canonical pair is not a safe
            # duplicate test; reconcile manually rather than create a new row.
            legacy = self._unmanaged_season_rows(competition_id, season_year)
            if legacy:
                raise ESPNIdentityError(
                    f"refusing to create fixture for provider {self.provider} "
                    f"event {external_id}: {competition_id} {season_year} still "
                    f"holds unmanaged legacy fixture(s) "
                    f"{[int(r[0]) for r in legacy]} that could not be adopted "
                    f"exactly (identity/stage/round mismatch); explicit legacy "
                    f"fixture reconciliation is required before the ESPN import."
                )
            match_id = provider_match_id(self.provider, external_id)
            self._new_matches += 1
            guard = self.db.fetchone(
                "SELECT home_team_id, away_team_id, competition_id FROM matches "
                "WHERE id = ?",
                (match_id,),
            )
            if guard and (int(guard[0]), int(guard[1]), guard[2]) != (
                home_id, away_id, competition_id
            ):
                raise ESPNIdentityError(
                    f"fixture ID collision for provider {self.provider} event "
                    f"{external_id} at id {match_id}"
                )
            self.db.execute(
                """
                INSERT INTO matches (
                    id, home_team_id, away_team_id, home_score, away_score,
                    matchday, date, status, stage, round, group_name, competition_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (match_id, home_id, away_id, m["homeScore"], m["awayScore"],
                 None, m["date"], m["status"], m["stage"], m["round"], None,
                 competition_id),
            )

        self.db.execute(
            "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (provider, external_id) DO UPDATE SET match_id = excluded.match_id",
            (self.provider, external_id, match_id),
        )
        return match_id

    def _replace_standings(
        self,
        competition_id: str,
        standings: List[Dict[str, Any]],
        team_ids: Dict[str, int],
    ) -> int:
        now = datetime.utcnow().isoformat()
        self.db.execute(
            "DELETE FROM standings WHERE competition_id = ?", (competition_id,)
        )
        written = 0
        for team in standings:
            team_id = team_ids.get(team["externalTeamId"])
            if team_id is None:
                raise ESPNValidationError(
                    f"standings team {team['externalTeamId']} unresolved; "
                    "refusing a partial snapshot replacement"
                )
            self.db.execute(
                """
                INSERT INTO standings (
                    competition_id, team_id, position, played, won, drawn, lost,
                    goals_for, goals_against, goal_difference, points, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (competition_id, team_id, team.get("position"), team.get("played", 0),
                 team.get("won", 0), team.get("draw", 0), team.get("lost", 0),
                 team.get("goalsFor", 0), team.get("goalsAgainst", 0),
                 team.get("goalDifference", 0), team.get("points", 0), now),
            )
            written += 1
        return written
