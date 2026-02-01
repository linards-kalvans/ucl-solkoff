"""Team name matching between GitHub data and football-data.org API."""
import logging
from typing import Dict, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class TeamMatcher:
    """Matches team names from GitHub data to football-data.org team IDs."""
    
    def __init__(self, db):
        """Initialize team matcher.
        
        Args:
            db: Database instance
        """
        self.db = db
        self._initialize_team_mappings_table()
    
    def _initialize_team_mappings_table(self):
        """Create team_mappings table if it doesn't exist."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS team_mappings (
                team_name TEXT PRIMARY KEY,
                team_id INTEGER,
                confidence REAL,
                source TEXT
            )
        """)
        self.db.commit()
    
    def find_or_create_team_id(self, team_name: str, competition_id: str = "CL") -> Optional[int]:
        """Find existing team ID or create a new one.
        
        Args:
            team_name: Team name from GitHub data
            competition_id: Competition code
            
        Returns:
            Team ID (existing or newly created)
        """
        if not team_name:
            return None
        
        # Normalize team name
        normalized_name = self._normalize_team_name(team_name)
        
        # Check if we have a mapping
        mapping = self.db.fetchone("""
            SELECT team_id, confidence
            FROM team_mappings
            WHERE team_name = ?
        """, (normalized_name,))
        
        if mapping and mapping[0]:
            return mapping[0]
        
        # Try to find in existing teams table by name matching
        existing_teams = self.db.fetchall("""
            SELECT id, name
            FROM teams
        """)
        
        best_match = None
        best_score = 0.0
        
        for team_id, existing_name in existing_teams:
            score = self._similarity_score(normalized_name, self._normalize_team_name(existing_name))
            if score > best_score and score > 0.8:  # 80% similarity threshold
                best_score = score
                best_match = (team_id, existing_name)
        
        if best_match:
            team_id, matched_name = best_match
            # Store mapping for future use
            self.db.execute("""
                INSERT INTO team_mappings (team_name, team_id, confidence, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (team_name) DO UPDATE SET
                    team_id = excluded.team_id,
                    confidence = excluded.confidence
            """, (normalized_name, team_id, best_score, "fuzzy_match"))
            self.db.commit()
            logger.debug(f"Matched '{team_name}' to existing team ID {team_id} ({matched_name}) with {best_score:.2f} confidence")
            return team_id
        
        # Create new team with synthetic ID
        # Use negative IDs for GitHub-sourced teams to avoid conflicts
        # Get the minimum existing ID and subtract
        min_id_result = self.db.fetchone("SELECT MIN(id) FROM teams")
        min_id = min_id_result[0] if min_id_result and min_id_result[0] else 0
        
        # Generate synthetic ID (negative, starting from -1)
        new_team_id = -(abs(min_id) + 1000 + len(existing_teams))
        
        try:
            self.db.execute("""
                INSERT INTO teams (id, name, code, crest)
                VALUES (?, ?, ?, ?)
            """, (new_team_id, team_name, None, None))
            self.db.commit()
            
            # Store mapping
            self.db.execute("""
                INSERT INTO team_mappings (team_name, team_id, confidence, source)
                VALUES (?, ?, ?, ?)
            """, (normalized_name, new_team_id, 1.0, "synthetic"))
            self.db.commit()
            
            logger.debug(f"Created new team ID {new_team_id} for '{team_name}'")
            return new_team_id
            
        except Exception as e:
            logger.warning(f"Error creating team for '{team_name}': {e}")
            return None
    
    def _normalize_team_name(self, name: str) -> str:
        """Normalize team name for matching.
        
        Args:
            name: Raw team name
            
        Returns:
            Normalized name
        """
        if not name:
            return ""
        
        # Convert to lowercase
        normalized = name.lower().strip()
        
        # Remove common suffixes
        suffixes = [
            ' fc', ' football club', ' cf', ' club de fútbol',
            ' ac', ' athletic club', ' afc', ' association football club',
            ' united', ' city', ' town', ' rovers', ' wanderers'
        ]
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)].strip()
        
        # Remove common prefixes
        prefixes = ['fc ', 'cf ', 'ac ', 'afc ']
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
        
        # Remove special characters
        normalized = normalized.replace("'", "").replace("-", " ").replace(".", "")
        
        # Normalize whitespace
        normalized = ' '.join(normalized.split())
        
        return normalized
    
    def _similarity_score(self, name1: str, name2: str) -> float:
        """Calculate similarity score between two team names.
        
        Args:
            name1: First team name
            name2: Second team name
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        if not name1 or not name2:
            return 0.0
        
        # Use SequenceMatcher for fuzzy matching
        matcher = SequenceMatcher(None, name1, name2)
        base_score = matcher.ratio()
        
        # Bonus for exact match after normalization
        if name1 == name2:
            return 1.0
        
        # Bonus for containing same words
        words1 = set(name1.split())
        words2 = set(name2.split())
        if words1 and words2:
            word_overlap = len(words1 & words2) / max(len(words1), len(words2))
            base_score = max(base_score, word_overlap * 0.9)
        
        return base_score
    
    def get_team_id_by_name(self, team_name: str) -> Optional[int]:
        """Get team ID by name (exact or fuzzy match).
        
        Args:
            team_name: Team name to look up
            
        Returns:
            Team ID or None if not found
        """
        normalized = self._normalize_team_name(team_name)
        
        # Check mappings first
        mapping = self.db.fetchone("""
            SELECT team_id
            FROM team_mappings
            WHERE team_name = ?
        """, (normalized,))
        
        if mapping and mapping[0]:
            return mapping[0]
        
        # Try direct name match
        result = self.db.fetchone("""
            SELECT id
            FROM teams
            WHERE LOWER(TRIM(name)) = LOWER(?)
        """, (team_name,))
        
        if result:
            return result[0]
        
        return None

