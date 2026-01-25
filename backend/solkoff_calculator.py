"""Solkoff coefficient calculator."""
from datetime import datetime
from typing import Dict, List, Set
from backend.database import Database


class SolkoffCalculator:
    """Calculates Solkoff coefficients (sum of opponent points)."""
    
    def __init__(self, db: Database):
        """Initialize calculator.
        
        Args:
            db: Database instance
        """
        self.db = db
    
    def get_opponents(self, team_id: int) -> Set[int]:
        """Get all opponents for a team.
        
        Args:
            team_id: Team ID
            
        Returns:
            Set of opponent team IDs
        """
        opponents = set()
        
        # Get opponents from home matches
        home_matches = self.db.fetchall("""
            SELECT away_team_id FROM matches
            WHERE home_team_id = ? AND status = 'FINISHED'
        """, (team_id,))
        
        for match in home_matches:
            opponents.add(match[0])
        
        # Get opponents from away matches
        away_matches = self.db.fetchall("""
            SELECT home_team_id FROM matches
            WHERE away_team_id = ? AND status = 'FINISHED'
        """, (team_id,))
        
        for match in away_matches:
            opponents.add(match[0])
        
        return opponents
    
    def calculate_solkoff(self, team_id: int) -> int:
        """Calculate Solkoff coefficient for a team.
        
        Solkoff coefficient = sum of points earned by all opponents faced.
        
        Args:
            team_id: Team ID
            
        Returns:
            Solkoff coefficient value
        """
        opponents = self.get_opponents(team_id)
        
        if not opponents:
            return 0
        
        # Get points for all opponents
        placeholders = ','.join(['?'] * len(opponents))
        opponent_points = self.db.fetchall(f"""
            SELECT points FROM standings
            WHERE team_id IN ({placeholders})
        """, tuple(opponents))
        
        # Sum all opponent points
        solkoff_value = sum((row[0] if row and row[0] is not None else 0) for row in opponent_points)
        
        return solkoff_value
    
    def calculate_all(self):
        """Calculate Solkoff coefficients for all teams and store in database."""
        # Get all teams
        teams = self.db.fetchall("SELECT id FROM teams")
        
        now = datetime.utcnow().isoformat()
        
        for team_row in teams:
            team_id = team_row[0]
            solkoff_value = self.calculate_solkoff(team_id)
            
            # Store in database
            self.db.execute("""
                INSERT INTO solkoff_coefficients (team_id, solkoff_value, calculated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (team_id) DO UPDATE SET
                    solkoff_value = excluded.solkoff_value,
                    calculated_at = excluded.calculated_at
            """, (team_id, solkoff_value, now))
        
        self.db.commit()

