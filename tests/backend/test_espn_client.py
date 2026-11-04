"""Hermetic tests for the pure ESPN client/parser (no network)."""
from __future__ import annotations

import httpx
import pytest

from backend.espn_client import ESPNClient, ESPNClientError, ESPNValidationError
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


def _client(competition_id="EL", season_year=2024):
    return FakeESPN(
        P.standings_payload(competition_id, season_year),
        P.el_season_events(season_year) if competition_id == "EL"
        else P.conf_season_events(season_year),
    )


def test_fetch_season_counts_and_stage_mapping():
    data = _client().fetch_season("EL", 2024)
    assert data["competition_id"] == "EL"
    assert data["season_year"] == 2024
    assert data["league_matches"] == 8
    assert len(data["standings"]["teams"]) == 36
    assert len(data["matches"]) == 189
    league = [m for m in data["matches"] if m["stage"] == "LEAGUE_STAGE"]
    assert len(league) == 144
    final = [m for m in data["matches"] if m["stage"] == "KNOCKOUT" and m["round"] == "Final"]
    assert len(final) == 1
    playoff = [m for m in data["matches"] if m["stage"] == "KNOCKOUT_PLAYOFF"]
    assert len(playoff) == 16
    assert all(m["stage"] != "LEAGUE_STAGE" or m["round"] is None for m in data["matches"])


def test_conference_counts():
    data = _client("UCL", 2024).fetch_season("UCL", 2024)
    assert data["league_matches"] == 6
    assert len(data["matches"]) == 153
    assert len([m for m in data["matches"] if m["stage"] == "LEAGUE_STAGE"]) == 108


def test_standings_stat_mapping():
    payload = P.standings_payload("EL", 2024)
    client = FakeESPN(payload, [])
    teams = client._parse_standings("EL", 2024, payload)["teams"]
    first = teams[0]
    assert first["externalTeamId"] == "1"
    assert first["position"] == 1
    assert first["played"] == 8
    assert first["won"] == 4
    assert first["draw"] == 2
    assert first["lost"] == 2
    assert first["goalsFor"] == 12
    assert first["goalsAgainst"] == 9
    assert first["goalDifference"] == 3
    assert first["points"] == 14
    assert first["crest"].endswith("1.png")


def test_standings_name_mismatch_rejected():
    payload = P.standings_payload("EL", 2024)
    payload["name"] = "UEFA Champions League"
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("EL", 2024, payload)


def test_standings_season_mismatch_rejected():
    payload = P.standings_payload("EL", 2024)
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("EL", 2025, payload)


def test_standings_team_count_enforced():
    payload = P.standings_payload("EL", 2024, teams=30)
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("EL", 2024, payload)


def test_unknown_stage_rejected():
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "mystery-stage", 1, 2, 1, 0)]
    with pytest.raises(ESPNValidationError):
        FakeESPN(P.standings_payload("EL", 2024), events).fetch_season("EL", 2024)


def test_missing_event_id_rejected():
    events = [P.event(None, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)]
    with pytest.raises(ESPNValidationError):
        FakeESPN(P.standings_payload("EL", 2024), events).fetch_season("EL", 2024)


def test_negative_score_rejected():
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, -1, 0)]
    with pytest.raises(ESPNValidationError):
        FakeESPN({}, [])._parse_matches("EL", 2024, events)


def test_same_team_fixture_rejected():
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 1, 1, 0)]
    with pytest.raises(ESPNValidationError):
        FakeESPN({}, [])._parse_matches("EL", 2024, events)


def test_fixture_outside_season_rejected():
    events = [P.event(1, "2023-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)]
    with pytest.raises(ESPNValidationError):
        FakeESPN({}, [])._parse_matches("EL", 2024, events)


def test_standings_wdl_inconsistency_rejected():
    payload = P.standings_payload("EL", 2024)
    for stat in payload["children"][0]["standings"]["entries"][0]["stats"]:
        if stat["name"] == "wins":
            stat["value"] = 5  # now W+D+L != played
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("EL", 2024, payload)


def test_standings_points_inconsistency_rejected():
    payload = P.standings_payload("EL", 2024)
    for stat in payload["children"][0]["standings"]["entries"][0]["stats"]:
        if stat["name"] == "points":
            stat["value"] = 99
    with pytest.raises(ESPNValidationError):
        FakeESPN(payload, [])._parse_standings("EL", 2024, payload)


def test_qualifying_stage_skipped_with_diagnostic():
    events = [
        P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0),
        P.event(2, "2024-07-20T20:00Z", 2024, "qualifying-first-round", 3, 4, 2, 0),
    ]
    data = FakeESPN(P.standings_payload("EL", 2024), events).fetch_season("EL", 2024)
    assert [m["externalEventId"] for m in data["matches"]] == ["1"]
    assert any("qualifying" in d for d in data["diagnostics"])


def test_completed_match_without_score_rejected():
    events = [P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, None, None)]
    with pytest.raises(ESPNValidationError):
        FakeESPN(P.standings_payload("EL", 2024), events)._parse_matches("EL", 2024, events)


