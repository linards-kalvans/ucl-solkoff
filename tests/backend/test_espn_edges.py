"""Independent edge-regression tests for the ESPN provider.

These deliberately go beyond the primary ``test_espn_client``/``test_espn_service``
suites and target gaps that materially threaten the ESPN acceptance criteria:

* HTTP retry/cache behaviour around transient errors and malformed payloads;
* authoritative league/endpoint identity and conflicting duplicate event IDs;
* per-team standings goals/points persistence (not merely ``played`` counts);
* stable already-canonical identity mapping and untrusted alias ambiguity;
* legacy fixture adoption across competitions/seasons;
* a mapped event whose teams/season changed must not overwrite the wrong row;
* transaction rollback on a mid-write failure;
* explicit "ESPN is never used for CL" routing.

All HTTP is exercised with ``httpx.MockTransport`` (no network); the database is
always a fresh temporary DuckDB file, never the production database.
"""
from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

import backend.espn_client as espn_client
from backend.api_cache import APICache
from backend.competitions import espn_slug_for, provider_for
from backend.data_service import DataService
from backend.database import Database
from backend.espn_client import ESPNClient, ESPNClientError, ESPNValidationError
from backend.espn_service import ESPNDataService, ESPNIdentityError
from tests.backend import espn_payloads as P


class FakeESPN(ESPNClient):
    """Offline client returning injected standings/scoreboard payloads."""

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
    database = Database(db_path=str(tmp_path / "espn_edges.db"))
    yield database
    database.close()


def count(db, table, where="", params=()):
    row = db.fetchone(f"SELECT COUNT(*) FROM {table} {where}", params)
    return row[0] if row else 0


_PRISTINE_HTTPX_CLIENT = httpx.Client


def _install_mock_transport(monkeypatch, handler):
    """Route ``backend.espn_client``'s httpx.Client through a MockTransport.

    Also neutralises the politeness sleep (clamped to >=0.5s) so retry loops do
    not slow the suite; retry *counts* are still asserted. The pristine client
    class is captured at import so repeated installs never wrap each other.
    """
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _PRISTINE_HTTPX_CLIENT(*args, **kwargs)

    monkeypatch.setattr(espn_client.httpx, "Client", factory)
    monkeypatch.setattr(espn_client.time, "sleep", lambda *a, **k: None)


def _insert_teams(db, ids=(1, 2, 3, 4), names=None):
    for idx in ids:
        name = names.get(idx) if names else f"Team {idx}"
        db.execute(
            "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
            (idx, name),
        )


def _insert_match(db, match_id, home, away, competition="EL",
                  stage="LEAGUE_STAGE", round_name=None,
                  date="2024-09-20T20:00Z", status="SCHEDULED"):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, NULL, ?)
        """,
        (match_id, home, away, date, status, stage, round_name, competition),
    )


# ---------------------------------------------------------------------------
# HTTP retry / cache / malformed payloads
# ---------------------------------------------------------------------------

def test_transient_http_errors_retried_then_succeed(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/bad"):
            return httpx.Response(400, json={"error": "bad"})
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=None, max_retries=3, retry_backoff=0.0)

    # Transient 5xx are retried with backoff, then succeed.
    assert client._request("https://example.test/espn") == {"ok": True}
    assert calls["n"] == 3

    # A non-transient 4xx fails after one attempt, not an endless loop.
    with pytest.raises(ESPNClientError):
        client._request("https://example.test/bad")
    assert calls["n"] == 3


def test_exhausted_retries_raise_and_do_not_cache(monkeypatch, db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={"error": "down"})

    cache = APICache(db)
    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=cache, max_retries=2, retry_backoff=0.0)

    with pytest.raises(ESPNClientError):
        client._request("https://example.test/down")
    assert calls["n"] == 2
    assert cache.get("espn:https://example.test/down") is None


def test_non_dict_json_payload_rejected_and_not_cached(monkeypatch, db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=[1, 2, 3])

    cache = APICache(db)
    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=cache, max_retries=1)

    with pytest.raises(ESPNValidationError):
        client._request("https://example.test/list")
    assert cache.get("espn:https://example.test/list") is None

    # A malformed success must not be served from cache on the next call.
    with pytest.raises(ESPNValidationError):
        client._request("https://example.test/list")
    assert calls["n"] == 2


def test_invalid_json_body_is_bounded_client_error_and_not_cached(monkeypatch, db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=b"{not valid json")

    cache = APICache(db)
    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=cache, max_retries=2, retry_backoff=0.0)

    # A truncated/malformed 200 is a source failure; it must surface as the
    # client's bounded error and never be cached as a valid season result.
    with pytest.raises(ESPNClientError):
        client._request("https://example.test/garbage")
    assert cache.get("espn:https://example.test/garbage") is None


def test_cache_hit_avoids_network_and_is_source_namespaced(monkeypatch, db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"ok": 1})

    cache = APICache(db)
    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=cache, max_retries=1)

    first = client._request("https://example.test/cached")
    second = client._request("https://example.test/cached")
    assert first == second == {"ok": 1}
    assert calls["n"] == 1
    # Source-namespaced key, distinct from a bare URL key.
    assert cache.get("espn:https://example.test/cached") == {"ok": 1}
    assert cache.get("https://example.test/cached") is None


def test_rejected_semantic_standings_body_evicted_and_not_reserved(monkeypatch, db):
    """A cached body rejected on *semantics* must not be re-served from cache."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if "/standings" in request.url.path:
            bad = P.standings_payload("EL", 2024)
            bad["name"] = "UEFA Champions League"  # foreign league
            return httpx.Response(200, json=bad)
        return httpx.Response(200, json=P.scoreboard_payload([], slug="uefa.europa"))

    cache = APICache(db)
    _install_mock_transport(monkeypatch, handler)
    client = ESPNClient(cache=cache, max_retries=1)

    standings_url = (
        "https://site.api.espn.com/apis/v2/sports/soccer/"
        "uefa.europa/standings?season=2024"
    )
    # An unrelated provider/cache entry must survive the season eviction.
    cache.set("espn:https://example.test/unrelated", {"ok": 1})

    with pytest.raises(ESPNValidationError):
        client.fetch_season("EL", 2024)
    assert cache.get(f"espn:{standings_url}") is None
    assert cache.get("espn:https://example.test/unrelated") == {"ok": 1}

    # Second attempt must hit the network again (not a poisoned cache hit) and
    # reject again rather than silently "succeed" from the cached body.
    with pytest.raises(ESPNValidationError):
        client.fetch_season("EL", 2024)
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Authoritative league / endpoint identity, duplicate event IDs
# ---------------------------------------------------------------------------

