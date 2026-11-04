"""Hermetic tests for ESPN identity resolution and transactional ingestion."""
from __future__ import annotations

import copy
from unittest.mock import Mock, patch

import duckdb
import pytest

from backend.competitions import provider_for
from backend.data_service import DataService, missing_history_competitions
from backend.database import Database
from backend.espn_client import ESPNClient, ESPNValidationError
from backend.espn_service import ESPNDataService, ESPNIdentityError
from backend.fixture_ids import INT32_MIN, provider_match_id
from backend.scheduler import DataScheduler
from tests.backend import espn_payloads as P


class FakeESPN(ESPNClient):
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


class StubClient:
    """Returns a pre-built dataset verbatim (bypasses client parsing)."""

    def __init__(self, dataset):
        self._dataset = dataset

    def fetch_season(self, competition_id, season_year):
        return copy.deepcopy(self._dataset)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "espn.db"))
    yield database
    database.close()


def count(db, table, where="", params=()):
    row = db.fetchone(f"SELECT COUNT(*) FROM {table} {where}", params)
    return row[0] if row else 0


# --- full ingestion --------------------------------------------------------

def test_full_archived_season_ingest(db):
    svc = ESPNDataService(db, client=el_client(2024))
    counts = svc.sync_season("EL", 2024)
    assert counts["matches"] == 189
    assert counts["league_matches"] == 144
    assert counts["knockout_matches"] == 45
    assert counts["teams"] == 36
    assert counts["standings"] == 0  # archived seasons never write standings
    assert counts["teamsCreated"] == 36
    assert counts["matchesNew"] == 189
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
    assert count(db, "teams") == 36
    assert count(db, "provider_team_mappings") == 36
    assert count(db, "provider_match_mappings") == 189
    # New canonical teams use bounded negative int32 ids.
    for (tid,) in db.fetchall("SELECT id FROM teams"):
        assert INT32_MIN <= tid < 0


def test_second_sync_is_idempotent(db):
    svc = ESPNDataService(db, client=el_client(2024))
    svc.sync_season("EL", 2024)
    before = count(db, "matches", "WHERE competition_id = 'EL'")
    counts = svc.sync_season("EL", 2024)
    assert counts["matches"] == 189
    assert counts["teamsCreated"] == 0 and counts["teamsReused"] == 36
    assert counts["matchesNew"] == 0 and counts["matchesUpdated"] == 189
    assert count(db, "matches", "WHERE competition_id = 'EL'") == before
    assert count(db, "teams") == 36
    assert count(db, "provider_match_mappings") == 189


def test_reschedule_updates_mapped_row(db):
    events = P.el_season_events(2024)
    svc = ESPNDataService(db, client=el_client(2024, events=events))
    svc.sync_season("EL", 2024)

    target = next(e for e in events if e["season"]["slug"] == "league-phase")
    ext = target["id"]
    row = db.fetchone(
        "SELECT match_id FROM provider_match_mappings WHERE provider='espn' AND external_id=?",
        (ext,),
    )
    match_id = row[0]

    moved = [dict(e) for e in events]
    for e in moved:
        if e["id"] == ext:
            e["date"] = "2024-10-15T20:00Z"
    svc.client = el_client(2024, events=moved)
    svc.sync_season("EL", 2024)

    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
    date = db.fetchone('SELECT "date" FROM matches WHERE id = ?', (match_id,))[0]
    assert date == "2024-10-15T20:00Z"


