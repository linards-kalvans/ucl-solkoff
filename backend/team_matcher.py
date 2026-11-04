"""Team name matching between GitHub data and football-data.org API."""
import logging
import re
from typing import Dict, Optional, Tuple
from difflib import SequenceMatcher

from backend.fixture_ids import INT32_MIN

logger = logging.getLogger(__name__)

# Leading kickoff artifact, colon or dot form ("18:45 ", "20.45 ").
_KICKOFF_PREFIX = re.compile(r'^\s*\d{1,2}[:.]\d{2}\s+')


def strip_kickoff(name: str) -> str:
    """Remove a leading kickoff time artifact from a team name."""
    if not name:
        return ""
    return _KICKOFF_PREFIX.sub('', name).strip()


def normalize_team_name(name: str) -> str:
    """Normalize a team name for matching/storage (kickoff artifacts removed)."""
    if not name:
        return ""

    # Strip kickoff artifacts first so a leading "21:00"/"20.45" never survives.
    normalized = strip_kickoff(name).lower().strip()

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

        Never returns or stores a polluted (kickoff-prefixed) target:
        - a cached mapping is honoured only when its target team row is clean;
        - an exact clean-name match is preferred (positive id, unambiguous);
        - the fuzzy fallback only considers clean team rows.

        Args:
            team_name: Team name from GitHub data
            competition_id: Competition code

        Returns:
            Team ID (existing or newly created)
        """
        if not team_name:
            return None

        # Strip any kickoff prefix before matching or storing so colon/dot
        # artifacts never reach team rows or the mapping cache.
        clean_name = self._strip_kickoff(team_name)
        if not clean_name:
            return None

        normalized_name = self._normalize_team_name(clean_name)

        existing_teams = self.db.fetchall("SELECT id, name FROM teams")
        name_by_id = {team_id: name for team_id, name in existing_teams}
        # Clean rows only: polluted (kickoff-prefixed) rows are invisible to
        # matching so a bad alias can never be re-selected.
        clean_rows = [
            (team_id, name) for team_id, name in existing_teams
            if strip_kickoff(name) == name
        ]

        # 1) Cached mapping, but only when its target is a clean canonical row.
        mapping = self.db.fetchone("""
            SELECT team_id, confidence
            FROM team_mappings
            WHERE team_name = ?
        """, (normalized_name,))
        if mapping and mapping[0]:
            target_name = name_by_id.get(mapping[0])
            if target_name is not None and strip_kickoff(target_name) == target_name:
                return mapping[0]
            logger.debug(
                f"Ignoring cached mapping '{normalized_name}' -> {mapping[0]} "
                f"(target name {target_name!r} is not clean)"
            )

        # 2) Exact clean-name match; prefer a unique positive id.
        exact = [team_id for team_id, name in clean_rows if name == clean_name]
        if len(exact) == 1:
            return exact[0]
        positives = [team_id for team_id in exact if team_id > 0]
        if len(positives) == 1:
            return positives[0]
        if len(exact) > 1:
            logger.warning(
                f"Ambiguous clean team name {clean_name!r}: ids {sorted(exact)}; not guessing"
            )
            return None

        # 3) Fuzzy fallback over clean rows only (unchanged 0.8 threshold).
        best_match = None
        best_score = 0.0
        for team_id, existing_name in clean_rows:
            score = self._similarity_score(normalized_name, self._normalize_team_name(existing_name))
            if score > best_score and score > 0.8:  # 80% similarity threshold
                best_score = score
                best_match = (team_id, existing_name)

        if best_match:
            team_id, matched_name = best_match
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

        # 4) Create a new clean team with a bounded, collision-free synthetic id.
        existing_ids = {team_id for team_id, _ in existing_teams}
        min_id = min(existing_ids) if existing_ids else 0
        new_team_id = -(abs(min_id) + 1000 + len(existing_ids))
        while new_team_id < INT32_MIN or new_team_id in existing_ids:
            new_team_id += 1

        try:
            self.db.execute("""
                INSERT INTO teams (id, name, code, crest)
                VALUES (?, ?, ?, ?)
            """, (new_team_id, clean_name, None, None))
            self.db.commit()

            # Store mapping
            self.db.execute("""
                INSERT INTO team_mappings (team_name, team_id, confidence, source)
                VALUES (?, ?, ?, ?)
            """, (normalized_name, new_team_id, 1.0, "synthetic"))
            self.db.commit()

            logger.debug(f"Created new team ID {new_team_id} for '{clean_name}'")
            return new_team_id

        except Exception as e:
            logger.warning(f"Error creating team for '{clean_name}': {e}")
            return None
    
    def _strip_kickoff(self, name: str) -> str:
        """Remove a leading kickoff time artifact from a team name."""
        return strip_kickoff(name)
    
    def _normalize_team_name(self, name: str) -> str:
        """Normalize team name for matching (see module ``normalize_team_name``)."""
        return normalize_team_name(name)
    
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

