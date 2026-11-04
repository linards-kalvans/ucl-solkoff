"""Shared Playwright fixtures for the UCL Strength Lab frontend.

The suite serves the *production* ``frontend/`` directory through a tiny
threaded static HTTP server (never the FastAPI app / lifespan) and intercepts
every ``/api/`` request with deterministic in-memory fixtures. External traffic
(real crest hosts, analytics, ...) is aborted so the tests are hermetic: no
network, no API keys, no database writes.

Only user-visible behaviour is asserted. Fixtures mirror the real backend
schemas taken from ``backend/main.py`` and ``backend/playoff_analyzer.py``.
"""
from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import sync_playwright

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


# --------------------------------------------------------------------------- #
# Static file server
# --------------------------------------------------------------------------- #
class _StaticHandler(SimpleHTTPRequestHandler):
    """Serve ``frontend/`` as the site root (index.html at ``/``)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, fmt, *args):  # keep test output clean
        pass


@pytest.fixture(scope="session")
def static_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# Deterministic standings fixture
# --------------------------------------------------------------------------- #
# Positions are official and deliberately differ from the strength order so the
# suite can prove badges/normalisation use official position and the *complete*
# dataset, not the visible/filtered subset.
_SPECIALS = {
    1: ("Gamma Pinnacle", "PIN", 21, 1.05, 50.0),
    2: ("Delta Rovers", "DEL", 19, 1.20, 60.0),
    3: ("Epsilon City", "EPS", 18, 1.15, 55.0),
    4: ("Zeta Wanderers", "ZET", 16, 1.30, 70.0),
    5: ("Alpha Strength FC", "STR", 15, 1.10, 99.9),   # strength leader
    6: ("Eta Sporting", "ETA", 14, 1.25, 65.0),
    7: ("Iota Athletic", "IOT", 13, 1.35, 75.0),
    8: ("Beta Schedule FC", "SCH", 12, 1.90, 40.0),    # solkoff leader
    20: ("Omega Playoff", "OMG", 10, 1.18, 90.0),     # high strength, low position
}


def _build_standings():
    rows = []
    for position in range(1, 27):
        if position in _SPECIALS:
            name, code, points, solkoff, strength = _SPECIALS[position]
        else:
            name, code = f"Filler {position:02d}", f"F{position:02d}"
            points = max(1, 12 - (position - 8))
            solkoff = round(1.0 + (position % 5) * 0.05, 3)
            strength = round(points * solkoff, 3)

        won = min(8, points // 3 + 1)
        drawn = max(0, min(8 - won, points - won * 3))
        lost = 8 - won - drawn
        crest = f"https://crest.example/{code.lower()}.png"

        rows.append({
            "teamId": position,
            "teamName": name,
            "teamCode": code,
            "teamCrest": crest,
            "position": position,
            "played": 8,
            "won": won,
            "drawn": drawn,
            "lost": lost,
            "gf": 10 + (position % 7),
            "ga": 5 + (position % 9),
            "gd": (10 + (position % 7)) - (5 + (position % 9)),
            "points": points,
            "solkoffCoefficient": solkoff,
            "strengthScore": strength,
        })

    # Variants for fallback / safety assertions.
    rows[0]["teamCrest"] = None                                  # missing crest
    rows[8]["teamCrest"] = "https://crest.example/broken.png"    # broken image
    rows[4]["teamCrest"] = "javascript:alert(1)"                 # unsafe scheme
    return rows


ALL_STANDINGS = _build_standings()


# --------------------------------------------------------------------------- #
# Pair / analysis builders (real backend schema)
# --------------------------------------------------------------------------- #
def _default_prob(method="elo"):
    if method == "elo":
        return {
            "team1Win": 0.62,
            "team2Win": 0.38,
            "draw": 0.0,
            "method": "elo",
            "team1EloRating": 1800,
            "team2EloRating": 1650,
            "team1MatchesPlayed": 42,
            "team2MatchesPlayed": 40,
        }
    if method == "no_data":
        return {"team1Win": 0.5, "team2Win": 0.5, "draw": 0.0, "method": "no_data"}
    return {"team1Win": 0.6, "team2Win": 0.4, "draw": 0.0, "method": method}


def make_pair(match_id, team1, team2, stage="ROUND_OF_16", status="SCHEDULED",
              date="2026-03-05T20:00:00Z", win_probability=True, method="elo"):
    """team1/team2 are ``(id, name, crest)`` tuples."""
    pair = {
        "matchId": match_id,
        "team1": {"id": team1[0], "name": team1[1], "crest": team1[2]},
        "team2": {"id": team2[0], "name": team2[1], "crest": team2[2]},
        "matchday": 9,
        "stage": {
            "KNOCKOUT_PLAYOFF": "Knockout Round Play-off",
            "ROUND_OF_16": "Round of 16",
            "QUARTER_FINAL": "Quarter-finals",
            "SEMI_FINAL": "Semi-finals",
            "FINAL": "Final",
        }.get(stage, stage),
        "status": status,
        "date": date,
        "apiStage": stage,
        "apiRound": stage,
    }
    if win_probability:
        pair["winProbability"] = _default_prob(method)
    return pair


def _team_stats(team_id, name, crest, opponent_id, opponent_name):
    return {
        "teamId": team_id,
        "teamName": name,
        "teamCrest": crest,
        "played": 6,
        "won": 4,
        "drawn": 1,
        "lost": 1,
        "goalsFor": 12,
        "goalsAgainst": 6,
        "goalDifference": 6,
        "points": 13,
        "pointsPercentage": 72.2,
        "solkoffCoefficient": 1.2,
        "strengthPerGame": 0.87,
        "matches": [
            {
                "opponentId": opponent_id,
                "opponentName": opponent_name,
                "opponentCrest": None,
                "teamScore": 2,
                "opponentScore": 1,
                "outcome": "win",
                "date": "2025-11-01",
                "isHome": True,
            },
            {
                "opponentId": 99,
                "opponentName": "Common Opponent",
                "opponentCrest": None,
                "teamScore": 1,
                "opponentScore": 1,
                "outcome": "draw",
                "date": "2025-10-15",
                "isHome": False,
            },
        ],
        "notableWins": [
            {"date": "2025-11-01", "opponent": opponent_name, "score": "2-1"},
            {"date": "2025-10-01", "opponent": "Minnow FC", "score": "4-0"},
        ],
    }


def make_analysis(team1, team2, method="elo"):
    """Full nested analysis matching ``PlayoffAnalyzer.analyze_pair`` output."""
    stats1 = _team_stats(team1[0], team1[1], team1[2], team2[0], team2[1])
    stats2 = _team_stats(team2[0], team2[1], team2[2], team1[0], team1[1])
    if method == "elo":
        win_prob = {
            "team1Win": 0.62,
            "team2Win": 0.38,
            "draw": 0.0,
            "method": "elo",
            "team1EloRating": 1800,
            "team2EloRating": 1650,
            "team1MatchesPlayed": 42,
            "team2MatchesPlayed": 40,
            "team1Stats": stats1,
            "team2Stats": stats2,
        }
    else:
        win_prob = {
            "team1Win": 0.6,
            "team2Win": 0.4,
            "draw": 0.0,
            "method": method,
            "team1MainStrength": 1.1,
            "team2MainStrength": 0.9,
            "team1HistoricalStrength": 1.0,
            "team2HistoricalStrength": 0.8,
        }
    return {
        "team1": {"id": team1[0], "name": team1[1], "crest": team1[2]},
        "team2": {"id": team2[0], "name": team2[1], "crest": team2[2]},
        "commonOpponentsCount": 2,
        "historicalYears": 10,
        "leagueTable": {
            "team1": stats1,
            "team2": stats2,
            "commonOpponents": [
                {"id": 99, "name": "Common Opponent", "crest": None},
                {"id": 98, "name": "Other Opponent", "crest": None},
            ],
            "fullLeagueTable": [stats1, stats2,
                {
                    "teamId": 99, "teamName": "Common Opponent", "teamCrest": None,
                    "played": 4, "won": 1, "drawn": 1, "lost": 2,
                    "goalsFor": 4, "goalsAgainst": 6, "goalDifference": -2,
                    "points": 4, "pointsPercentage": 33.3,
                    "solkoffCoefficient": 1.0, "strengthPerGame": 0.33,
                    "matches": [],
                },
            ],
            "winProbability": win_prob,
        },
    }


def make_solkoff_details(team_id, name, code, crest):
    return {
        "teamId": team_id,
        "teamName": name,
        "teamCode": code,
        "teamCrest": crest,
        "solkoffCoefficient": 1.1,
        "opponents": [
            {
                "teamId": 99,
                "teamName": "Opponent United",
                "teamCrest": None,
                "points": 9,
                "played": 3,
                "pointsPerGame": 3.0,
                "matchesPlayed": 1,
                "matches": [
                    {
                        "homeScore": 2, "awayScore": 1, "teamScore": 2,
                        "opponentScore": 1, "outcome": "win",
                        "date": "2025-11-01", "isHome": True,
                    }
                ],
            }
        ],
        "averageOpponentPPG": 3.0,
        "matchesCount": 1,
        "opponentsCount": 1,
    }


# --------------------------------------------------------------------------- #
# API router & browser fixtures
# --------------------------------------------------------------------------- #
class ApiRouter:
    """Deterministic replacement for every backend endpoint used by app.js."""

    def __init__(self):
        self.standings = deepcopy(ALL_STANDINGS)
        self.standings_status = 200
        self.standings_sequence = None
        self._standings_calls = 0

        self.stage = "ROUND_OF_16"
        self.stage_status = 200

        self.pairs = {}
        self.pairs_status = {}
        self.pairs_sequence = {}
        self._pairs_calls = defaultdict(int)

        self.analysis = {}
        self.analysis_status = {}
        self.analysis_sequence = {}
        self._analysis_calls = defaultdict(int)

        self.solkoff = {}
        self.solkoff_status = {}

        self.refresh_status = 200
        self.refresh_sequence = None
        self._refresh_calls = 0

        # Season switching: the app resolves seasons first and appends the
        # selected year to season-dependent GETs. ``season_log`` records the
        # requested year so tests can assert scoping; ``standings_by_season`` /
        # ``pairs_by_season`` let a test vary a view per season.
        self.seasons = {
            "currentSeason": 2026,
            "seasons": [
                {"year": 2026, "label": "2026/27"},
                {"year": 2025, "label": "2025/26"},
                {"year": 2024, "label": "2024/25"},
            ],
        }
        self.seasons_status = 200
        self.season_log = []  # (method, path, season_param or None)
        self.standings_by_season = {}
        self.standings_status_by_season = {}
        self.pairs_by_season = {}

        # Competition metadata + optional per-competition payloads. Legacy tests
        # never set these, so the default fixtures above keep serving unchanged.
        self.competitions = {
            "default": "CL",
            "competitions": [
                {"code": "CL", "label": "Champions League", "leagueMatches": 8, "hasCurrentData": True},
                {"code": "EL", "label": "Europa League", "leagueMatches": 8, "hasCurrentData": False},
                {"code": "UCL", "label": "Conference League", "leagueMatches": 6, "hasCurrentData": False},
            ],
        }
        self.competitions_status = 200
        self.competition_log = []  # (method, path, competition_param or None)
        self.standings_by_competition = {}
        self.pairs_by_competition = {}
        self.seasons_by_competition = {}

        self.counts = defaultdict(int)
        self.log = []
        self._hold = []
        self._held_routes = []

    # -- configuration ----------------------------------------------------- #
    def set_standings(self, data, status=200):
        self.standings = deepcopy(data)
        self.standings_status = status

    def set_stage(self, stage, status=200):
        self.stage = stage
        self.stage_status = status

    def set_pairs(self, stage, data, status=200):
        self.pairs[stage.upper()] = deepcopy(data)
        self.pairs_status[stage.upper()] = status

    def set_analysis(self, team1_id, team2_id, data, status=200):
        self.analysis[(int(team1_id), int(team2_id))] = deepcopy(data)
        self.analysis_status[(int(team1_id), int(team2_id))] = status

    def set_solkoff(self, team_id, data, status=200):
        self.solkoff[int(team_id)] = deepcopy(data)
        self.solkoff_status[int(team_id)] = status

    def set_refresh(self, status=200):
        self.refresh_status = status

    def set_seasons(self, data, status=200):
        self.seasons = deepcopy(data)
        self.seasons_status = status

    def set_competitions(self, data, status=200):
        self.competitions = deepcopy(data)
        self.competitions_status = status

    def set_standings_for_competition(self, code, data):
        self.standings_by_competition[code.upper()] = deepcopy(data)

    def set_pairs_for_competition(self, code, stage, data):
        self.pairs_by_competition[(code.upper(), stage.upper())] = deepcopy(data)

    def set_seasons_for_competition(self, code, data):
        self.seasons_by_competition[code.upper()] = deepcopy(data)

    def set_standings_for_season(self, year, data):
        self.standings_by_season[int(year)] = deepcopy(data)

    def set_standings_status_for_season(self, year, status):
        self.standings_status_by_season[int(year)] = status

    def set_pairs_for_season(self, year, stage, data):
        self.pairs_by_season[(int(year), stage.upper())] = deepcopy(data)

    def hold_path(self, substring):
        """Hold the next request whose path contains ``substring``.

        Held requests are only fulfilled when :meth:`release_held` is called,
        which lets tests simulate slow / out-of-order responses without blocking
        the Playwright sync event loop.
        """
        self._hold.append(substring)

    def release_held(self):
        """Fulfil every held request with the response snapshotted at request time.

        Snapshotting matters: fulfilling at release time would hand an old
        in-flight request whatever fixture is configured *now*, defeating stale
        response / cache-invalidation tests.
        """
        held, self._held_routes = self._held_routes, []
        for route, status, body in held:
            self._fulfill(route, status, body)

    @property
    def held_count(self):
        return len(self._held_routes)

    # -- Playwright route callback ----------------------------------------- #
    def route_request(self, route, *args):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        method = request.method.upper()
        season = parse_qs(parsed.query).get("season", [None])[0]
        competition = parse_qs(parsed.query).get("competition", [None])[0]
        self.counts[f"{method} {path}"] += 1
        self.log.append((method, path))
        self.season_log.append((method, path, season))
        self.competition_log.append((method, path, competition))

        for sub in list(self._hold):
            if sub in path:
                self._hold.remove(sub)
                # Snapshot the response now so a late release cannot pick up a
                # newer fixture (which would mask stale-response bugs).
                status, body = self._response(method, path, season, competition)
                self._held_routes.append((route, status, body))
                return

        status, body = self._response(method, path, season, competition)
        self._fulfill(route, status, body)

    # -- response selection ------------------------------------------------- #
    def _take(self, sequence, index):
        if not sequence:
            return None
        index = min(index, len(sequence) - 1)
        return sequence[index]

    def _season_year(self, season):
        if season is None or not season.isdigit():
            return None
        return int(season)

    def _competition_code(self, competition):
        return competition.upper() if competition else None

    def _response(self, method, path, season=None, competition=None):
        code = self._competition_code(competition)

        if path == "/api/competitions" and method == "GET":
            return self.competitions_status, self.competitions

        if path == "/api/seasons" and method == "GET":
            if code is not None and code in self.seasons_by_competition:
                return 200, self.seasons_by_competition[code]
            return self.seasons_status, self.seasons

        if path == "/api/standings" and method == "GET":
            if code is not None and code in self.standings_by_competition:
                return 200, self.standings_by_competition[code]
            year = self._season_year(season)
            if year is not None and year in self.standings_status_by_season:
                status = self.standings_status_by_season[year]
                body = self.standings_by_season.get(year, self.standings)
                return status, body
            if year is not None and year in self.standings_by_season:
                return 200, self.standings_by_season[year]
            self._standings_calls += 1
            seq = self._take(self.standings_sequence, self._standings_calls - 1)
            if seq is not None:
                return seq if isinstance(seq, tuple) else (200, seq)
            return self.standings_status, self.standings

        if path == "/api/tournament/current-stage" and method == "GET":
            return self.stage_status, {"stage": self.stage}

        match = re.match(r"^/api/knockout-pairs/([A-Za-z0-9_]+)$", path)
        if match:
            stage = match.group(1).upper()
            if code is not None and (code, stage) in self.pairs_by_competition:
                return 200, self.pairs_by_competition[(code, stage)]
            year = self._season_year(season)
            if year is not None and (year, stage) in self.pairs_by_season:
                return 200, self.pairs_by_season[(year, stage)]
            self._pairs_calls[stage] += 1
            seq = self._take(self.pairs_sequence.get(stage), self._pairs_calls[stage] - 1)
            if seq is not None:
                return seq if isinstance(seq, tuple) else (200, seq)
            return self.pairs_status.get(stage, 200), self.pairs.get(stage, [])

        match = re.match(r"^/api/teams/(\d+)/solkoff-details$", path)
        if match:
            team_id = int(match.group(1))
            return self.solkoff_status.get(team_id, 200), self.solkoff.get(team_id, {})

        match = re.match(r"^/api/playoff-pairs/(\d+)/(\d+)/analysis$", path)
        if match:
            key = (int(match.group(1)), int(match.group(2)))
            self._analysis_calls[key] += 1
            seq = self._take(self.analysis_sequence.get(key), self._analysis_calls[key] - 1)
            if seq is not None:
                return seq if isinstance(seq, tuple) else (200, seq)
            if key in self.analysis:
                return self.analysis_status.get(key, 200), self.analysis[key]
            return 404, {"detail": "not found"}

        if path == "/api/refresh" and method == "POST":
            self._refresh_calls += 1
            if self.refresh_sequence:
                seq = self._take(self.refresh_sequence, self._refresh_calls - 1)
                if seq is not None:
                    return seq if isinstance(seq, tuple) else (200, seq)
            return self.refresh_status, {"message": "Data refresh triggered successfully"}

        return 200, {}

    @staticmethod
    def _fulfill(route, status, body):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(body),
        )


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def api():
    return ApiRouter()


@pytest.fixture
def context(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 950})
    ctx.set_default_timeout(4000)
    ctx.set_default_navigation_timeout(10000)
    # Block every non-local URL (crest hosts, fonts, analytics, ...).
    ctx.route(
        re.compile(r"^https?://(?!127\.0\.0\.1|localhost)"),
        lambda route: route.abort(),
    )
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context, api):
    page = context.new_page()
    page.route("**/api/**", api.route_request)
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def open_app(page, static_server):
    """Navigate to the served frontend, optionally with a URL hash."""

    def _open(hash_fragment="", path="/"):
        url = f"{static_server}{path}"
        fragment = (hash_fragment or "").lstrip("#")
        if fragment:
            url += f"#{fragment}"
        page.goto(url, wait_until="domcontentloaded")
        return page

    return _open


# --------------------------------------------------------------------------- #
# Reusable fixture builders / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def standings():
    return deepcopy(ALL_STANDINGS)


@pytest.fixture
def build_pair():
    return make_pair


@pytest.fixture
def build_analysis():
    return make_analysis


@pytest.fixture
def build_solkoff_details():
    return make_solkoff_details


@pytest.fixture
def stage_tab():
    """Locate a Knockout stage tab by its visible label."""

    def _locate(page, label):
        tabs = page.locator("#knockout [role='tab']")
        if tabs.count() == 0:
            tabs = page.locator("[role='tab']")
        # Anchor the pattern so the *full* visible label must match: an
        # unanchored ``final`` would also match ``Quarter-final``/``Semi-final``
        # and silently select whichever stage happens to come first in the DOM.
        pattern = re.compile(rf"^(?:{label})$", re.I)
        return tabs.filter(has_text=pattern).first

    return _locate


@pytest.fixture
def bar_ratio():
    """Return ``bar_width / track_width`` for a progress-bar element."""

    def _ratio(locator):
        return locator.evaluate(
            """el => {
                const track = el.parentElement;
                const r = el.getBoundingClientRect();
                const tr = track.getBoundingClientRect();
                return tr.width ? r.width / tr.width : 0;
            }"""
        )

    return _ratio


@pytest.fixture
def wait_until(page):
    """Condition-driven acknowledgement for held-route races.

    Polls a Python predicate while pumping Playwright events (``wait_for_timeout``)
    so intercepted requests are actually dispatched. Raises if the condition is
    never met; this replaces blind fixed sleeps.
    """

    def _wait(predicate, timeout=5000, interval=25):
        page.wait_for_timeout(0)
        deadline = timeout
        while deadline > 0:
            if predicate():
                return True
            page.wait_for_timeout(interval)
            deadline -= interval
        raise AssertionError(
            "condition not met within %dms" % timeout
        )

    return _wait
