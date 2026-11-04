"""Read-only ESPN public JSON client and payload parser for EL/UCL.

This module is deliberately pure with respect to persistence: it performs
bounded, polite HTTP GETs and parses external payloads into validated records.
It never touches the ``teams``/``matches``/``standings`` tables; identity and
writes live in :mod:`backend.espn_service`.

ESPN endpoints are undocumented. Public, credential-free access does not by
itself establish unrestricted reuse rights; keep that operational caveat in
mind. Requests are spaced (>=0.5s), retried with bounded backoff and cached
with source-namespaced keys.

Season recipe (validated against live payloads):
    standings  GET {V2}/soccer/{slug}/standings?season={year}
    fixtures   GET {SITE}/soccer/{slug}/scoreboard?dates={year}&limit=1000
               and the following calendar year, deduplicated by event ID and
               filtered on ``event.season.year``. Range-form ``dates`` return
               HTTP 400 and numeric ``page`` is ignored, so only single-year
               pulls are used.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.api_cache import APICache
from backend.competitions import espn_slug_for, league_matches_for

logger = logging.getLogger(__name__)

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"

# Supported competitions expose 36 league-phase clubs in the new format.
EXPECTED_STANDINGS_TEAMS = 36

_LEAGUE_NAMES = {
    "EL": "UEFA Europa League",
    "UCL": "UEFA Conference League",
}

# ESPN season slug/name -> (app stage, app round). Keyed on the stable slug,
# never the season-varying numeric ``season.type`` id. Unknown stages fail
# visibly rather than being counted as league-phase data.
_STAGE_MAP = {
    "league-phase": ("LEAGUE_STAGE", None),
    "league": ("LEAGUE_STAGE", None),
    "knockout-round-playoffs": ("KNOCKOUT_PLAYOFF", "Knockout Play-off"),
    "knockout-round-playoff": ("KNOCKOUT_PLAYOFF", "Knockout Play-off"),
    "round-of-16": ("KNOCKOUT", "Round of 16"),
    "quarterfinals": ("KNOCKOUT", "Quarter-finals"),
    "quarter-finals": ("KNOCKOUT", "Quarter-finals"),
    "semifinals": ("KNOCKOUT", "Semi-finals"),
    "semi-finals": ("KNOCKOUT", "Semi-finals"),
    "final": ("KNOCKOUT", "Final"),
}

# Pre-tournament qualifying stages carry their own fixtures but are not part of
# the league-phase/knockout data this app stores. They are skipped explicitly
# (with a diagnostic) instead of being mistaken for league games or rejected as
# "unknown". Anything else that is not a known non-qualifier stage raises.
_QUALIFYING_SLUGS = {
    "preliminary-round",
    "first-qualifying-round",
    "second-qualifying-round",
    "third-qualifying-round",
    "qualifying-first-round",
    "qualifying-second-round",
    "qualifying-third-round",
    "playoffs",
    "play-off-round",
}

_STATUS_MAP = {
    "STATUS_SCHEDULED": "SCHEDULED",
    "STATUS_TIMED": "TIMED",
    "STATUS_IN_PROGRESS": "IN_PLAY",
    "STATUS_FIRST_HALF": "IN_PLAY",
    "STATUS_HALFTIME": "IN_PLAY",
    "STATUS_SECOND_HALF": "IN_PLAY",
    "STATUS_EXTRA_TIME": "IN_PLAY",
    "STATUS_SHOOTOUT": "IN_PLAY",
    "STATUS_FULL_TIME": "FINISHED",
    "STATUS_FINAL": "FINISHED",
    "STATUS_FINAL_AET": "FINISHED",
    "STATUS_FINAL_PEN": "FINISHED",
    "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELED",
    "STATUS_CANCELLED": "CANCELED",
    "STATUS_SUSPENDED": "SUSPENDED",
    "STATUS_DELAYED": "DELAYED",
    "STATUS_ABANDONED": "ABANDONED",
}

# Statistic name -> record field for a standings entry.
_STAT_FIELDS = {
    "gamesPlayed": "played",
    "wins": "won",
    "ties": "draw",
    "losses": "lost",
    "pointsFor": "goalsFor",
    "pointsAgainst": "goalsAgainst",
    "pointDifferential": "goalDifference",
    "points": "points",
    "rank": "position",
}

# Statistics that must be present and integral (rank is optional).
_REQUIRED_STATS = (
    "gamesPlayed", "wins", "ties", "losses",
    "pointsFor", "pointsAgainst", "pointDifferential", "points",
)


class ESPNClientError(RuntimeError):
    """A bounded ESPN request failed (network, HTTP or malformed body)."""


class ESPNValidationError(RuntimeError):
    """An ESPN payload did not match the requested competition/season shape."""


class ESPNClient:
    """Bounded ESPN soccer client and payload parser."""

    def __init__(
        self,
        cache: Optional[APICache] = None,
        timeout: float = 30.0,
        min_request_interval: float = 0.5,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        cache_ttl: int = 3600,
    ):
        self.cache = cache
        self.timeout = timeout
        self.min_request_interval = max(0.5, float(min_request_interval))
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff = retry_backoff
        self.cache_ttl = cache_ttl
        self._last_request_time = 0.0
        self.headers = {
            "User-Agent": "ucl-solkoff/1.0 (+public JSON reader)",
            "Accept": "application/json",
        }

    # -- HTTP -------------------------------------------------------------

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _request(self, url: str) -> Dict[str, Any]:
        """GET ``url`` with caching, polite spacing and bounded retries.

        Only a parsed JSON object is cached; HTTP failures and malformed bodies
        are never stored. A syntactically valid body is cached here before its
        semantic validation, so :meth:`fetch_season` evicts the season's keys
        (:meth:`invalidate_season`) whenever parsing rejects a payload. A
        rejected body is therefore never re-served from cache for its TTL.
        """
        cache_key = f"espn:{url}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._rate_limit()
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(url, headers=self.headers)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    last_error = ESPNClientError(
                        f"ESPN transient HTTP {response.status_code} for {url}"
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_backoff * (2 ** attempt))
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as e:
                    # A truncated/malformed 200 is a source failure, not a valid
                    # season result: surface the bounded error and never cache.
                    raise ESPNClientError(
                        f"ESPN response for {url} was not valid JSON"
                    ) from e
                if not isinstance(data, dict):
                    raise ESPNValidationError(
                        f"ESPN response for {url} was not a JSON object"
                    )
                if self.cache is not None:
                    self.cache.set(cache_key, data, self.cache_ttl)
                return data
            except httpx.HTTPStatusError as e:
                logger.error("ESPN HTTP error for %s: %s", url, e)
                raise ESPNClientError(str(e)) from e
            except httpx.RequestError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                logger.error("ESPN request error for %s: %s", url, e)
                raise ESPNClientError(str(e)) from e

        raise ESPNClientError(f"ESPN request failed for {url}: {last_error}")

    def _get_standings(self, slug: str, season_year: int) -> Dict[str, Any]:
        return self._request(self._standings_url(slug, season_year))

    def _get_scoreboard(self, slug: str, year_arg: int) -> Dict[str, Any]:
        return self._request(self._scoreboard_url(slug, year_arg))

    @staticmethod
    def _standings_url(slug: str, season_year: int) -> str:
        return f"{STANDINGS_BASE}/{slug}/standings?season={season_year}"

    @staticmethod
    def _scoreboard_url(slug: str, year_arg: int) -> str:
        return f"{SITE_BASE}/{slug}/scoreboard?dates={year_arg}&limit=1000"

    def _invalidate(self, url: str) -> None:
        """Delete this client's own source-namespaced cache entry for ``url``."""
        if self.cache is not None:
            self.cache.clear(f"espn:{url}")

    def invalidate_season(self, competition_id: str, season_year: int) -> None:
        """Evict the cached ESPN payloads for one competition-season.

        Called when a payload cached by :meth:`_request` is rejected by semantic
        validation, so a wrong-league/malformed body is not re-served from cache
        for its TTL. Only this season's own source-namespaced keys are removed;
        other providers and seasons are untouched.
        """
        if self.cache is None:
            return
        slug = espn_slug_for(competition_id)
        for url in (
            self._standings_url(slug, season_year),
            self._scoreboard_url(slug, season_year),
            self._scoreboard_url(slug, season_year + 1),
        ):
            self._invalidate(url)

    # -- Public season fetch ---------------------------------------------

    def fetch_season(self, competition_id: str, season_year: int) -> Dict[str, Any]:
        """Fetch and parse one competition-season from ESPN.

        Returns a validated record with ``standings`` (36 league-phase clubs)
        and ``matches`` (deduplicated, stage/round/status mapped). All network
        work happens here, before any database transaction.
        """
        competition_id = competition_id.strip().upper()
        slug = espn_slug_for(competition_id)

        try:
            standings_raw = self._get_standings(slug, season_year)
            standings = self._parse_standings(
                competition_id, season_year, standings_raw
            )

            diagnostics: List[str] = []
            signatures: Dict[str, Tuple] = {}
            events_by_id: Dict[str, Dict[str, Any]] = {}
            for year_arg in (season_year, season_year + 1):
                payload = self._get_scoreboard(slug, year_arg)
                for event in self._filter_events(season_year, payload, slug):
                    raw_id = event.get("id")
                    if raw_id is None or raw_id == "":
                        raise ESPNValidationError(
                            f"ESPN event without an id in {competition_id} "
                            f"season {season_year}"
                        )
                    event_id = str(raw_id)
                    signature = self._event_signature(event)
                    if event_id in signatures:
                        if signatures[event_id] != signature:
                            raise ESPNValidationError(
                                f"conflicting duplicate event id {event_id} in "
                                f"{competition_id} {season_year}: "
                                f"{signatures[event_id]} != {signature}"
                            )
                        # Same identity across calendar pulls: a later snapshot
                        # may be a genuine status/score update, so keep freshest.
                        events_by_id[event_id] = event
                        diagnostics.append(
                            f"event {event_id} repeated across calendar pulls; "
                            "using the latest snapshot"
                        )
                    else:
                        signatures[event_id] = signature
                        events_by_id[event_id] = event

            matches, match_diags = self._parse_matches(
                competition_id, season_year, list(events_by_id.values())
            )
            diagnostics.extend(match_diags)
        except ESPNValidationError:
            # A cached body was rejected on its semantics (wrong league/season,
            # malformed entries): evict this season's keys so it is not
            # re-served from cache for its TTL.
            self.invalidate_season(competition_id, season_year)
            raise

        return {
            "competition_id": competition_id,
            "season_year": int(season_year),
            "league_slug": slug,
            "league_matches": league_matches_for(competition_id),
            "standings": standings,
            "matches": matches,
            "diagnostics": diagnostics,
        }

    # -- Parsing ----------------------------------------------------------

    def _parse_standings(
        self, competition_id: str, season_year: int, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        expected_name = _LEAGUE_NAMES.get(competition_id)
        if expected_name and payload.get("name") != expected_name:
            raise ESPNValidationError(
                f"standings name {payload.get('name')!r} does not match "
                f"{expected_name!r} for {competition_id}"
            )
        season = payload.get("season") or {}
        if season.get("year") != season_year:
            raise ESPNValidationError(
                f"standings season {season.get('year')!r} does not match "
                f"requested {season_year}"
            )

        children = payload.get("children") or []
        entries = []
        for child in children:
            standings = (child or {}).get("standings") or {}
            entries.extend(standings.get("entries") or [])
        if not entries:
            raise ESPNValidationError(
                f"no standings entries for {competition_id} season {season_year}"
            )

        teams: List[Dict[str, Any]] = []
        seen_ids = set()
        for index, entry in enumerate(entries):
            team = entry.get("team") or {}
            external_id = team.get("id")
            name = team.get("displayName") or team.get("name")
            if not external_id:
                raise ESPNValidationError("standings entry without a team id")
            external_id = str(external_id)
            if not name:
                raise ESPNValidationError(
                    f"standings entry {external_id} has no team name"
                )
            if external_id in seen_ids:
                raise ESPNValidationError(
                    f"duplicate standings team id {external_id}"
                )
            seen_ids.add(external_id)

            stats = {s.get("name"): s.get("value") for s in (entry.get("stats") or [])}
            values: Dict[str, int] = {}
            for stat_name in _REQUIRED_STATS:
                parsed = self._int_or_none(stats.get(stat_name))
                if parsed is None:
                    raise ESPNValidationError(
                        f"standings team {external_id} missing integer stat "
                        f"{stat_name!r}"
                    )
                values[stat_name] = parsed
            non_negative = (
                "gamesPlayed", "wins", "ties", "losses",
                "pointsFor", "pointsAgainst", "points",
            )
            if any(values[name] < 0 for name in non_negative):
                raise ESPNValidationError(
                    f"standings team {external_id} has a negative stat: {values}"
                )

            played = values["gamesPlayed"]
            won, draw, lost = values["wins"], values["ties"], values["losses"]
            gf, ga = values["pointsFor"], values["pointsAgainst"]
            gd, points = values["pointDifferential"], values["points"]
            if won + draw + lost != played:
                raise ESPNValidationError(
                    f"standings team {external_id}: W+D+L {won+draw+lost} != "
                    f"played {played}"
                )
            if 3 * won + draw != points:
                # Strict 3/1/0: the app has never modelled externally applied
                # point deductions (sanctions). An adjusted table fails visibly
                # for review instead of being silently accepted.
                raise ESPNValidationError(
                    f"standings team {external_id}: 3W+D {3*won+draw} != "
                    f"points {points}"
                )
            if gf - ga != gd:
                raise ESPNValidationError(
                    f"standings team {external_id}: GF-GA {gf-ga} != GD {gd}"
                )

            position = self._int_or_none(stats.get("rank"))
            teams.append({
                "externalTeamId": external_id,
                "name": name,
                "abbreviation": team.get("abbreviation"),
                "crest": self._logo(team),
                "slug": team.get("slug"),
                "played": played,
                "won": won,
                "draw": draw,
                "lost": lost,
                "goalsFor": gf,
                "goalsAgainst": ga,
                "goalDifference": gd,
                "points": points,
                "position": position if position is not None else index + 1,
            })

        if len(teams) != EXPECTED_STANDINGS_TEAMS:
            raise ESPNValidationError(
                f"{competition_id} season {season_year} standings has "
                f"{len(teams)} teams, expected {EXPECTED_STANDINGS_TEAMS}"
            )
        return {"teams": teams}

    def _filter_events(
        self, season_year: int, payload: Dict[str, Any], expected_slug: str
    ) -> List[Dict[str, Any]]:
        """Verify the scoreboard's league identity, then keep this season's events.

        The payload's authoritative top-level ``leagues`` metadata must name the
        requested competition slug; a foreign-league body is rejected rather than
        silently accepted. Its advertised ``season.year`` is deliberately *not*
        compared to the calendar pull year, because ESPN advertises the current
        season while a single calendar pull may carry the adjacent season's
        events. Only ``event.season.year`` filters the events themselves.
        """
        self._verify_scoreboard_league(expected_slug, payload)
        events = payload.get("events")
        if not isinstance(events, list):
            # A body without an events list is a schema failure, not an empty
            # schedule: never let it be read as "no fixtures this season".
            raise ESPNValidationError(
                "ESPN scoreboard payload has no events list"
            )
        kept = []
        for event in events:
            season = event.get("season") or {}
            if season.get("year") != season_year:
                continue
            kept.append(event)
        return kept

    @staticmethod
    def _verify_scoreboard_league(expected_slug: str, payload: Dict[str, Any]) -> None:
        """Require authoritative scoreboard league metadata to match the request."""
        leagues = payload.get("leagues")
        if not isinstance(leagues, list) or not leagues:
            raise ESPNValidationError(
                "ESPN scoreboard payload is missing authoritative league metadata"
            )
        slugs = {
            entry.get("slug")
            for entry in leagues
            if isinstance(entry, dict) and entry.get("slug")
        }
        if expected_slug not in slugs:
            raise ESPNValidationError(
                f"ESPN scoreboard leagues {sorted(slugs)} do not include the "
                f"requested competition {expected_slug!r}"
            )

    @staticmethod
    def _event_signature(event: Dict[str, Any]) -> Tuple:
        """Stable provider identity for duplicate detection (ignores score/status)."""
        season = event.get("season") or {}
        competitions = event.get("competitions") or []
        comp = competitions[0] if competitions else {}
        sides: Dict[Any, Any] = {}
        for competitor in comp.get("competitors") or []:
            sides[competitor.get("homeAway")] = str(
                (competitor.get("team") or {}).get("id")
            )
        leg = comp.get("leg")
        leg_value = leg.get("value") if isinstance(leg, dict) else None
        return (
            season.get("year"),
            season.get("slug"),
            sides.get("home"),
            sides.get("away"),
            leg_value,
            str(event.get("date") or "")[:10],
        )

    def _parse_matches(
        self, competition_id: str, season_year: int, events: List[Dict[str, Any]]
    ) -> "Tuple[List[Dict[str, Any]], List[str]]":
        matches: List[Dict[str, Any]] = []
        diagnostics: List[str] = []
        for event in events:
            raw_id = event.get("id")
            if raw_id is None or raw_id == "":
                raise ESPNValidationError("ESPN event without an id")
            event_id = str(raw_id)

            competitions = event.get("competitions") or []
            if not competitions:
                raise ESPNValidationError(f"event {event_id} has no competition")
            comp = competitions[0]

            season = event.get("season") or {}
            slug = season.get("slug")
            if slug in _QUALIFYING_SLUGS or (
                isinstance(slug, str) and slug.startswith("qualifying")
            ):
                diagnostics.append(
                    f"skipping qualifying event {event_id} (stage {slug!r})"
                )
                continue
            if slug not in _STAGE_MAP:
                raise ESPNValidationError(
                    f"unknown ESPN season stage {slug!r} for event {event_id} "
                    f"in {competition_id}"
                )
            stage, round_name = _STAGE_MAP[slug]

            status = self._map_status(comp.get("status") or event.get("status"))

            date = event.get("date") or comp.get("date")
            if not self._valid_season_date(date, season_year):
                raise ESPNValidationError(
                    f"event {event_id} date {date!r} is missing or outside "
                    f"{competition_id} season {season_year}"
                )

            home, away = None, None
            for competitor in comp.get("competitors") or []:
                side = competitor.get("homeAway")
                if side == "home":
                    home = competitor
                elif side == "away":
                    away = competitor
            if home is None or away is None:
                if status == "FINISHED":
                    raise ESPNValidationError(
                        f"completed event {event_id} missing home/away competitor"
                    )
                diagnostics.append(
                    f"event {event_id} missing home/away competitor; skipped"
                )
                continue

            home_team = self._team_ref(home)
            away_team = self._team_ref(away)
            if home_team is None or away_team is None:
                if status == "FINISHED":
                    raise ESPNValidationError(
                        f"completed event {event_id} is missing a resolved team"
                    )
                diagnostics.append(
                    f"event {event_id} has an unresolved placeholder team; skipped"
                )
                continue
            if home_team["externalTeamId"] == away_team["externalTeamId"]:
                raise ESPNValidationError(
                    f"event {event_id} pairs a team with itself "
                    f"({home_team['externalTeamId']})"
                )

            home_score = self._int_or_none(home.get("score"))
            away_score = self._int_or_none(away.get("score"))
            for side, score in (("home", home_score), ("away", away_score)):
                if score is not None and score < 0:
                    raise ESPNValidationError(
                        f"event {event_id} has a negative {side} score {score}"
                    )
            if status == "FINISHED" and (home_score is None or away_score is None):
                raise ESPNValidationError(
                    f"completed event {event_id} is missing a final score"
                )

            leg = comp.get("leg")
            leg_value = leg.get("value") if isinstance(leg, dict) else None

            matches.append({
                "externalEventId": event_id,
                "date": date,
                "status": status,
                "stage": stage,
                "round": round_name,
                "leg": leg_value,
                "home": home_team,
                "away": away_team,
                "homeScore": home_score,
                "awayScore": away_score,
            })
        return matches, diagnostics

    @staticmethod
    def _team_ref(competitor: Dict[str, Any]) -> "Optional[Dict[str, Any]]":
        team = competitor.get("team") or {}
        external_id = team.get("id")
        if not external_id:
            return None
        return {
            "externalTeamId": str(external_id),
            "name": team.get("displayName") or team.get("name"),
            "abbreviation": team.get("abbreviation"),
            "crest": team.get("logo") or ESPNClient._logo(team),
        }

    @staticmethod
    def _logo(team: Dict[str, Any]) -> Optional[str]:
        logos = team.get("logos") or []
        if logos and isinstance(logos, list):
            return (logos[0] or {}).get("href")
        return team.get("logo")

    @staticmethod
    def _season_bounds(season_year: int) -> "Tuple[str, str]":
        return f"{season_year}-08-01", f"{season_year + 1}-08-01"

    def _valid_season_date(self, date: Any, season_year: int) -> bool:
        if not date:
            return False
        try:
            parsed = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        day = parsed.date().isoformat()
        start, end = self._season_bounds(season_year)
        return start <= day < end

    @staticmethod
    def _map_status(status: Optional[Dict[str, Any]]) -> str:
        status = status or {}
        type_info = status.get("type") or {}
        name = type_info.get("name")
        if name in _STATUS_MAP:
            return _STATUS_MAP[name]
        state = type_info.get("state")
        if state == "post":
            return "FINISHED"
        if state == "in":
            return "IN_PLAY"
        if state == "pre":
            return "SCHEDULED"
        raise ESPNValidationError(
            f"unknown ESPN status {name!r} / state {state!r}"
        )

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