def test_ucl_endpoints_use_conference_slug_and_expected_bases():
    seen = []

    class Recorder(ESPNClient):
        def _request(self, url):
            seen.append(url)
            if "/standings" in url:
                return P.standings_payload("UCL", 2024)
            return P.scoreboard_payload([], slug="uefa.europa.conf")

    Recorder(cache=None).fetch_season("UCL", 2024)
    standings = [u for u in seen if "/standings" in u]
    scoreboards = [u for u in seen if "/scoreboard" in u]
    assert standings == [
        "https://site.api.espn.com/apis/v2/sports/soccer/"
        "uefa.europa.conf/standings?season=2024"
    ]
    assert scoreboards == [
        "https://site.api.espn.com/apis/site/v2/sports/soccer/"
        "uefa.europa.conf/scoreboard?dates=2024&limit=1000",
        "https://site.api.espn.com/apis/site/v2/sports/soccer/"
        "uefa.europa.conf/scoreboard?dates=2025&limit=1000",
    ]


def test_ucl_standings_rejects_foreign_league_name():
    payload = P.standings_payload("EL", 2024)  # name "UEFA Europa League"
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("UCL", 2024, payload)


def test_conflicting_duplicate_event_ids_rejected():
    first = P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)
    # Same provider event id, different clubs (feed corruption).
    conflicting = P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 3, 4, 1, 0)

    class Split(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            events = [first] if year_arg == 2024 else [conflicting]
            return P.scoreboard_payload(events, slug=slug)

    with pytest.raises(ESPNValidationError):
        Split(P.standings_payload("EL", 2024), []).fetch_season("EL", 2024)


def test_foreign_league_scoreboard_rejected_and_db_unchanged(db):
    """A body naming another competition is refused even with correct years."""

    class Foreign(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            year = str(year_arg)
            events = [e for e in self._events if e["date"][:4] == year]
            # Correct event years/stages, but authoritative metadata is the
            # Conference League while EL was requested.
            return P.scoreboard_payload(events, slug="uefa.europa.conf")

    svc = ESPNDataService(
        db,
        client=Foreign(P.standings_payload("EL", 2024), P.el_season_events(2024)),
    )
    with pytest.raises(ESPNValidationError):
        svc.sync_season("EL", 2024)

    assert count(db, "teams") == 0
    assert count(db, "matches") == 0
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "provider_match_mappings") == 0


def test_scoreboard_metadata_season_year_not_tied_to_calendar():
    """Advertised league season.year need not equal the calendar pull year."""
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)]

    class Cross(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            year = str(year_arg)
            evs = [e for e in self._events if e["date"][:4] == year]
            return P.scoreboard_payload(evs, slug=slug, metadata_season_year=2100)

    data = Cross(P.standings_payload("EL", 2024), events).fetch_season("EL", 2024)
    assert [m["externalEventId"] for m in data["matches"]] == ["1"]


def test_scoreboard_missing_events_list_rejected():
    """A body lacking ``events`` is a schema failure, never an empty schedule."""

    class NoEvents(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            return {"leagues": [{"slug": slug}], "events": None}

    with pytest.raises(ESPNValidationError):
        NoEvents(P.standings_payload("EL", 2024), []).fetch_season("EL", 2024)


def test_scoreboard_missing_league_metadata_rejected():
    """Authoritative league metadata is required, not merely optional."""

    class NoLeagues(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            return {"events": []}

    with pytest.raises(ESPNValidationError):
        NoLeagues(P.standings_payload("EL", 2024), []).fetch_season("EL", 2024)


# ---------------------------------------------------------------------------
# Standings goals/points (not merely played counts)
# ---------------------------------------------------------------------------

def test_current_standings_persist_espn_goals_and_points(db, monkeypatch):
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    ESPNDataService(db, client=el_client(2024)).sync_season("EL", 2024)

    row = db.fetchone(
        """
        SELECT s.played, s.won, s.drawn, s.lost, s.goals_for, s.goals_against,
               s.goal_difference, s.points, s.position
        FROM standings s
        JOIN provider_team_mappings m ON s.team_id = m.team_id
        WHERE s.competition_id = 'EL' AND m.provider = 'espn' AND m.external_id = '1'
        """
    )
    # These are the ESPN statistics, not values reconstructed from the stored
    # 2-1 fixtures (which would yield a different points/goals profile).
    assert row == (8, 4, 2, 2, 12, 9, 3, 14, 1)


# ---------------------------------------------------------------------------
# Canonical identity stability / alias ambiguity
# ---------------------------------------------------------------------------

def test_existing_provider_mapping_identity_is_stable(db):
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (77, 'Original FC', NULL, NULL)")
    db.execute(
        "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
        "VALUES ('espn', '1', 77)"
    )
    # A canonical club that would otherwise match the ESPN display name.
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (5, 'ESPN Club 01', NULL, NULL)")
    db.commit()

    ESPNDataService(db, client=el_client(2024)).sync_season("EL", 2024)

    mapped = db.fetchone(
        "SELECT team_id FROM provider_team_mappings WHERE provider='espn' AND external_id='1'"
    )
    assert mapped[0] == 77
    assert count(
        db, "provider_team_mappings", "WHERE provider='espn' AND external_id='1'"
    ) == 1


def test_no_implicit_club_suffix_merge(db):
    # "United" must not be stripped to fold this into "ESPN Club 01".
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (20, 'ESPN Club 01 United', NULL, NULL)"
    )
    db.commit()

    ESPNDataService(db, client=el_client(2024)).sync_season("EL", 2024)

    mapped = db.fetchone(
        "SELECT team_id FROM provider_team_mappings WHERE provider='espn' AND external_id='1'"
    )
    assert mapped[0] != 20
    assert count(db, "teams", "WHERE id = 20") == 1


def test_alias_to_ambiguous_canonical_raises(db):
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (1, 'Foo Bar', NULL, NULL)")
    db.execute("INSERT INTO teams (id, name, code, crest) VALUES (2, 'foo-bar', NULL, NULL)")
    db.commit()

    standings = P.standings_payload("EL", 2024)
    standings["children"][0]["standings"]["entries"][0]["team"]["displayName"] = "Monaco"
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)]
    svc = ESPNDataService(
        db,
        client=FakeESPN(standings, events),
        aliases={"monaco": "Foo Bar"},
    )

    with pytest.raises(ESPNIdentityError):
        svc._persist(svc.client.fetch_season("EL", 2024))
    assert count(db, "provider_team_mappings") == 0


