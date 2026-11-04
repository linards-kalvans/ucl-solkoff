"""Synthetic ESPN payload builders for hermetic tests.

These mirror the *shape* of the live ESPN site scoreboard and v2 standings
payloads recorded during reconnaissance, without shipping large raw bodies.

Standings statistics are **derived from the same generated league results** the
scoreboard events carry, so the persisted table and the stored fixtures are
always mutually consistent (``played = W+D+L``, ``3W+D = points``,
``GF-GA = GD``). Generation covers the 36-club / 8-game (6-game for the
Conference) new-format structure.
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any, Dict, List, Optional, Tuple

LEAGUE_NAMES = {"EL": "UEFA Europa League", "UCL": "UEFA Conference League"}
LEAGUE_GAMES = {"EL": 8, "UCL": 6}
# ESPN league slugs carried as authoritative scoreboard metadata.
SCOREBOARD_SLUGS = {"EL": "uefa.europa", "UCL": "uefa.europa.conf"}

# Per-fixture results for league actor index 0 ("ESPN Club 01"), in the order its
# fixtures appear in the round-pair schedule. For EL this yields the profile
# 4W-2D-2L / GF 12 / GA 9 / GD +3 / 14 pts (the ESPN statistics the provider
# would report), while every other club's record is derived from the same games.
_LEAGUE_ACTOR_PLANS = {
    "EL": [(3, 0), (2, 0), (1, 0), (3, 2), (1, 1), (2, 2), (0, 2), (0, 2)],
    "UCL": [(2, 0), (2, 1), (1, 1), (0, 1), (3, 1), (2, 2)],
}


def _team_name(index: int) -> str:
    return f"ESPN Club {index:02d}"


def _team_abbr(index: int) -> str:
    return f"E{index:02d}"


def round_pairs(n: int, rounds: int) -> List[Tuple[int, int]]:
    """A circle-method schedule: ``rounds`` rounds of ``n/2`` unique pairs."""
    arr = list(range(n))
    pairs: List[Tuple[int, int]] = []
    for _ in range(rounds):
        for i in range(n // 2):
            pairs.append((arr[i], arr[n - 1 - i]))
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    return pairs


def league_results(competition_id: str) -> List[Tuple[int, int, int, int]]:
    """League fixtures as ``(home_idx0, away_idx0, home_score, away_score)``.

    Index 0 is "ESPN Club 01". Every club plays exactly ``LEAGUE_GAMES`` games.
    """
    games = LEAGUE_GAMES[competition_id]
    plan = list(_LEAGUE_ACTOR_PLANS[competition_id])
    if len(plan) != games:
        raise AssertionError(f"{competition_id} actor plan must have {games} games")
    results: List[Tuple[int, int, int, int]] = []
    plan_index = 0
    for home, away in round_pairs(36, games):
        if home == 0 or away == 0:
            gf, ga = plan[plan_index]
            plan_index += 1
            if home == 0:
                results.append((home, away, gf, ga))
            else:
                results.append((home, away, ga, gf))
        else:
            results.append((home, away, 2, 1))
    if plan_index != games:
        raise AssertionError(f"{competition_id} actor fixtures {plan_index} != {games}")
    return results


def _league_records(competition_id: str) -> Dict[int, Dict[str, int]]:
    """Per-club league record derived from :func:`league_results` (0-based idx)."""
    records = {
        idx: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0}
        for idx in range(36)
    }
    for home, away, hs, as_ in league_results(competition_id):
        for idx, gf, ga in ((home, hs, as_), (away, as_, hs)):
            rec = records[idx]
            rec["played"] += 1
            rec["gf"] += gf
            rec["ga"] += ga
            if gf > ga:
                rec["won"] += 1
            elif gf < ga:
                rec["lost"] += 1
            else:
                rec["drawn"] += 1
    for rec in records.values():
        rec["gd"] = rec["gf"] - rec["ga"]
        rec["points"] = 3 * rec["won"] + rec["drawn"]
    return records


def standings_payload(
    competition_id: str,
    season_year: int,
    league_games: Optional[int] = None,
    teams: int = 36,
) -> Dict[str, Any]:
    records = _league_records(competition_id)
    entries = []
    for idx in range(1, teams + 1):
        rec = records[idx - 1]
        entries.append({
            "team": {
                "id": str(idx),
                "displayName": _team_name(idx),
                "abbreviation": _team_abbr(idx),
                "logos": [{"href": f"https://cdn.example/{idx}.png"}],
            },
            "stats": [
                {"name": "gamesPlayed", "value": rec["played"]},
                {"name": "wins", "value": rec["won"]},
                {"name": "ties", "value": rec["drawn"]},
                {"name": "losses", "value": rec["lost"]},
                {"name": "pointsFor", "value": rec["gf"]},
                {"name": "pointsAgainst", "value": rec["ga"]},
                {"name": "pointDifferential", "value": rec["gd"]},
                {"name": "points", "value": rec["points"]},
                {"name": "rank", "value": idx},
            ],
        })
    return {
        "name": LEAGUE_NAMES[competition_id],
        "season": {"year": season_year, "displayName": f"{season_year}-{season_year+1}"},
        "children": [
            {"name": "League Phase", "standings": {"entries": entries}},
        ],
    }


def scoreboard_payload(
    events: List[Dict[str, Any]],
    slug: str = "uefa.europa",
    metadata_season_year: Optional[int] = None,
) -> Dict[str, Any]:
    """A site scoreboard body with authoritative top-level league metadata.

    Mirrors the live shape: ``leagues`` names the competition (the requested
    slug) and its advertised ``season.year`` is the *current* season, which need
    not equal the calendar ``dates`` year or the event-season year.
    """
    return {
        "leagues": [
            {
                "slug": slug,
                "name": "Synthetic League",
                "season": {"year": metadata_season_year},
            }
        ],
        "events": list(events),
    }


def _competitor(side: str, index: int, score: Optional[int]) -> Dict[str, Any]:
    return {
        "homeAway": side,
        "team": {
            "id": str(index),
            "displayName": _team_name(index),
            "abbreviation": _team_abbr(index),
            "logo": f"https://cdn.example/{index}.png",
        },
        "score": None if score is None else str(score),
    }


def event(
    event_id,
    date: str,
    season_year: int,
    slug: str,
    home: int,
    away: int,
    home_score: Optional[int],
    away_score: Optional[int],
    status_name: str = "STATUS_FULL_TIME",
    status_state: str = "post",
    leg: Optional[int] = None,
    teams_resolved: bool = True,
) -> Dict[str, Any]:
    competitors = []
    for side, index, score in (("home", home, home_score), ("away", away, away_score)):
        comp = _competitor(side, index, score)
        if not teams_resolved:
            comp["team"] = {}
        competitors.append(comp)
    competition: Dict[str, Any] = {
        "status": {"type": {"name": status_name, "state": status_state}},
        "competitors": competitors,
    }
    if leg is not None:
        competition["leg"] = {"value": leg, "displayValue": f"{leg} Leg"}
    return {
        "id": None if event_id is None else str(event_id),
        "date": date,
        "season": {"year": season_year, "type": 99999, "slug": slug},
        "competitions": [competition],
    }


def _knockout_events(season_year: int, base_eid: int, score: Tuple[int, int]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    eid = base_eid
    knockout_plan = [
        ("knockout-round-playoffs", 8, "02-19"),
        ("round-of-16", 8, "03-05"),
        ("quarterfinals", 4, "04-09"),
        ("semifinals", 2, "04-30"),
        ("final", 1, "05-28"),
    ]
    base = 1
    for slug, ties, md in knockout_plan:
        for tie in range(ties):
            home = base + (tie * 2) % 32
            away = base + (tie * 2 + 1) % 32
            legs = 1 if slug == "final" else 2
            for leg in range(1, legs + 1):
                eid += 1
                kickoff = _date(season_year + 1, int(md[:2]), int(md[3:])) + timedelta(
                    days=(leg - 1) * 7
                )
                events.append(event(
                    eid, f"{kickoff.isoformat()}T20:00Z", season_year,
                    slug, home if leg == 1 else away,
                    away if leg == 1 else home,
                    score[0], score[1], leg=leg if legs == 2 else None,
                ))
    return events


def el_season_events(season_year: int = 2024) -> List[Dict[str, Any]]:
    """A complete synthetic EL season: 144 league + 45 knockout = 189 fixtures."""
    events: List[Dict[str, Any]] = []
    eid = 5_000_000
    results = league_results("EL")
    for round_index, (home, away, hs, as_) in enumerate(results):
        day = 10 + round_index % 20
        month = 9 if round_index < 72 else 1
        year = season_year if month == 9 else season_year + 1
        eid += 1
        events.append(event(
            eid, f"{year}-{month:02d}-{day:02d}T20:00Z", season_year,
            "league-phase", home + 1, away + 1, hs, as_,
        ))
    events.extend(_knockout_events(season_year, eid, (1, 0)))
    return events


def conf_season_events(season_year: int = 2024) -> List[Dict[str, Any]]:
    """A complete synthetic Conference season: 108 league + 45 knockout = 153."""
    events: List[Dict[str, Any]] = []
    eid = 6_000_000
    results = league_results("UCL")
    for round_index, (home, away, hs, as_) in enumerate(results):
        day = 10 + round_index % 20
        month = 9 if round_index < 54 else 1
        year = season_year if month == 9 else season_year + 1
        eid += 1
        events.append(event(
            eid, f"{year}-{month:02d}-{day:02d}T20:00Z", season_year,
            "league-phase", home + 1, away + 1, hs, as_,
        ))
    events.extend(_knockout_events(season_year, eid, (2, 0)))
    return events