def test_future_placeholder_team_skipped_with_diagnostic():
    events = [
        P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0),
        P.event(2, "2024-10-01T20:00Z", 2024, "league-phase", 3, 4, None, None,
                status_name="STATUS_SCHEDULED", status_state="pre", teams_resolved=False),
    ]
    client = FakeESPN(P.standings_payload("EL", 2024), events)
    matches, diagnostics = client._parse_matches("EL", 2024, events)
    assert len(matches) == 1
    assert any("placeholder" in d for d in diagnostics)


def test_season_year_filter_and_dedup():
    # Same event listed by both calendar pulls; a foreign-season event must drop.
    base = P.event(1, "2024-09-20T20:00Z", 2024, "league-phase", 1, 2, 1, 0)
    foreign = P.event(2, "2024-05-20T20:00Z", 2023, "league-phase", 3, 4, 1, 0)

    class Dup(FakeESPN):
        def _get_scoreboard(self, slug, year_arg):
            return P.scoreboard_payload([base, base, foreign], slug=slug)

    data = Dup(P.standings_payload("EL", 2024), []).fetch_season("EL", 2024)
    assert len(data["matches"]) == 1
    assert data["matches"][0]["externalEventId"] == "1"


def test_status_mapping_variants():
    client = FakeESPN({}, [])
    assert client._map_status({"type": {"name": "STATUS_FULL_TIME", "state": "post"}}) == "FINISHED"
    assert client._map_status({"type": {"name": "STATUS_FINAL_PEN", "state": "post"}}) == "FINISHED"
    assert client._map_status({"type": {"name": "STATUS_FINAL_AET", "state": "post"}}) == "FINISHED"
    assert client._map_status({"type": {"name": "STATUS_SCHEDULED", "state": "pre"}}) == "SCHEDULED"
    assert client._map_status({"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}}) == "IN_PLAY"
    assert client._map_status({"type": {"name": "STATUS_POSTPONED", "state": "post"}}) == "POSTPONED"


def test_unknown_status_rejected():
    with pytest.raises(ESPNValidationError):
        FakeESPN({}, [])._map_status({"type": {"name": "STATUS_WEIRD", "state": "wat"}})


def test_season_url_recipe():
    seen = []

    class Recorder(ESPNClient):
        def _request(self, url):
            seen.append(url)
            if "/standings" in url:
                return P.standings_payload("EL", 2024)
            return P.scoreboard_payload([], slug="uefa.europa")

    Recorder(cache=None).fetch_season("EL", 2024)
    assert any("standings?season=2024" in u for u in seen)
    assert any("scoreboard?dates=2024&limit=1000" in u for u in seen)
    assert any("scoreboard?dates=2025&limit=1000" in u for u in seen)
    assert all("-" not in u.split("dates=")[1].split("&")[0] for u in seen if "dates=" in u)


def test_http_failure_raises_client_error():
    def handler(request):
        return httpx.Response(400, json={"code": 400, "message": "Failed"})

    client = ESPNClient(cache=None, min_request_interval=0.5, max_retries=1)
    client.headers = dict(client.headers)
    transport = httpx.MockTransport(handler)
    real_client_factory = httpx.Client

    def fake_client(**kwargs):
        kwargs["transport"] = transport
        return real_client_factory(**kwargs)

    import backend.espn_client as mod
    original = mod.httpx.Client
    mod.httpx.Client = fake_client
    try:
        with pytest.raises(ESPNClientError):
            client._request("https://example.test/espn")
    finally:
        mod.httpx.Client = original


def test_league_slug_required():
    with pytest.raises(ValueError):
        ESPNClient(cache=None).fetch_season("CL", 2024)