# ---------------------------------------------------------------------------
# Legacy fixture adoption / source collisions
# ---------------------------------------------------------------------------

def test_legacy_fixture_in_other_competition_not_adopted(db):
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
    cl_id = -321
    _insert_match(
        db, cl_id, sides["home"], sides["away"],
        competition="CL", date=first["date"],
    )
    db.commit()

    ESPNDataService(db, client=el_client(2024, events=events)).sync_season("EL", 2024)

    mapped = db.fetchone(
        "SELECT match_id FROM provider_match_mappings "
        "WHERE provider='espn' AND external_id=?",
        (first["id"],),
    )
    assert mapped[0] != cl_id
    # The CL fixture is untouched and a distinct EL row was created.
    assert db.fetchone(
        "SELECT competition_id, home_score FROM matches WHERE id = ?", (cl_id,)
    ) == ("CL", None)
    assert count(db, "matches", "WHERE competition_id = 'EL'") == 189


def test_legacy_fixture_outside_season_window_not_adopted(db):
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
    # Same clubs/stage but a date in the previous season: a different leg.
    legacy_id = -322
    _insert_match(
        db, legacy_id, sides["home"], sides["away"],
        date="2023-09-20T20:00Z",
    )
    db.commit()

    ESPNDataService(db, client=el_client(2024, events=events)).sync_season("EL", 2024)

    mapped = db.fetchone(
        "SELECT match_id FROM provider_match_mappings "
        "WHERE provider='espn' AND external_id=?",
        (first["id"],),
    )
    assert mapped[0] != legacy_id
    assert db.fetchone(
        "SELECT \"date\" FROM matches WHERE id = ?", (legacy_id,)
    ) == ("2023-09-20T20:00Z",)


