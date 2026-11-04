"""Focused fail-closed legacy-takeover guard tests for ``ESPNDataService``.

These pin the narrow guards required before any ESPN takeover of a competition
that still holds unmanaged new-format (>=2024) legacy rows:

* an incoming club must have a persisted provider mapping or a unique exact/
  reviewed alias canonical identity, otherwise the import refuses to create a
  parallel canonical team;
* while any unclaimed legacy row remains in the competition-season, a fixture
  that cannot be adopted exactly is refused rather than duplicated (the legacy
  row may be the same real fixture under older polluted canonical IDs, so the
  canonical pair cannot be trusted).

Nothing here touches production data, the network or the separate reviewed
reconciliation migration; only ``backend/espn_service.py`` guards are exercised.
"""
from __future__ import annotations

import pytest

from backend.database import Database
from backend.espn_client import ESPNClient
from backend.espn_service import ESPNDataService, ESPNIdentityError
from tests.backend import espn_payloads as P


class FakeESPN(ESPNClient):
    """Offline client returning a pre-built standings/scoreboard pair."""

    def __init__(self, standings, events):
        super().__init__(cache=None)
        self._standings = standings
        self._events = events

    def _get_standings(self, slug, season_year):
        return self._standings

    def _get_scoreboard(self, slug, year_arg):
        year = str(year_arg)
        events = [e for e in self._events if e["date"][:4] == year]
        return P.scoreboard_payload(events, slug=slug)


def el_client(season_year=2024, events=None):
    return FakeESPN(
        P.standings_payload("EL", season_year),
        P.el_season_events(season_year) if events is None else events,
    )


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "espn_guard.db"))
    yield database
    database.close()


def count(db, table, where="", params=()):
    row = db.fetchone(f"SELECT COUNT(*) FROM {table} {where}", params)
    return row[0] if row else 0


def seed_teams(db, affix=None):
    """Seed all 36 canonical teams matching the ESPN display names.

    ``affix`` is an optional ``(index, suffix)`` that renames one club to a
    legacy-affixed identity so its ESPN name no longer resolves exactly.
    """
    for idx in range(1, 37):
        name = P._team_name(idx)
        if affix and idx == affix[0]:
            name = f"{name} {affix[1]}"
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, name),
        )
    db.commit()


