"""Parser for openfootball structured text format."""
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class FootballTxtParser:
    """Parses structured football.txt format from openfootball repository."""
    
    # Month name (3-letter) -> number, used for date headers and season rollover.
    _MONTHS = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }

    def __init__(self):
        """Initialize parser."""
        self.current_season = None
        self.current_competition = None
        self.current_stage = None
        self.current_round = None
        self.current_group = None
        self.current_date = None
        self.current_matchday = None
        
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
        self.current_date = None
        self.current_matchday = None
        
        lines = content.split('\n')
        teams = set()
        matches = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line or line.startswith('#'):
                i += 1
                continue

            # Date headers come first so every following match inherits the
            # nearest active date. Dates are tracked sequentially and reset per
            # parse; there is no fixed lookback window.
            header_date = self._parse_date_header(line)
            if header_date:
                self.current_date = header_date
                i += 1
                continue
            
            # Parse competition header
            if line.startswith('='):
                comp_name = line.lstrip('= ').strip()
                self.current_competition = self._normalize_competition(comp_name)
                logger.debug(f"Found competition: {comp_name}")
            
            # Parse new-format (2024/25+) league-phase header. Two shapes exist:
            # "League, Matchday 1" (with a matchday) and the plain "League phase"
            # used by the real EL/UCL files. Exclude knockout headings that also
            # mention a "phase" (e.g. "Knockout phase") so the league tag never
            # leaks into the play-off section.
            elif self._is_league_heading(line):
                self.current_stage = "LEAGUE_STAGE"
                self.current_round = None
                self.current_group = None
                self.current_matchday = self._extract_matchday(line)

            # Parse new-format play-off header, e.g. "Playoffs, Matchday 1"
            elif 'playoff' in line.lower() or 'play-off' in line.lower():
                self.current_stage = "KNOCKOUT_PLAYOFF"
                self.current_round = "Knockout Play-off"
                self.current_group = None
                self.current_matchday = self._extract_matchday(line)

            # Parse group stage header
            elif 'Group' in line and ('|' in line or ':' in line):
                self.current_stage = "GROUP_STAGE"
                self.current_round = None
                self.current_matchday = None
                # Extract group name
                group_match = re.search(r'Group\s+([A-H])', line, re.IGNORECASE)
                if group_match:
                    self.current_group = f"Group {group_match.group(1).upper()}"
                # Extract teams from group line
                team_names = self._extract_teams_from_group_line(line)
                for team_name in team_names:
                    teams.add(team_name)
            
            # Parse knockout stage headers (case-insensitive; covers one-word
            # "Quarterfinals"/"Semifinals" and hyphenated forms).
            elif self._is_knockout_heading(line):
                self.current_stage = "KNOCKOUT"
                self.current_group = None
                self.current_matchday = None
                self.current_round = self._extract_round_name(line)
                logger.debug(f"Found round: {self.current_round}")
            
            # Parse match lines
            elif self._is_match_line(line):
                match_data = self._parse_match_line(line)
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
    
    def _normalize_competition(self, comp_name: str) -> Optional[str]:
        """Normalize a competition name from a file header to a code.

        Returns ``None`` for an unrecognised name so an unknown header is never
        silently treated as Champions League. Callers that already know the
        expected competition keep using that explicit context.

        Args:
            comp_name: Competition name from file

        Returns:
            Competition code (CL, EL, UCL) or ``None`` when unrecognised
        """
        comp_name_lower = comp_name.lower()
        if 'champions league' in comp_name_lower:
            return "CL"
        elif 'europa league' in comp_name_lower:
            return "EL"
        elif 'conference league' in comp_name_lower:
            return "UCL"
        return None
    
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
    
    def _is_league_heading(self, line: str) -> bool:
        """True for league-phase headings, with or without a matchday.

        Matches both ``League, Matchday N`` and the plain ``▪ League phase``
        header used by the real EL/UCL files. Knockout headings that happen to
        contain the word "phase" (e.g. ``Knockout phase``) are excluded so the
        league tag is not applied to the play-off section.
        """
        low = line.lower()
        if 'league' not in low:
            return False
        if not ('matchday' in low or 'phase' in low):
            return False
        return not any(key in low for key in (
            'knockout', 'playoff', 'play-off', 'final',
        ))

    def _is_knockout_heading(self, line: str) -> bool:
        """True for knockout round headings, case-insensitively.

        Covers the actual new-format headings ("Finals, Quarterfinals",
        "Finals, Semifinals") as well as hyphenated and one-word spellings, so a
        heading is never missed and the previous round is not carried over.
        """
        low = line.lower()
        return any(key in low for key in (
            'round of 16', 'last 16',
            'quarterfinal', 'quarter-final', 'quarter final',
            'semifinal', 'semi-final', 'semi final',
            'final',
        ))

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
    
    def _extract_matchday(self, line: str) -> Optional[int]:
        """Numeric matchday from a header like ``League, Matchday 3``."""
        m = re.search(r'matchday\s+(\d+)', line, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _season_year(self) -> Optional[int]:
        """Start year parsed from the active season identifier."""
        if self.current_season:
            m = re.search(r'(\d{4})', str(self.current_season))
            if m:
                return int(m.group(1))
        return None

    def _parse_date_header(self, line: str) -> Optional[str]:
        """Resolve a date-header line to ISO ``YYYY-MM-DD``.

        Supports ISO headers and weekday/month/day headers with an optional
        explicit year (``Tue Sep 17 2024``, ``Wed Sep/7``, ``Tue Mar 4``). When
        the year is absent it is resolved from the active season using the
        August boundary. Returns ``None`` for any line that is not a date.
        """
        iso_match = re.search(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b', line)
        if iso_match:
            year, month, day = iso_match.groups()
            month_i, day_i = int(month), int(day)
            if 1 <= month_i <= 12 and 1 <= day_i <= 31:
                return f"{int(year):04d}-{month_i:02d}-{day_i:02d}"
            return None

        m = re.search(
            r'([A-Za-z]{3,9})\s+([A-Za-z]{3,9})[/\s]+(\d{1,2})(?:\s+(\d{4}))?',
            line,
        )
        if not m:
            return None

        month_i = self._MONTHS.get(m.group(2).lower()[:3])
        if not month_i:
            return None
        day_i = int(m.group(3))
        if not 1 <= day_i <= 31:
            return None

        if m.group(4):
            year_i = int(m.group(4))
        else:
            season_year = self._season_year()
            if season_year is None:
                return None
            year_i = season_year if month_i >= 8 else season_year + 1

        return f"{year_i:04d}-{month_i:02d}-{day_i:02d}"

    def _parse_match_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single match line using the active parser state.

        The kickoff time (colon or dot) is consumed as a prefix and never
        reaches the team names. The match score is the underlying result, not a
        penalty-shootout tally. The date/stage/matchday come from the nearest
        preceding headers.
        """
        try:
            # Kickoff prefix: "18:45 ..." or "20.45 ..." (colon and dot forms).
            time_match = re.match(r'^(\d{1,2})[:.](\d{2})\s+', line)
            match_time = None
            if time_match:
                match_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
                line = line[time_match.end():].strip()

            # All score candidates; annotated lines may carry shootout/aggregate.
            scores = list(re.finditer(r'(\d+)\s*-\s*(\d+)', line))
            if not scores:
                return None
            score_match = self._choose_score(scores, line)
            home_score = int(score_match.group(1))
            away_score = int(score_match.group(2))

            parts = re.split(r'\s+v\s+', line, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                home_raw = parts[0]
                rest = parts[1]
                score_pos = re.search(r'\d+\s*-\s*\d+', rest)
                away_raw = rest[:score_pos.start()] if score_pos else rest
            else:
                # Legacy fallback: split around the first score occurrence.
                first = scores[0]
                home_raw = line[:first.start()]
                away_raw = line[first.end():]

            home_team = self._clean_team_name(home_raw)
            away_team = self._clean_team_name(away_raw)

            if not home_team or not away_team:
                return None

            venue_match = re.search(r'@\s+([^,]+)', line)
            venue = venue_match.group(1).strip() if venue_match else None

            return {
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "date": self.current_date,
                "time": match_time,
                "status": "FINISHED",
                "stage": self.current_stage,
                "round": self.current_round,
                "group_name": self.current_group,
                "matchday": self.current_matchday,
                "competition_id": self.current_competition,
                "venue": venue,
            }

        except Exception as e:
            logger.debug(f"Error parsing match line '{line}': {e}")
            return None

    def _choose_score(self, scores: List[re.Match], line: str) -> re.Match:
        """Pick the underlying match score, skipping shootout/aggregate tallies.

        Penalty and extra-time annotations are written as ``1-4 pen. 0-1 a.e.t.
        (0-1, 0-1)``: the a.e.t. score is the real result and the ``pen.`` score
        is the shootout. Prefer the score immediately before the first
        ``a.e.t``/``aet`` marker, then before ``pen``; otherwise the first
        (regulation) score.
        """
        low = line.lower()
        markers = []
        for token in ('a.e.t', 'aet'):
            pos = low.find(token)
            if pos != -1:
                markers.append(pos)
                break
        pen_pos = low.find('pen')
        if pen_pos != -1:
            markers.append(pen_pos)

        for pos in markers:
            before = [s for s in scores if s.start() < pos]
            if before:
                return before[-1]
        return scores[0]

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

