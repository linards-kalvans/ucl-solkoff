"""Goal-adjusted Elo rating calculator."""
import math
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
from backend.database import Database

logger = logging.getLogger(__name__)

DEFAULT_RATING = 1500.0
K_FACTOR = 30.0
HOME_ADVANTAGE = 75.0
MIN_MATCHES_FOR_CONFIDENCE = 5
VALID_COMPETITIONS = ('CL', 'EL', 'UCL')


class EloCalculator:
    """Calculates goal-adjusted Elo ratings from historical match data."""

    def __init__(self, db: Database):
        self.db = db

    def _expected_score(self, rating_a: float, rating_b: float, home_advantage_for_a: float = 0.0) -> float:
        """Expected score for team A vs B. home_advantage_for_a boosts A's effective rating."""
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a - home_advantage_for_a) / 400.0))

    def _goal_multiplier(self, goal_diff: int) -> float:
        """Goal-difference multiplier: larger margins produce bigger rating shifts."""
        return 1.0 + math.log(abs(goal_diff) + 1)

    def _actual_score(self, home_score: int, away_score: int) -> Tuple[float, float]:
        """Return (actual_home, actual_away): 1.0/0.0 win, 0.5/0.5 draw."""
        if home_score > away_score:
            return 1.0, 0.0
        elif home_score < away_score:
            return 0.0, 1.0
        return 0.5, 0.5

    def calculate_all(self):
        """Replay all finished matches chronologically and store resulting Elo ratings."""
        matches = self.db.fetchall("""
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM matches
            WHERE status = 'FINISHED'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND date IS NOT NULL
              AND competition_id IN (?, ?, ?)
            ORDER BY date ASC
        """, VALID_COMPETITIONS)

        ratings: Dict[int, float] = {}
        played: Dict[int, int] = {}

        for row in matches:
            home_id, away_id, home_score, away_score = row[0], row[1], int(row[2]), int(row[3])

            r_home = ratings.get(home_id, DEFAULT_RATING)
            r_away = ratings.get(away_id, DEFAULT_RATING)

            exp_home = self._expected_score(r_home, r_away, HOME_ADVANTAGE)
            exp_away = 1.0 - exp_home

            actual_home, actual_away = self._actual_score(home_score, away_score)
            mult = self._goal_multiplier(abs(home_score - away_score))

            ratings[home_id] = r_home + K_FACTOR * mult * (actual_home - exp_home)
            ratings[away_id] = r_away + K_FACTOR * mult * (actual_away - exp_away)
            played[home_id] = played.get(home_id, 0) + 1
            played[away_id] = played.get(away_id, 0) + 1

        now = datetime.utcnow().isoformat()
        for team_id, rating in ratings.items():
            self.db.execute("""
                INSERT INTO elo_ratings (team_id, rating, matches_played, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (team_id) DO UPDATE SET
                    rating = excluded.rating,
                    matches_played = excluded.matches_played,
                    updated_at = excluded.updated_at
            """, (team_id, round(rating, 4), played[team_id], now))

        self.db.commit()
        logger.info(f"Elo ratings calculated for {len(ratings)} teams.")

    def get_rating(self, team_id: int) -> Optional[Tuple[float, int]]:
        """Return (rating, matches_played) for a team, or None if not found."""
        row = self.db.fetchone(
            "SELECT rating, matches_played FROM elo_ratings WHERE team_id = ?",
            (team_id,)
        )
        return (float(row[0]), int(row[1])) if row else None

    def get_team_stats(self, team_id: int) -> Optional[Dict]:
        """Return W/D/L, goal diff, and top notable wins for a team across Elo-rated matches."""
        rows = self.db.fetchall("""
            SELECT
                m.home_team_id, m.away_team_id,
                m.home_score, m.away_score,
                m.date,
                t1.name AS home_name,
                t2.name AS away_name
            FROM matches m
            LEFT JOIN teams t1 ON m.home_team_id = t1.id
            LEFT JOIN teams t2 ON m.away_team_id = t2.id
            WHERE (m.home_team_id = ? OR m.away_team_id = ?)
              AND m.status = 'FINISHED'
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
              AND m.competition_id IN (?, ?, ?)
            ORDER BY m.date ASC
        """, (team_id, team_id) + VALID_COMPETITIONS)

        if not rows:
            return None

        won = drawn = lost = gf = ga = 0
        wins_by_margin = []  # (margin, date, opponent_name, team_score, opp_score)

        for row in rows:
            is_home = row[0] == team_id
            ts = int(row[2]) if is_home else int(row[3])
            os_ = int(row[3]) if is_home else int(row[2])
            opp_name = (row[6] if is_home else row[5]) or "Unknown"
            date = row[4]

            gf += ts
            ga += os_
            diff = ts - os_

            if diff > 0:
                won += 1
                wins_by_margin.append((diff, date, opp_name, ts, os_))
            elif diff == 0:
                drawn += 1
            else:
                lost += 1

        wins_by_margin.sort(key=lambda x: -x[0])
        notable_wins = [
            {
                "date": n[1][:10] if n[1] else None,
                "opponent": n[2],
                "score": f"{n[3]}–{n[4]}",
                "goalDiff": n[0],
            }
            for n in wins_by_margin[:3]
        ]

        return {
            "won": won,
            "drawn": drawn,
            "lost": lost,
            "goalsFor": gf,
            "goalsAgainst": ga,
            "goalDifference": gf - ga,
            "notableWins": notable_wins,
        }

    def get_win_probability(self, team1_id: int, team2_id: int) -> Optional[Dict]:
        """Return Elo-based win probability dict, or None if either team lacks sufficient data.

        Assumes a neutral venue (no home advantage applied to output probabilities).
        Returns None rather than a fallback so callers can apply their own fallback logic.
        """
        result1 = self.get_rating(team1_id)
        result2 = self.get_rating(team2_id)

        if result1 is None or result2 is None:
            return None

        rating1, played1 = result1
        rating2, played2 = result2

        if played1 < MIN_MATCHES_FOR_CONFIDENCE or played2 < MIN_MATCHES_FOR_CONFIDENCE:
            return None

        p_team1 = 1.0 / (1.0 + 10.0 ** ((rating2 - rating1) / 400.0))
        p_team2 = 1.0 - p_team1

        result = {
            "team1Win": round(p_team1, 3),
            "team2Win": round(p_team2, 3),
            "draw": 0.0,
            "method": "elo",
            "team1EloRating": round(rating1, 1),
            "team2EloRating": round(rating2, 1),
            "team1MatchesPlayed": played1,
            "team2MatchesPlayed": played2,
        }

        stats1 = self.get_team_stats(team1_id)
        stats2 = self.get_team_stats(team2_id)
        if stats1:
            result["team1Stats"] = stats1
        if stats2:
            result["team2Stats"] = stats2

        return result