def seed_legacy_match(db, match_id, home, away, date, stage="LEAGUE_STAGE"):
    """An unmanaged (no provider mapping) legacy EL row."""
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, 'SCHEDULED', ?, NULL, NULL, 'EL')
        """,
        (match_id, home, away, date, stage),
    )
    db.commit()


def _first_league_pair(events):
    first = next(e for e in events if e["season"]["slug"] == "league-phase")
    comp = first["competitions"][0]
    sides = {c["homeAway"]: int(c["team"]["id"]) for c in comp["competitors"]}
    return first, sides["home"], sides["away"]


# --- club identity gate ----------------------------------------------------

def test_legacy_affixed_name_blocks_current_new_team(db, monkeypatch):
    # Current-year refresh while an older new-format season still holds an
    # unmanaged legacy row: one club's canonical name carries a legacy affix, so
    # the incoming ESPN club cannot be resolved exactly and must not be created
    # as a fresh negative-id twin.
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2026)
    seed_teams(db, affix=(1, "FC"))
    events = P.el_season_events(2026)
    _, home, away = _first_league_pair(events)
    seed_legacy_match(db, -11, home, away, "2024-09-19T20:00Z")

    svc = ESPNDataService(db, client=el_client(2026))
    with pytest.raises(ESPNIdentityError) as excinfo:
        svc.sync_season("EL", 2026)

    assert "reconciliation" in str(excinfo.value)
    # Fail closed: no mappings, no new clubs, no fixtures.
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "teams") == 36
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 1


def test_exact_identities_allow_safe_fixture_adoption(db):
    # All 36 standings identities resolve to unique exact canonical teams and the
    # first processed fixture is adopted exactly; once no unclaimed row remains
    # the remaining genuinely-new fixtures are still created (no blanket block).
    seed_teams(db)
    events = P.el_season_events(2024)
    dataset = el_client(2024, events=events).fetch_season("EL", 2024)
    first = dataset["matches"][0]
    legacy_id = -999
    seed_legacy_match(
        db, legacy_id,
        int(first["home"]["externalTeamId"]), int(first["away"]["externalTeamId"]),
        first["date"], stage=first["stage"],
    )

    svc = ESPNDataService(db, client=el_client(2024, events=events))
    counts = svc.sync_season("EL", 2024)

    assert counts["teamsCreated"] == 0
    assert counts["teamsReused"] == 36
    assert counts["matchesAdopted"] == 1
    mapped = db.fetchone(
        "SELECT match_id FROM provider_match_mappings "
        "WHERE provider='espn' AND external_id=?",
        (first["externalEventId"],),
    )
    assert mapped[0] == legacy_id
    # Reused, not duplicated: the original row plus 188 created = 189.
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
    assert count(db, "teams") == 36


# --- fixture duplicate gate ------------------------------------------------

def test_null_stage_legacy_pair_blocks_duplicate(db):
    # A NULL-stage legacy row cannot be adopted exactly (stage mismatch), but it
    # is the same canonical pair in the same season, so creating a new row would
    # duplicate it: refuse and require manual reconciliation.
    seed_teams(db)
    events = P.el_season_events(2024)
    first, home, away = _first_league_pair(events)
    legacy_id = -321
    seed_legacy_match(db, legacy_id, home, away, first["date"], stage=None)

    svc = ESPNDataService(db, client=el_client(2024, events=events))
    with pytest.raises(ESPNIdentityError) as excinfo:
        svc.sync_season("EL", 2024)

    assert "reconciliation" in str(excinfo.value)
    # Old row unchanged; nothing persisted.
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "matches") == 1
    row = db.fetchone(
        'SELECT status, home_score, away_score FROM matches WHERE id = ?',
        (legacy_id,),
    )
    assert row == ("SCHEDULED", None, None)


def test_unmanaged_legacy_row_with_different_canonical_ids_blocks(db):
    # Production can hold the same real fixture under older polluted canonical
    # team IDs. The incoming ESPN fixture's clean pair will not match them, so a
    # pair-limited check would silently duplicate the season. While ANY unclaimed
    # row remains in the competition-season, refuse and require reconciliation.
    seed_teams(db)  # clean ids 1..36: all 36 identities resolve exactly
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1001,'Old club A',NULL,NULL)")
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1002,'Old club B',NULL,NULL)")
    db.commit()
    seed_legacy_match(db, -55, 1001, 1002, "2024-09-19T20:00Z")

    svc = ESPNDataService(db, client=el_client(2024))
    with pytest.raises(ESPNIdentityError) as excinfo:
        svc.sync_season("EL", 2024)

    assert "reconciliation" in str(excinfo.value)
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "teams") == 38
    assert count(db, "matches") == 1


# --- clean / fully-mapped takeover -----------------------------------------

def test_clean_db_import_still_succeeds(db):
    svc = ESPNDataService(db, client=el_client(2024))
    counts = svc.sync_season("EL", 2024)

    assert counts["teamsCreated"] == 36
    assert counts["matchesNew"] == 189
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
    assert count(db, "provider_team_mappings") == 36


def test_fully_mapped_import_idempotent_and_adds_scheduled_slot(db, monkeypatch):
    # Pin the season as current so a partially-scheduled feed is valid.
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    league_events = [
        e for e in P.el_season_events(2024) if e["season"]["slug"] == "league-phase"
    ]

    svc = ESPNDataService(db, client=el_client(2024, events=league_events))
    first = svc.sync_season("EL", 2024)
    assert first["matches"] == 144
    assert count(db, "provider_match_mappings") == 144

    # Repeat sync: idempotent, no new identities or fixtures.
    second = svc.sync_season("EL", 2024)
    assert second["matchesNew"] == 0
    assert second["matchesUpdated"] == 144
    assert second["teamsCreated"] == 0
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 144

    # A genuinely new scheduled knockout slot is still importable after the
    # competition is fully ESPN-mapped (no blanket post-takeover block).
    new_event = P.event(
        9_000_001, "2025-02-19T20:00Z", 2024, "knockout-round-playoffs",
        1, 2, None, None, status_name="STATUS_SCHEDULED", status_state="pre", leg=1,
    )
    svc.client = el_client(2024, events=league_events + [new_event])
    third = svc.sync_season("EL", 2024)
    assert third["matchesNew"] == 1
    assert third["matchesAdopted"] == 0
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 145
