"""Solkoff coefficient calculator."""
from datetime import datetime
from typing import Dict, List, Set, Tuple
from backend.database import Database


def get_season_bounds(today=None) -> Tuple[str, str]:
    """Return current-season bounds as ISO date strings.

    The season window is August 1 inclusive through the following August 1
    exclusive, matching the historical-ingestion convention. The year is
    resolved at call time (UTC by default) so a long-running scheduler rolls
    over correctly.

    Args:
        today: Optional date or datetime for deterministic resolution.
            Defaults to current UTC time.

    Returns:
        Tuple of (start, end) ISO date strings, both ``YYYY-MM-DD``.
    """
    if today is None:
        today = datetime.utcnow()
    start_year = today.year if today.month >= 8 else today.year - 1
    return f"{start_year}-08-01", f"{start_year + 1}-08-01"


class SolkoffCalculator:
    """Calculates Solkoff coefficients (average PPG of opponents)."""
    
    def __init__(self, db: Database, competition_id: str = "CL"):
        """Initialize calculator.
        
        Args:
            db: Database instance
            competition_id: Competition whose matches are counted
        """
        self.db = db
        self.competition_id = competition_id
    
    def get_opponents(self, team_id: int) -> Set[int]:
        """Get all opponents for a team in the current season.

        Only finished league-stage matches in the configured competition and
        current season window count. Bounds are resolved at call time.
        
        Args:
            team_id: Team ID
            
        Returns:
            Set of opponent team IDs
        """
        opponents = set()
        season_start, season_end = get_season_bounds()
        
        # Get opponents from home matches
        home_matches = self.db.fetchall("""
            SELECT away_team_id FROM matches
            WHERE home_team_id = ? AND status = 'FINISHED'
              AND competition_id = ? AND stage = 'LEAGUE_STAGE'
              AND "date" >= ? AND "date" < ?
        """, (team_id, self.competition_id, season_start, season_end))
        
        for match in home_matches:
            opponents.add(match[0])
        
        # Get opponents from away matches
        away_matches = self.db.fetchall("""
            SELECT home_team_id FROM matches
            WHERE away_team_id = ? AND status = 'FINISHED'
              AND competition_id = ? AND stage = 'LEAGUE_STAGE'
              AND "date" >= ? AND "date" < ?
        """, (team_id, self.competition_id, season_start, season_end))
        
        for match in away_matches:
            opponents.add(match[0])
        
        return opponents
    
    def calculate_solkoff(self, team_id: int) -> float:
        """Calculate Solkoff coefficient for a team.
        
        Solkoff coefficient = average points per game of all opponents faced.
        
        Args:
            team_id: Team ID
            
        Returns:
            Solkoff coefficient value (average PPG of opponents)
        """
        opponents = self.get_opponents(team_id)
        
        if not opponents:
            return 0.0
        
        # Get points and games played for all opponents in this competition only.
        # Missing rows for the target competition must yield no stats (0.0),
        # never a fallback to another competition's standings.
        placeholders = ','.join(['?'] * len(opponents))
        opponent_stats = self.db.fetchall(f"""
            SELECT points, played FROM standings
            WHERE competition_id = ? AND team_id IN ({placeholders})
        """, (self.competition_id,) + tuple(opponents))

        # Calculate average points per game of opponents
        opponent_ppg_list = []
        for row in opponent_stats:
            if row and row[0] is not None and row[1] is not None and row[1] > 0:
                points = row[0] if row[0] is not None else 0
                played = row[1] if row[1] is not None else 0
                if played > 0:
                    opponent_ppg_list.append(points / played)
        
        if opponent_ppg_list:
            solkoff_value = sum(opponent_ppg_list) / len(opponent_ppg_list)
        else:
            solkoff_value = 0.0
        
        return round(solkoff_value, 3)
    
    def calculate_all(self):
        """Calculate Solkoff coefficients for this competition's standings.

        Only clubs that appear in the configured competition's live standings
        are (re)written, so a calculation for one competition can never erase or
        overwrite another competition's coefficients.
        """
        # Clubs tracked by this competition's live standings snapshot.
        teams = self.db.fetchall(
            "SELECT team_id FROM standings WHERE competition_id = ?",
            (self.competition_id,),
        )

        now = datetime.utcnow().isoformat()

        for team_row in teams:
            team_id = team_row[0]
            solkoff_value = self.calculate_solkoff(team_id)

            # Store in database, scoped to this competition
            self.db.execute("""
                INSERT INTO solkoff_coefficients (competition_id, team_id, solkoff_value, calculated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (competition_id, team_id) DO UPDATE SET
                    solkoff_value = excluded.solkoff_value,
                    calculated_at = excluded.calculated_at
            """, (self.competition_id, team_id, solkoff_value, now))

        self.db.commit()