# ---------------------------------------------------------------------------
# Mapped event changed teams / season
# ---------------------------------------------------------------------------

def test_mapped_event_with_changed_teams_refused(db):
    _insert_teams(db, (1, 2, 3, 4))
    _insert_match(db, -500, 1, 2, stage="LEAGUE_STAGE", round_name=None)
    db.execute(
        "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
        "VALUES ('espn', 'E', -500)"
    )
    db.commit()

    svc = ESPNDataService(db, client=FakeESPN({}, []))
    changed = {
        "externalEventId": "E",
        "date": "2025-01-01T20:00Z",
        "status": "FINISHED",
        "stage": "LEAGUE_STAGE",
        "round": None,
        "leg": None,
        "home": {"externalTeamId": "3"},
        "away": {"externalTeamId": "4"},
        "homeScore": 2,
        "awayScore": 1,
    }

    # The provider reused event "E" for a different pairing; overwriting the
    # mapped row would attach 3-4's scoreline to the 1-2 record.
    with pytest.raises(ESPNIdentityError):
        svc._resolve_match(changed, {"3": 3, "4": 4}, "EL", 2024)
    assert db.fetchone(
        "SELECT home_team_id, away_team_id, home_score FROM matches WHERE id = -500"
    ) == (1, 2, None)


def test_mapped_event_changed_season_refused(db):
    _insert_teams(db, (1, 2))
    _insert_match(db, -501, 1, 2, date="2024-09-20T20:00Z")
    db.execute(
        "INSERT INTO provider_match_mappings (provider, external_id, match_id) "
        "VALUES ('espn', 'F', -501)"
    )
    db.commit()

    svc = ESPNDataService(db, client=FakeESPN({}, []))
    moved = {
        "externalEventId": "F",
        "date": "2025-09-20T20:00Z",
        "status": "SCHEDULED",
        "stage": "LEAGUE_STAGE",
        "round": None,
        "leg": None,
        "home": {"externalTeamId": "1"},
        "away": {"externalTeamId": "2"},
        "homeScore": None,
        "awayScore": None,
    }

    # A mapped 2024/25 row must not be relabelled with a following-season date.
    with pytest.raises(ESPNIdentityError):
        svc._resolve_match(moved, {"1": 1, "2": 2}, "EL", 2025)
    assert db.fetchone(
        'SELECT "date" FROM matches WHERE id = -501'
    ) == ("2024-09-20T20:00Z",)


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------

def test_rollback_on_standings_write_failure(db, monkeypatch):
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)
    svc = ESPNDataService(db, client=el_client(2024))

    def boom(*args, **kwargs):
        raise RuntimeError("standings write failed")

    monkeypatch.setattr(svc, "_replace_standings", boom)

    with pytest.raises(RuntimeError):
        svc.sync_season("EL", 2024)

    assert count(db, "teams") == 0
    assert count(db, "matches") == 0
    assert count(db, "provider_team_mappings") == 0
    assert count(db, "provider_match_mappings") == 0
    assert count(db, "standings") == 0


# ---------------------------------------------------------------------------
# ESPN is never used for CL
# ---------------------------------------------------------------------------

def test_cl_sync_never_constructs_espn_service(db):
    ds = DataService(db, Mock())
    ds.sync_teams = Mock()
    ds.sync_matches = Mock()
    ds.sync_knockout_matches = Mock()
    ds.sync_standings = Mock()

    ds.sync_all("CL")

    assert provider_for("CL") == "football-data"
    assert ds._espn_service is None
    ds.sync_teams.assert_called_once_with("CL")


def test_cl_never_hits_espn_endpoints():
    assert espn_slug_for("EL") == "uefa.europa"

    with pytest.raises(ValueError):
        espn_slug_for("CL")

    class Boom(ESPNClient):
        def _request(self, url):  # pragma: no cover - must never run
            raise AssertionError("ESPN endpoint requested for CL")

    with pytest.raises(ValueError):
        Boom(cache=None).fetch_season("CL", 2024)