def test_reuses_existing_canonical_fixture(db):
    # Pre-create canonical teams whose names match the ESPN display names.
    for idx in range(1, 37):
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, P._team_name(idx)),
        )
    events = P.el_season_events(2024)
    first = next(e for e in events if e["season"]["slug"] == "league-phase")
    comp = first["competitions"][0]
    by_side = {c["homeAway"]: int(c["team"]["id"]) for c in comp["competitors"]}
    legacy_id = -999
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, 'SCHEDULED', 'LEAGUE_STAGE',
                  NULL, NULL, 'EL')
        """,
        (legacy_id, by_side["home"], by_side["away"], first["date"]),
    )
    db.commit()

    svc = ESPNDataService(db, client=el_client(2024, events=events))
    svc.sync_season("EL", 2024)

    mapped = db.fetchone(
        "SELECT match_id FROM provider_match_mappings WHERE provider='espn' AND external_id=?",
        (first["id"],),
    )
    assert mapped[0] == legacy_id
    # Reused, not duplicated: 189 total rows despite the pre-existing one.
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
    assert count(db, "teams") == 36
    # Adoption applies the current ESPN result in the FIRST transaction, so a
    # scheduled legacy row becomes finished immediately.
    adopted = db.fetchone(
        'SELECT status, home_score, away_score, "date" FROM matches WHERE id = ?',
        (legacy_id,),
    )
    assert adopted[0] == "FINISHED"
    assert adopted[1] == 3 and adopted[2] == 0
    assert adopted[3] == first["date"]


# --- identity --------------------------------------------------------------

def _base_dataset(client):
    return client.fetch_season("EL", 2024)


def test_ambiguous_canonical_name_raises(db):
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1,'ESPN Club 01',NULL,NULL)")
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (2,'espn-club-01',NULL,NULL)")
    db.commit()
    dataset = _base_dataset(el_client(2024))
    with pytest.raises(ESPNIdentityError):
        ESPNDataService(db, client=el_client(2024))._persist(dataset)
    # Rolled back: no mappings persisted.
    assert count(db, "provider_team_mappings") == 0


def test_resolve_team_missing_canonical_raises(tmp_path):
    # A mapping pointing at a non-existent canonical team must fail loudly rather
    # than returning a dangling id into the persistence boundary. A native
    # connection without an enforced foreign key lets this dangling row exist;
    # the service must still refuse it.
    class _ConnDB:
        """Database-shaped adapter over a native connection (no enforced FK)."""

        def __init__(self, conn):
            self.conn = conn

        def execute(self, query, params=None):
            return self.conn.execute(query, params) if params else self.conn.execute(query)

        def fetchone(self, query, params=None):
            cursor = self.conn.execute(query, params) if params else self.conn.execute(query)
            return cursor.fetchone()

        def fetchall(self, query, params=None):
            cursor = self.conn.execute(query, params) if params else self.conn.execute(query)
            return cursor.fetchall()

    con = duckdb.connect(str(tmp_path / "dangling.db"))
    con.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT, code TEXT, crest TEXT)")
    con.execute(
        "CREATE TABLE provider_team_mappings "
        "(provider TEXT, external_id TEXT, team_id INTEGER)"
    )
    con.execute("INSERT INTO provider_team_mappings VALUES ('espn', '1', 77)")
    svc = ESPNDataService(_ConnDB(con), client=el_client(2024))
    try:
        with pytest.raises(ESPNIdentityError):
            svc._resolve_team({"externalTeamId": "1", "name": "ESPN Club 01"}, {})
    finally:
        con.close()


def test_reuses_canonical_team_by_exact_name_across_competitions(db):
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (77,'ESPN Club 01',NULL,NULL)")
    db.commit()
    svc = ESPNDataService(db, client=el_client(2024))
    svc.sync_season("EL", 2024)
    mapped = db.fetchone(
        "SELECT team_id FROM provider_team_mappings WHERE provider='espn' AND external_id='1'"
    )
    assert mapped[0] == 77
    # No duplicate club created for the same name.
    assert count(db, "teams", "WHERE name = 'ESPN Club 01'") == 1


def test_alias_resolution(db):
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (55,'AS Monaco',NULL,NULL)")
    db.commit()
    # ESPN displays "Monaco"; an explicit reviewed alias maps it.
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)]
    standings = P.standings_payload("EL", 2024)
    standings["children"][0]["standings"]["entries"][0]["team"]["displayName"] = "Monaco"
    svc = ESPNDataService(db, client=FakeESPN(standings, events), aliases={"monaco": "AS Monaco"})
    svc._persist(svc.client.fetch_season("EL", 2024))
    mapped = db.fetchone(
        "SELECT team_id FROM provider_team_mappings WHERE provider='espn' AND external_id='1'"
    )
    assert mapped[0] == 55


def test_existing_metadata_preserved_when_not_missing(db):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (9,'ESPN Club 01','OLD','old.png')"
    )
    db.commit()
    svc = ESPNDataService(db, client=el_client(2024))
    svc.sync_season("EL", 2024)
    row = db.fetchone("SELECT code, crest FROM teams WHERE id = 9")
    assert row == ("OLD", "old.png")


def test_missing_metadata_filled(db):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (9,'ESPN Club 01',NULL,NULL)"
    )
    db.commit()
    svc = ESPNDataService(db, client=el_client(2024))
    svc.sync_season("EL", 2024)
    row = db.fetchone("SELECT code, crest FROM teams WHERE id = 9")
    assert row == ("E01", "https://cdn.example/1.png")


# --- collision / ambiguity guards -----------------------------------------

def test_fixture_id_collision_aborts(db):
    dataset = _base_dataset(el_client(2024))
    match = dataset["matches"][0]
    ext = match["externalEventId"]
    candidate = provider_match_id("espn", ext)
    home = int(match["home"]["externalTeamId"])
    away = int(match["away"]["externalTeamId"])

    # Seed all 36 canonical identities so team resolution reuses existing clubs
    # and can never trip the legacy team-creation gate.
    for idx in range(1, 37):
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, P._team_name(idx)),
        )
    # A *pre-2024* legacy EL row already occupies the deterministic provider id
    # of a different fixture. It sits before the new-format window, so neither
    # the legacy team gate nor the unmanaged-season-row gate applies; the import
    # must fail on the fixture ID collision specifically. The row's canonical
    # pair (3,4) differs from this event's (home,away).
    assert (home, away) != (3, 4)
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, 3, 4, NULL, NULL, NULL, '2023-09-01T20:00Z', 'SCHEDULED',
                  'GROUP_STAGE', NULL, NULL, 'EL')
        """,
        (candidate,),
    )
    db.commit()

    with pytest.raises(ESPNIdentityError) as excinfo:
        ESPNDataService(db, client=el_client(2024))._persist(dataset)

    # Assert the *specific* branch, not just any ESPNIdentityError: a different
    # legacy gate message would fail this.
    message = str(excinfo.value)
    assert message.startswith("fixture ID collision")
    assert f"event {ext} at id {candidate}" in message
    # Rolled back: no partial identity/fixture writes.
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "provider_team_mappings") == 0


