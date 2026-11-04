"""Season discovery and historical league-table reconstruction.

Only new-format Champions League seasons (2024/25 onward) are exposed. The
current season keeps using the live ``standings`` table; older seasons are
rebuilt read-only from stored FINISHED league-stage matches so selecting a
season never mutates stored standings. All SQL is parameterised.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 2024/25 is the first season played in the new league-phase ("Swiss") format.
FIRST_SUPPORTED_SEASON = 2024


def current_season_year(today: Optional[datetime] = None) -> int:
    """Season start year for ``today`` (August boundary, UTC by default)."""
    if today is None:
        today = datetime.utcnow()
    return today.year if today.month >= 8 else today.year - 1


def season_bounds(year: int) -> Tuple[str, str]:
    """ISO bounds for a season: Aug 1 inclusive through following Aug 1 exclusive."""
    return f"{year}-08-01", f"{year + 1}-08-01"


def season_label(year: int) -> str:
    """Human label, e.g. ``2024`` -> ``2024/25``."""
    return f"{year}/{(year + 1) % 100:02d}"


def is_supported_year(year: Any, today: Optional[datetime] = None) -> bool:
    """True for integer seasons in ``[2024, current]``."""
    if isinstance(year, bool) or not isinstance(year, int):
        return False
    return FIRST_SUPPORTED_SEASON <= year <= current_season_year(today)


def discover_seasons(db, competition_id: str = "CL",
                     today: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Stored new-format seasons, newest first.

    A season is listed when it has any stored match in ``competition_id``; the
    current season is always listed even when empty. Older-format seasons and
    other competitions are never returned.
    """
    current = current_season_year(today)
    seasons: List[Dict[str, Any]] = []
    for year in range(current, FIRST_SUPPORTED_SEASON - 1, -1):
        start, end = season_bounds(year)
        row = db.fetchone(
            """
            SELECT COUNT(*) FROM matches
            WHERE competition_id = ? AND "date" >= ? AND "date" < ?
            """,
            (competition_id, start, end),
        )
        count = row[0] if row else 0
        if count > 0 or year == current:
            seasons.append({"year": year, "label": season_label(year)})
    return seasons


def _empty_team(team_id: int, name, code, crest) -> Dict[str, Any]:
    return {
        "teamId": team_id,
        "teamName": name,
        "teamCode": code,
        "teamCrest": crest,
        "position": None,
        "played": 0,
        "won": 0,
        "drawn": 0,
        "lost": 0,
        "gf": 0,
        "ga": 0,
        "gd": 0,
        "points": 0,
        "solkoffCoefficient": 0.0,
        "strengthScore": 0.0,
    }


def league_phase_boundary(year: int) -> str:
    """ISO boundary separating a new-format season's league phase from its
    knockout play-off round (Feb 1; the league phase always finishes in Jan)."""
    return f"{year + 1}-02-01"


def _league_predicate() -> str:
    """SQL predicate selecting a new-format season's league-phase matches.

    Accepts explicit ``LEAGUE_STAGE`` tags and the *legacy* stored shape written
    before new-format headers were recognised by the parser: a NULL stage, NULL
    round, NULL group, NULL matchday and a date before the February play-off
    boundary. The extra NULL-field guards are what keep this from treating an
    arbitrary untagged row (e.g. a legacy play-off tie in February) as league.
    """
    return (
        "(m.stage = 'LEAGUE_STAGE' OR ("
        "m.stage IS NULL AND (m.\"round\" IS NULL OR m.\"round\" = '') "
        "AND (m.group_name IS NULL OR m.group_name = '') AND m.matchday IS NULL "
        'AND m."date" < ?))'
    )


def _league_rows(db, year: int, competition_id: str):
    start, end = season_bounds(year)
    return db.fetchall(
        f"""
        SELECT m.home_team_id, m.away_team_id, m.home_score, m.away_score,
               t1.name, t1.code, t1.crest, t2.name, t2.code, t2.crest
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.competition_id = ?
          AND m.status = 'FINISHED'
          AND m."date" >= ? AND m."date" < ?
          AND {_league_predicate()}
        ORDER BY m."date"
        """,
        (competition_id, start, end, league_phase_boundary(year)),
    )


