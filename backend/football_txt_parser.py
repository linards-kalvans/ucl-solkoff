"""Parser for openfootball structured text format."""
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class FootballTxtParser:
    """Parses structured football.txt format from openfootball repository."""
    
    def __init__(self):
        """Initialize parser."""
        self.current_season = None
        self.current_competition = None
        self.current_stage = None
        self.current_round = None
        self.current_group = None
        
    def parse(self, content: str, competition: str = "CL", season: str = None) -> Dict[str, Any]:
        """Parse football.txt content.
        
        Args:
            content: Text content to parse
            competition: Competition code (CL, EL, UCL)
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            Dictionary with parsed data:
            - teams: List of team dictionaries
            - matches: List of match dictionaries
            - competition: Competition name
            - season: Season identifier
        """
        if not content:
            return {"teams": [], "matches": [], "competition": competition, "season": season}
        
        self.current_competition = competition
        self.current_season = season
        self.current_stage = None
        self.current_round = None
        self.current_group = None
        
        lines = content.split('\n')
        teams = set()
        matches = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line or line.startswith('#'):
                i += 1
                continue
            
            # Parse competition header
            if line.startswith('='):
                comp_name = line.lstrip('= ').strip()
                self.current_competition = self._normalize_competition(comp_name)
                logger.debug(f"Found competition: {comp_name}")
            
            # Parse group stage header
            elif 'Group' in line and ('|' in line or ':' in line):
                self.current_stage = "GROUP_STAGE"
                self.current_round = None
                # Extract group name
                group_match = re.search(r'Group\s+([A-H])', line, re.IGNORECASE)
                if group_match:
                    self.current_group = f"Group {group_match.group(1).upper()}"
                # Extract teams from group line
                team_names = self._extract_teams_from_group_line(line)
                for team_name in team_names:
                    teams.add(team_name)
            
            # Parse knockout stage headers
            elif any(keyword in line for keyword in ['Round of 16', 'Quarter-finals', 'Quarter-final', 'Semi-finals', 'Semi-final', 'Final']):
                self.current_stage = "KNOCKOUT"
                self.current_group = None
                self.current_round = self._extract_round_name(line)
                logger.debug(f"Found round: {self.current_round}")
            
            # Parse match lines
            elif self._is_match_line(line):
                match_data = self._parse_match_line(line, i, lines)
                if match_data:
                    matches.append(match_data)
                    # Extract teams from match
                    if match_data.get('home_team'):
                        teams.add(match_data['home_team'])
                    if match_data.get('away_team'):
                        teams.add(match_data['away_team'])
            
            i += 1
        
        # Convert teams set to list of dictionaries
        teams_list = [{"name": name, "code": None, "crest": None} for name in sorted(teams)]
        
        return {
            "teams": teams_list,
            "matches": matches,
            "competition": self.current_competition,
            "season": season
        }
    
    def _normalize_competition(self, comp_name: str) -> str:
        """Normalize competition name to code.
        
        Args:
            comp_name: Competition name from file
            
        Returns:
            Competition code (CL, EL, UCL)
        """
        comp_name_lower = comp_name.lower()
        if 'champions league' in comp_name_lower:
            return "CL"
        elif 'europa league' in comp_name_lower:
            return "EL"
        elif 'conference league' in comp_name_lower:
            return "UCL"
        return "CL"  # Default
    
    def _extract_teams_from_group_line(self, line: str) -> List[str]:
        """Extract team names from a group stage header line.
        
        Args:
            line: Group line (e.g., "Group A | Team1 Team2 Team3 Team4")
            
        Returns:
            List of team names
        """
        teams = []
        # Remove "Group X |" prefix
        if '|' in line:
            line = line.split('|', 1)[1]
        elif ':' in line:
            line = line.split(':', 1)[1]
        
        # Split by multiple spaces or tabs
        parts = re.split(r'\s{2,}|\t+', line.strip())
        for part in parts:
            part = part.strip()
            if part and len(part) > 2:  # Filter out very short strings
                teams.append(part)
        
        return teams
    
    def _extract_round_name(self, line: str) -> str:
        """Extract round name from header line.
        
        Args:
            line: Round header line
            
        Returns:
            Round name
        """
        line_lower = line.lower()
        if 'round of 16' in line_lower or 'last 16' in line_lower:
            return "Round of 16"
        elif 'quarter' in line_lower:
            return "Quarter-finals"
        elif 'semi' in line_lower:
            return "Semi-finals"
        elif 'final' in line_lower:
            return "Final"
        return line.strip()
    
    def _is_match_line(self, line: str) -> bool:
        """Check if a line represents a match.
        
        Args:
            line: Line to check
            
        Returns:
            True if line appears to be a match
        """
        # Match lines typically have:
        # - Time (e.g., "20.45")
        # - Team names
        # - Score (e.g., "1-1", "3-0")
        # - Optional venue
        
        # Check for score pattern
        score_pattern = r'\d+-\d+'
        if not re.search(score_pattern, line):
            return False
        
        # Check for time pattern (optional)
        time_pattern = r'\d{1,2}\.\d{2}'
        has_time = bool(re.search(time_pattern, line))
        
        # Should have at least two words (team names)
        words = line.split()
        if len(words) < 3:
            return False
        
        return True
    
    def _parse_match_line(self, line: str, line_num: int, all_lines: List[str]) -> Optional[Dict[str, Any]]:
        """Parse a single match line.
        
        Args:
            line: Match line to parse
            line_num: Current line number
            all_lines: All lines for context
            
        Returns:
            Match dictionary or None if parsing fails
        """
        try:
            # Extract date from previous lines if available
            date = self._extract_date_from_context(line_num, all_lines)
            
            # Extract time
            time_match = re.search(r'(\d{1,2}\.\d{2})', line)
            match_time = time_match.group(1) if time_match else None
            
            # Extract score
            score_match = re.search(r'(\d+)-(\d+)', line)
            if not score_match:
                return None
            
            home_score = int(score_match.group(1))
            away_score = int(score_match.group(2))
            
            # Extract teams (before and after score)
            # Common patterns:
            # - "20.45 Team1 v Team2 1-1"
            # - "Team1 v Team2 1-1 @ Venue"
            # - "Team1 Team2 1-1"
            
            # Try pattern with 'v' first
            parts = re.split(r'\s+v\s+', line, flags=re.IGNORECASE)
            if len(parts) >= 2:
                home_team = parts[0].strip()
                # Remove time from home team
                home_team = re.sub(r'\d{1,2}\.\d{2}', '', home_team).strip()
                
                # Away team is before score in second part
                away_part = parts[1]
                score_pos = away_part.find(score_match.group(0))
                if score_pos > 0:
                    away_team = away_part[:score_pos].strip()
                else:
                    # Score might be before team name
                    away_team = away_part.split()[0] if away_part.split() else None
            else:
                # Try without 'v' - split by score
                score_pos = line.find(score_match.group(0))
                if score_pos > 0:
                    before_score = line[:score_pos].strip()
                    after_score = line[score_pos + len(score_match.group(0)):].strip()
                    
                    # Remove time and venue info
                    before_score = re.sub(r'\d{1,2}\.\d{2}', '', before_score).strip()
                    after_score = re.sub(r'@\s+[^,]+', '', after_score).strip()
                    
                    # Extract team names (usually last 1-3 words before score, first 1-3 words after)
                    before_words = [w for w in before_score.split() if w and not w.isdigit()]
                    after_words = [w for w in after_score.split() if w and not w.isdigit()]
                    
                    if len(before_words) >= 2:
                        # Try last 2-3 words as team name
                        home_team = ' '.join(before_words[-2:])
                        if len(before_words) >= 3:
                            # Try 3 words if it makes sense
                            home_team_alt = ' '.join(before_words[-3:])
                            if len(home_team_alt.split()) <= 4:  # Reasonable team name length
                                home_team = home_team_alt
                    elif len(before_words) == 1:
                        home_team = before_words[0]
                    else:
                        return None
                    
                    if len(after_words) >= 2:
                        away_team = ' '.join(after_words[:2])
                        if len(after_words) >= 3:
                            away_team_alt = ' '.join(after_words[:3])
                            if len(away_team_alt.split()) <= 4:
                                away_team = away_team_alt
                    elif len(after_words) == 1:
                        away_team = after_words[0]
                    else:
                        return None
                else:
                    return None
            
            if not home_team or not away_team:
                return None
            
            # Clean team names
            home_team = self._clean_team_name(home_team)
            away_team = self._clean_team_name(away_team)
            
            if not home_team or not away_team:
                return None
            
            # Determine status
            status = "FINISHED" if home_score is not None and away_score is not None else "SCHEDULED"
            
            # Extract venue if present
            venue_match = re.search(r'@\s+([^,]+)', line)
            venue = venue_match.group(1).strip() if venue_match else None
            
            match_data = {
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "date": date,
                "time": match_time,
                "status": status,
                "stage": self.current_stage,
                "round": self.current_round,
                "group_name": self.current_group,
                "competition_id": self.current_competition,
                "venue": venue
            }
            
            return match_data
            
        except Exception as e:
            logger.debug(f"Error parsing match line '{line}': {e}")
            return None
    
    def _extract_date_from_context(self, line_num: int, all_lines: List[str], lookback: int = 10) -> Optional[str]:
        """Extract date from previous lines.
        
        Args:
            line_num: Current line number
            all_lines: All lines
            lookback: Number of lines to look back
            
        Returns:
            Date string in YYYY-MM-DD format or None
        """
        import calendar
        
        # Determine season year from self.current_season
        season_year = None
        if self.current_season:
            # Extract year from season string (e.g., "2023-24" -> 2023)
            year_match = re.search(r'(\d{4})', self.current_season)
            if year_match:
                season_year = int(year_match.group(1))
        
        # Look back for date patterns
        for i in range(max(0, line_num - lookback), line_num):
            line = all_lines[i].strip()
            
            # Pattern: "2023-04-01" or "2023/04/01" (ISO format)
            iso_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', line)
            if iso_match:
                year, month, day = iso_match.groups()
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            
            # Pattern: "Tue Apr/1" or "Tuesday April 1" (relative date)
            date_match = re.search(r'([A-Za-z]{3,9})\s+([A-Za-z]{3,9})[/\s]+(\d{1,2})', line)
            if date_match and season_year:
                try:
                    month_name = date_match.group(2)
                    day = int(date_match.group(3))
                    
                    # Map month names to numbers
                    month_map = {
                        'jan': 1, 'january': 1,
                        'feb': 2, 'february': 2,
                        'mar': 3, 'march': 3,
                        'apr': 4, 'april': 4,
                        'may': 5,
                        'jun': 6, 'june': 6,
                        'jul': 7, 'july': 7,
                        'aug': 8, 'august': 8,
                        'sep': 9, 'september': 9,
                        'oct': 10, 'october': 10,
                        'nov': 11, 'november': 11,
                        'dec': 12, 'december': 12
                    }
                    
                    month_lower = month_name.lower()[:3]
                    if month_lower in month_map:
                        month = month_map[month_lower]
                        # Determine year based on month and season
                        # Season runs Aug-Jul, so months Aug-Dec are season_year, Jan-Jul are season_year+1
                        if month >= 8:
                            year = season_year
                        else:
                            year = season_year + 1
                        
                        return f"{year}-{month:02d}-{day:02d}"
                except (ValueError, KeyError):
                    pass
        
        return None
    
    def _clean_team_name(self, name: str) -> str:
        """Clean and normalize team name.
        
        Args:
            name: Raw team name
            
        Returns:
            Cleaned team name
        """
        if not name:
            return ""
        
        # Remove extra whitespace
        name = ' '.join(name.split())
        
        # Remove common suffixes/prefixes that might be artifacts
        name = re.sub(r'^\d+\.\s*', '', name)  # Remove leading numbers
        name = re.sub(r'\s+@.*$', '', name)  # Remove venue info
        name = re.sub(r'\s*\(.*?\)\s*$', '', name)  # Remove parenthetical notes
        
        return name.strip()