def test_ambiguous_existing_fixture_aborts(db):
    for idx in range(1, 37):
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, P._team_name(idx)),
        )
    events = P.el_season_events(2024)
    first = next(e for e in events if e["season"]["slug"] == "league-phase")
    sides = {c["homeAway"]: int(c["team"]["id"]) for c in first["competitions"][0]["competitors"]}
    for match_id in (-201, -202):
        db.execute(
            """
            INSERT INTO matches (
                id, home_team_id, away_team_id, home_score, away_score,
                matchday, "date", status, stage, "round", group_name, competition_id
            ) VALUES (?, ?, ?, NULL, NULL, NULL, '2024-09-01T20:00Z', 'SCHEDULED',
                      'LEAGUE_STAGE', NULL, NULL, 'EL')
            """,
            (match_id, sides["home"], sides["away"]),
        )
    db.commit()

    dataset = FakeESPN(
        P.standings_payload("EL", 2024), events
    ).fetch_season("EL", 2024)
    with pytest.raises(ESPNIdentityError):
        ESPNDataService(db, client=el_client(2024))._persist(dataset)
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "matches") == 2


# --- validation ------------------------------------------------------------

def test_truncated_completed_season_rejected_and_db_unchanged(db):
    events = P.el_season_events(2024)
    league = [e for e in events if e["season"]["slug"] == "league-phase"]
    drop_id = league[0]["id"]
    truncated = [e for e in events if e["id"] != drop_id]  # drop one league fixture
    svc = ESPNDataService(db, client=el_client(2024, events=truncated))
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)
    assert count(db, "matches") == 0
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "teams") == 0


def test_duplicate_league_pair_rejected(db):
    events = P.el_season_events(2024)
    league = [e for e in events if e["season"]["slug"] == "league-phase"]
    first = league[0]
    sides = {c["homeAway"]: int(c["team"]["id"]) for c in first["competitions"][0]["competitors"]}
    twin = P.event(
        9_999_999, "2024-09-11T20:00Z", 2024, "league-phase",
        sides["home"], sides["away"], 1, 0,
    )
    dup = list(events) + [twin]
    svc = ESPNDataService(db, client=el_client(2024, events=dup))
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)


def test_corrupted_goals_rejected_before_persistence(db):
    dataset = el_client(2024).fetch_season("EL", 2024)
    dataset["standings"]["teams"][0]["goalsFor"] += 1  # breaks GF-GA=GD and record
    svc = ESPNDataService(db, client=StubClient(dataset))
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)
    assert count(db, "matches") == 0
    assert count(db, "teams") == 0
    assert count(db, "provider_team_mappings") == 0


def test_corrupted_points_rejected_before_persistence(db):
    dataset = el_client(2024).fetch_season("EL", 2024)
    dataset["standings"]["teams"][2]["points"] += 3
    svc = ESPNDataService(db, client=StubClient(dataset))
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)
    assert count(db, "standings") == 0
    assert count(db, "matches") == 0
    assert count(db, "teams") == 0


def test_dataset_validation_failure_invalidates_client_season_cache(db):
    dataset = el_client(2024).fetch_season("EL", 2024)
    dataset["standings"]["teams"][0]["points"] += 3  # disagrees with fixtures
    invalidated = []

    class SpyClient:
        def fetch_season(self, competition_id, season_year):
            return copy.deepcopy(dataset)

        def invalidate_season(self, competition_id, season_year):
            invalidated.append((competition_id, season_year))

    svc = ESPNDataService(db, client=SpyClient())
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)

    # A rejected dataset evicts this season's cached ESPN payloads so it is not
    # re-served from cache, and nothing was persisted.
    assert invalidated == [("EL", 2024)]
    assert count(db, "teams") == 0
    assert count(db, "matches") == 0