def _reconstruct(db, year: int, competition_id: str):
    """Rebuild a season's league table from stored results.

    Returns ``(ordered_standings, teams_by_id)`` using the same 3/1/0 points,
    points/GD/GF ordering and mean-unique-opponent-PPG Solkoff as the live
    standings.
    """
    teams: Dict[int, Dict[str, Any]] = {}
    opponents: Dict[int, set] = defaultdict(set)

    for (hid, aid, hs, as_, hn, hc, hcrest, an, ac, acrest) in _league_rows(
        db, year, competition_id
    ):
        if hs is None or as_ is None:
            continue
        home = teams.setdefault(hid, _empty_team(hid, hn, hc, hcrest))
        away = teams.setdefault(aid, _empty_team(aid, an, ac, acrest))

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

    for team in teams.values():
        team["gd"] = team["gf"] - team["ga"]
        team["_ppg"] = (team["points"] / team["played"]) if team["played"] > 0 else 0.0

    ppg_by_id = {tid: t["_ppg"] for tid, t in teams.items()}

    ordered = sorted(
        teams.values(),
        key=lambda t: (-t["points"], -t["gd"], -t["gf"], t["teamName"] or ""),
    )

    for position, team in enumerate(ordered, start=1):
        team["position"] = position
        ppgs = [
            ppg_by_id[opp]
            for opp in opponents.get(team["teamId"], set())
            if opp in teams and teams[opp]["played"] > 0
        ]
        solkoff = round(sum(ppgs) / len(ppgs), 3) if ppgs else 0.0
        team["solkoffCoefficient"] = solkoff
        if team["played"] > 0:
            team["strengthScore"] = round(
                (team["points"] * 100.0 / (team["played"] * 3)) * solkoff / 100.0, 6
            )
        else:
            team["strengthScore"] = 0.0
        team.pop("_ppg", None)

    by_id = {team["teamId"]: team for team in ordered}
    return ordered, by_id


def build_standings(db, year: int, competition_id: str = "CL") -> List[Dict[str, Any]]:
    """Historical league standings for ``year`` (empty list when no data)."""
    ordered, _ = _reconstruct(db, year, competition_id)
    return ordered


def build_breakdown(db, team_id: int, year: int,
                    competition_id: str = "CL") -> Optional[Dict[str, Any]]:
    """Historical Solkoff breakdown, mirroring the live details schema."""
    team_info = db.fetchone(
        "SELECT id, name, code, crest FROM teams WHERE id = ?", (team_id,)
    )
    if not team_info:
        return None

    _, by_id = _reconstruct(db, year, competition_id)
    start, end = season_bounds(year)

    rows = db.fetchall(
        f"""
        SELECT m.home_team_id, m.away_team_id, m.home_score, m.away_score,
               m."date",
               h.name, h.code, h.crest, a.name, a.code, a.crest
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        WHERE m.competition_id = ?
          AND m.status = 'FINISHED'
          AND m."date" >= ? AND m."date" < ?
          AND {_league_predicate()}
          AND (m.home_team_id = ? OR m.away_team_id = ?)
        ORDER BY m."date" DESC
        """,
        (competition_id, start, end, league_phase_boundary(year), team_id, team_id),
    )

    opponents: Dict[int, Dict[str, Any]] = {}
    for (hid, aid, hs, as_, date, hn, hc, hcrest, an, ac, acrest) in rows:
        is_home = hid == team_id
        opp_id = aid if is_home else hid
        opp_name = an if is_home else hn
        opp_code = ac if is_home else hc
        opp_crest = acrest if is_home else hcrest

        team_score = hs if is_home else as_
        opp_score = as_ if is_home else hs

        if team_score is None or opp_score is None:
            outcome = "unknown"
        elif team_score > opp_score:
            outcome = "win"
        elif team_score < opp_score:
            outcome = "loss"
        else:
            outcome = "draw"

        if opp_id not in opponents:
            standing = by_id.get(opp_id, {})
            opp_points = standing.get("points", 0)
            opp_played = standing.get("played", 0)
            opponents[opp_id] = {
                "teamId": opp_id,
                "teamName": opp_name,
                "teamCode": opp_code,
                "teamCrest": opp_crest,
                "points": opp_points,
                "played": opp_played,
                "pointsPerGame": (opp_points / opp_played) if opp_played > 0 else 0.0,
                "won": standing.get("won"),
                "draw": standing.get("drawn"),
                "lost": standing.get("lost"),
                "matchesPlayed": 0,
                "matches": [],
            }

        opponents[opp_id]["matchesPlayed"] += 1
        opponents[opp_id]["matches"].append({
            "homeScore": hs,
            "awayScore": as_,
            "teamScore": team_score,
            "opponentScore": opp_score,
            "outcome": outcome,
            "date": date,
            "isHome": is_home,
        })

    opponents_data = sorted(
        opponents.values(),
        key=lambda o: (-o.get("pointsPerGame", 0.0), o["teamName"] or ""),
    )

    ppgs = [opp["pointsPerGame"] for opp in opponents_data if opp["played"] > 0]
    avg_opponent_ppg = round(sum(ppgs) / len(ppgs), 3) if ppgs else 0.0
    solkoff = by_id.get(team_id, {}).get("solkoffCoefficient", 0.0)

    return {
        "teamId": team_info[0],
        "teamName": team_info[1],
        "teamCode": team_info[2],
        "teamCrest": team_info[3],
        "solkoffCoefficient": solkoff,
        "opponents": opponents_data,
        "averageOpponentPPG": avg_opponent_ppg,
        "matchesCount": sum(opp["matchesPlayed"] for opp in opponents_data),
        "opponentsCount": len(opponents_data),
    }
