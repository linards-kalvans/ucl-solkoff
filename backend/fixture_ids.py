"""Deterministic synthetic-ID helpers shared by import and repair.

Keeping these in one neutral module (no DB, no API) guarantees the historical
import path (``data_service``) and the offline repair tool (``historical_repair``)
derive identical fixture IDs, so a re-import after repair updates the same rows
instead of inserting duplicates.
"""
import hashlib

# DuckDB ``INTEGER`` is signed 32-bit.
INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1


def deterministic_match_id(competition_id: str, season: str, home_team_id: int,
                           away_team_id: int, date, stage, matchday) -> int:
    """Stable negative fixture ID from canonical match identity.

    Uses SHA-256 (never the process-randomised builtin ``hash``) so the ID is
    identical across processes and Python hash seeds. Always inside the DuckDB
    ``INTEGER`` range.
    """
    key = "|".join([
        str(competition_id), str(season), str(home_team_id), str(away_team_id),
        str(date or ""), str(stage or ""),
        str(matchday) if matchday is not None else "",
    ])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return -(int(digest[:12], 16) % 2_000_000_000 + 1)


def effective_matchday(matchday, stage, round_name):
    """Matchday used for the fixture ID when the parser had no explicit value.

    The parsed matchday always wins (league 1..8, play-off leg 1/2). Only the
    legacy stage/round heuristic is used as a fallback, matching the historical
    import so repair and re-import agree.
    """
    if matchday is not None:
        return matchday
    if stage == "GROUP_STAGE":
        return 1
    if round_name:
        name = round_name.lower()
        if "round of 16" in name or "last 16" in name:
            return 7
        if "quarter" in name:
            return 8
        if "semi" in name:
            return 9
        if "final" in name:
            return 10
    return None