def test_second_event_cannot_claim_mapped_existing_row(db):
    for idx in range(1, 37):
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, P._team_name(idx)),
        )
    events = P.el_season_events(2024)
    first = next(e for e in events if e["season"]["slug"] == "league-phase")
    sides = {
        c["homeAway"]: int(c["team"]["id"])
        for c in first["competitions"][0]["competitors"]
    }
    legacy_id = -777
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, 'SCHEDULED', 'LEAGUE_STAGE',
                  NULL, NULL, 'EL')
        """,
        (legacy_id, sides["home"], sides["away"], first["date"]),
    )
    # A different provider event already claims the row.
    db.execute(
        "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
        "VALUES ('espn', 'OTHER-EVENT', ?)",
        (legacy_id,),
    )
    db.commit()

    with pytest.raises(ESPNIdentityError):
        ESPNDataService(db, client=el_client(2024, events=events)).sync_season("EL", 2024)

    # Rolled back: the row is still only claimed by the other event, and the
    # new event did not overwrite it.
    assert db.fetchone(
        'SELECT home_score, status FROM matches WHERE id = ?', (legacy_id,)
    ) == (None, "SCHEDULED")
    assert count(db, "provider_match_mappings") == 1
    assert count(db, "matches") == 1


def test_zero_game_current_table_preserved(db, monkeypatch):
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    payload = P.standings_payload("EL", 2024)
    zero_stats = {
        "gamesPlayed", "wins", "ties", "losses",
        "pointsFor", "pointsAgainst", "pointDifferential", "points",
    }
    for entry in payload["children"][0]["standings"]["entries"]:
        for stat in entry["stats"]:
            if stat["name"] in zero_stats:
                stat["value"] = 0

    svc = ESPNDataService(db, client=FakeESPN(payload, []))
    counts = svc.sync_season("EL", 2024)
    assert counts["standings"] == 36
    assert count(db, "matches") == 0
    assert count(db, "standings", "WHERE competition_id = 'EL'") == 36
    assert db.fetchone(
        "SELECT COUNT(*) FROM standings WHERE played = 0"
    )[0] == 36


def test_current_season_writes_standings_only_for_selected_competition(db, monkeypatch):
    # Pin the "current" season so 2024 is treated as live.
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1,'CL Club',NULL,NULL)")
    db.execute(
        "INSERT INTO standings (competition_id, team_id, position, played, points, last_updated)"
        " VALUES ('CL', 1, 1, 1, 3, 'x')"
    )
    db.commit()

    svc = ESPNDataService(db, client=el_client(2024))
    counts = svc.sync_season("EL", 2024)
    assert counts["standings"] == 36
    assert count(db, "standings", "WHERE competition_id = 'EL'") == 36
    # CL snapshot untouched.
    assert count(db, "standings", "WHERE competition_id = 'CL'") == 1


def test_current_partial_season_without_knockout_allowed(db, monkeypatch):
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    events = [e for e in P.el_season_events(2024) if e["season"]["slug"] == "league-phase"]
    svc = ESPNDataService(db, client=el_client(2024, events=events))
    counts = svc.sync_season("EL", 2024)
    assert counts["matches"] == 144
    assert counts["standings"] == 36


# --- dispatch / credential handling ---------------------------------------

def test_data_service_dispatches_espn(db):
    api = Mock()
    ds = DataService(db, api)
    fake = Mock()
    ds._espn_service = fake
    ds.sync_all("EL")
    fake.sync_all.assert_called_once_with("EL")
    api.get_competition_standings.assert_not_called()


def test_data_service_cl_without_client_raises(db):
    ds = DataService(db, None)
    with pytest.raises(RuntimeError):
        ds.sync_all("CL")


def test_scheduler_tolerates_missing_key(tmp_path):
    db = Mock()
    with patch("backend.scheduler.APIClient", side_effect=ValueError("no key")), \
         patch("backend.scheduler.DataService"), \
         patch("backend.scheduler.SolkoffCalculator"), \
         patch("backend.scheduler.EloCalculator"):
        sched = DataScheduler(db, "CL", competitions=["CL", "EL"])
    assert sched.api_client is None
    assert sched.data_service is not None


def test_missing_history_reports_espn_windows(db):
    missing = missing_history_competitions(db)
    assert any(m.startswith("EL 2024") for m in missing)
    assert any(m.startswith("UCL 2024") for m in missing)
    # Conference inception: no pre-2021 Conference years are requested.
    assert not any(m.startswith("UCL 2020") for m in missing)


def test_provider_routing_is_explicit():
    assert provider_for("CL") == "football-data"
    assert provider_for("EL") == "espn"
    assert provider_for("UCL") == "espn"
