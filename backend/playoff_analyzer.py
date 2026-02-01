"""Analyzer for play-off pair matchups based on historical common opponents."""
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Set, Tuple, Optional
from collections import defaultdict
from backend.database import Database
from backend.api_client import APIClient

logger = logging.getLogger(__name__)


class PlayoffAnalyzer:
    """Analyzes play-off pairs based on historical matches against common opponents."""
    
    def __init__(self, db: Database, api_client: APIClient):
        """Initialize playoff analyzer.
        
        Args:
            db: Database instance
            api_client: API client for fetching historical data
        """
        self.db = db
        self.api_client = api_client
        self.historical_years = int(os.getenv("HISTORICAL_YEARS", "10"))
    
    def _is_valid_date(self, date_str: Optional[str]) -> bool:
        """Check if a date string is valid and reasonable.
        
        Args:
            date_str: Date string to validate
            
        Returns:
            True if date is valid and within reasonable range
        """
        if not date_str:
            return False
        
        try:
            # Parse date, handling timezone
            date_str_clean = date_str.replace('Z', '+00:00') if 'Z' in date_str else date_str
            date = datetime.fromisoformat(date_str_clean)
            
            # Get current time - normalize both to UTC for comparison
            from datetime import timezone, timedelta
            now_utc = datetime.now(timezone.utc)
            
            # Convert date to UTC if it has timezone info
            if date.tzinfo:
                date_utc = date.astimezone(timezone.utc)
            else:
                # If no timezone, assume UTC
                date_utc = date.replace(tzinfo=timezone.utc)
            
            # Date should not be more than 2 years in the future
            two_years_from_now = datetime(now_utc.year + 2, now_utc.month, now_utc.day, tzinfo=timezone.utc)
            if date_utc > two_years_from_now:
                logger.debug(f"Date {date_str} ({date_utc}) is more than 2 years in the future")
                return False
            
            # Date should not be more than 6 months in the past (for scheduled/future matches)
            # This catches invalid placeholder dates like July 2025 when we're in 2026
            six_months_ago = now_utc - timedelta(days=180)
            if date_utc < six_months_ago:
                logger.debug(f"Date {date_str} ({date_utc}) is more than 6 months in the past (now: {now_utc})")
                return False
            
            return True
        except (ValueError, AttributeError, TypeError) as e:
            logger.debug(f"Invalid date format: {date_str}, error: {e}")
            return False
    
    def get_playoff_pairs(self, competition_id: str = "CL", stage: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get current play-off pairs from the competition.
        
        Identifies pairs from knockout stages:
        - Knockout round play-off (first knockout stage)
        - Round of 16
        - Quarter-finals
        - Semi-finals
        - Final
        
        Args:
            competition_id: Competition ID
            stage: Optional stage filter. If provided, only returns pairs for that stage.
                  If None, returns all knockout pairs.
            
        Returns:
            List of play-off pairs with team information and stage
        """
        if stage:
            return self.get_pairs_by_stage(stage, competition_id)
        # Get matches from current competition that are in knockout stages
        # Use stage/round information if available, otherwise fall back to matchday
        # Filter out group stage matches explicitly
        matches = self.db.fetchall("""
            SELECT 
                m.id,
                m.home_team_id,
                m.away_team_id,
                m.status,
                m.matchday,
                m.date,
                m.stage,
                m.round,
                t1.name as home_team_name,
                t1.crest as home_team_crest,
                t2.name as away_team_name,
                t2.crest as away_team_crest
            FROM matches m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE m.status IN ('SCHEDULED', 'TIMED', 'LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED')
            AND m.stage != 'LEAGUE_STAGE'  -- Exclude league/group stage matches
            AND (
                -- Explicit knockout stage indicators
                m.stage = 'KNOCKOUT_OUT' OR 
                m.stage = 'KNOCKOUT_ROUND' OR
                (m.round IS NOT NULL AND (
                    UPPER(m.round) LIKE '%PLAY_OFF%' OR
                    UPPER(m.round) LIKE '%ROUND_OF_16%' OR
                    UPPER(m.round) LIKE '%QUARTER_FINAL%' OR
                    UPPER(m.round) LIKE '%SEMI_FINAL%' OR
                    UPPER(m.round) LIKE '%FINAL%'
                )) OR
                -- Fallback: matchday >= 7 (knockout stages typically start here)
                -- Exclude group stage matches (where group_name is set or stage is LEAGUE_STAGE)
                (m.matchday >= 7 AND (m.group_name IS NULL OR m.group_name = '') AND (m.stage IS NULL OR m.stage != 'LEAGUE_STAGE'))
            )
            ORDER BY m.matchday, m.date
        """)
        
        # Group matches into pairs
        # In knockout stages, teams play each other in two-legged ties
        # We identify pairs by finding teams that play each other
        pairs_dict = {}
        
        for match in matches:
            home_id = match[1]
            away_id = match[2]
            matchday = match[4]
            api_stage = match[6]  # stage from API
            api_round = match[7]   # round from API
            
            # Skip if this is a league/group stage match
            if api_stage == 'LEAGUE_STAGE':
                continue
            
            # Create a unique key for the pair (sorted team IDs)
            pair_key = tuple(sorted([home_id, away_id]))
            
            # Determine stage from API data if available, otherwise use matchday
            stage = None
            if api_round:
                round_upper = api_round.upper()
                if 'PLAY_OFF' in round_upper or 'PLAYOFF' in round_upper:
                    stage = "Knockout Round Play-off"
                elif 'ROUND_OF_16' in round_upper or 'ROUND OF 16' in round_upper or 'LAST_16' in round_upper:
                    stage = "Round of 16"
                elif 'QUARTER' in round_upper:
                    stage = "Quarter-finals"
                elif 'SEMI' in round_upper:
                    stage = "Semi-finals"
                elif 'FINAL' in round_upper:
                    stage = "Final"
            
            # Also check stage field for knockout indicators
            if not stage and api_stage:
                stage_upper = api_stage.upper()
                if 'KNOCKOUT' in stage_upper or 'PLAY_OFF' in stage_upper:
                    stage = "Knockout Round Play-off"
                elif 'ROUND_OF_16' in stage_upper or 'LAST_16' in stage_upper:
                    stage = "Round of 16"
                elif 'QUARTER' in stage_upper:
                    stage = "Quarter-finals"
                elif 'SEMI' in stage_upper:
                    stage = "Semi-finals"
                elif 'FINAL' in stage_upper:
                    stage = "Final"
            
            # Fallback to matchday-based detection if stage not determined
            if not stage:
                if matchday <= 8:
                    stage = "Knockout Round Play-off"
                elif matchday <= 10:
                    stage = "Round of 16"
                elif matchday <= 12:
                    stage = "Quarter-finals"
                elif matchday <= 14:
                    stage = "Semi-finals"
                else:
                    stage = "Final"
            
            # If we haven't seen this pair, or if this is a more recent match, update it
            if pair_key not in pairs_dict:
                pairs_dict[pair_key] = {
                    "matchId": match[0],
                    "team1": {
                        "id": home_id,
                        "name": match[6],
                        "crest": match[7]
                    },
                    "team2": {
                        "id": away_id,
                        "name": match[8],
                        "crest": match[9]
                    },
                    "matchday": matchday,
                    "stage": stage,
                    "status": match[3],
                    "date": match[5] if match[5] and self._is_valid_date(match[5]) else None,
                    "apiStage": api_stage,
                    "apiRound": api_round
                }
            else:
                # Update with most recent match information
                existing_pair = pairs_dict[pair_key]
                if matchday > existing_pair["matchday"] or (
                    matchday == existing_pair["matchday"] and 
                    match[5] and existing_pair.get("date") and 
                    match[5] > existing_pair["date"]
                ):
                    existing_pair["matchId"] = match[0]
                    existing_pair["matchday"] = matchday
                    existing_pair["status"] = match[3]
                    if match[5] and self._is_valid_date(match[5]):
                        existing_pair["date"] = match[5]
        
        # Convert to list and sort by stage and matchday
        pairs = list(pairs_dict.values())
        
        # Sort by stage priority and matchday
        stage_priority = {
            "Knockout Round Play-off": 1,
            "Round of 16": 2,
            "Quarter-finals": 3,
            "Semi-finals": 4,
            "Final": 5
        }
        
        pairs.sort(key=lambda x: (
            stage_priority.get(x["stage"], 99),
            x["matchday"],
            x.get("date", "")
        ))
        
        return pairs
    
    def get_current_stage(self, competition_id: str = "CL") -> str:
        """Determine the current tournament stage based on match data.
        
        Args:
            competition_id: Competition ID
            
        Returns:
            Current stage: 'LEAGUE', 'KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL'
        """
        # Get all matches ordered by matchday DESC to find the highest stage
        matches = self.db.fetchall("""
            SELECT 
                m.stage,
                m.round,
                m.matchday,
                m.status,
                m.group_name
            FROM matches m
            WHERE m.status IN ('SCHEDULED', 'TIMED', 'LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED')
            ORDER BY m.matchday DESC, m.date DESC
            LIMIT 100
        """)
        
        if not matches:
            return 'LEAGUE'
        
        # Check for highest stage with active matches
        stages_found = set()
        
        for match in matches:
            stage = match[0]
            round_info = match[1]
            matchday = match[2]
            group_name = match[4]
            
            # Skip league stage matches
            if stage == 'LEAGUE_STAGE' or (group_name and group_name != ''):
                continue
            
            # Check round field first (most specific)
            if round_info:
                round_upper = round_info.upper()
                if 'PLAY_OFF' in round_upper or 'PLAYOFF' in round_upper:
                    stages_found.add('KNOCKOUT_PLAYOFF')
                elif 'ROUND_OF_16' in round_upper or 'ROUND OF 16' in round_upper or 'LAST_16' in round_upper:
                    stages_found.add('ROUND_OF_16')
                elif 'QUARTER' in round_upper:
                    stages_found.add('QUARTER_FINAL')
                elif 'SEMI' in round_upper:
                    stages_found.add('SEMI_FINAL')
                elif 'FINAL' in round_upper:
                    stages_found.add('FINAL')
            
            # Check stage field
            if stage:
                stage_upper = stage.upper()
                if 'KNOCKOUT' in stage_upper or 'PLAY_OFF' in stage_upper:
                    stages_found.add('KNOCKOUT_PLAYOFF')
                elif 'ROUND_OF_16' in stage_upper or 'LAST_16' in stage_upper:
                    stages_found.add('ROUND_OF_16')
                elif 'QUARTER' in stage_upper:
                    stages_found.add('QUARTER_FINAL')
                elif 'SEMI' in stage_upper:
                    stages_found.add('SEMI_FINAL')
                elif 'FINAL' in stage_upper:
                    stages_found.add('FINAL')
            
            # Fallback to matchday-based detection
            if matchday >= 7:
                if matchday <= 8:
                    stages_found.add('KNOCKOUT_PLAYOFF')
                elif matchday <= 10:
                    stages_found.add('ROUND_OF_16')
                elif matchday <= 12:
                    stages_found.add('QUARTER_FINAL')
                elif matchday <= 14:
                    stages_found.add('SEMI_FINAL')
                else:
                    stages_found.add('FINAL')
        
        # Return highest stage found (in order of progression)
        stage_priority = {
            'FINAL': 6,
            'SEMI_FINAL': 5,
            'QUARTER_FINAL': 4,
            'ROUND_OF_16': 3,
            'KNOCKOUT_PLAYOFF': 2,
            'LEAGUE': 1
        }
        
        if stages_found:
            highest_stage = max(stages_found, key=lambda s: stage_priority.get(s, 0))
            return highest_stage
        
        return 'LEAGUE'
    
    def get_pairs_by_stage(self, stage: str, competition_id: str = "CL") -> List[Dict[str, Any]]:
        """Get play-off pairs for a specific stage.
        
        Args:
            stage: Stage identifier ('KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL')
            competition_id: Competition ID
            
        Returns:
            List of play-off pairs for the specified stage
        """
        # Map stage names to database queries
        stage_filters = {
            'KNOCKOUT_PLAYOFF': {
                'round_patterns': ['%PLAY_OFF%', '%PLAYOFF%'],
                'stage_values': ['KNOCKOUT_OUT', 'KNOCKOUT_ROUND', 'PLAYOFFS', 'PLAY_OFF'],
                'matchday_range': (1, 10)  # Expanded to catch matchday 1 matches
            },
            'ROUND_OF_16': {
                'round_patterns': ['%ROUND_OF_16%', '%ROUND OF 16%', '%LAST_16%'],
                'stage_values': [],
                'matchday_range': (9, 10)
            },
            'QUARTER_FINAL': {
                'round_patterns': ['%QUARTER%'],
                'stage_values': [],
                'matchday_range': (11, 12)
            },
            'SEMI_FINAL': {
                'round_patterns': ['%SEMI%'],
                'stage_values': [],
                'matchday_range': (13, 14)
            },
            'FINAL': {
                'round_patterns': ['%FINAL%'],
                'stage_values': [],
                'matchday_range': (15, 999)
            }
        }
        
        if stage not in stage_filters:
            return []
        
        filters = stage_filters[stage]
        
        # Build query conditions
        conditions = ["m.status IN ('SCHEDULED', 'TIMED', 'LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED')"]
        # Don't exclude LEAGUE_STAGE here - we want to see what stages we actually have
        # conditions.append("m.stage != 'LEAGUE_STAGE'")
        
        round_conditions = []
        if filters['round_patterns']:
            for pattern in filters['round_patterns']:
                round_conditions.append(f"UPPER(m.round) LIKE '{pattern}'")
        
        stage_conditions = []
        if filters['stage_values']:
            for value in filters['stage_values']:
                stage_conditions.append(f"m.stage = '{value}'")
        
        matchday_min, matchday_max = filters['matchday_range']
        
        # Combine conditions
        stage_round_conditions = []
        if round_conditions:
            stage_round_conditions.append(f"({' OR '.join(round_conditions)})")
        if stage_conditions:
            stage_round_conditions.append(f"({' OR '.join(stage_conditions)})")
        if matchday_min:
            stage_round_conditions.append(f"(m.matchday >= {matchday_min} AND m.matchday <= {matchday_max} AND (m.group_name IS NULL OR m.group_name = '') AND (m.stage IS NULL OR m.stage != 'LEAGUE_STAGE'))")
        
        if stage_round_conditions:
            conditions.append(f"({' OR '.join(stage_round_conditions)})")
        
        query = f"""
            SELECT 
                m.id,
                m.home_team_id,
                m.away_team_id,
                m.status,
                m.matchday,
                m.date,
                m.stage,
                m.round,
                t1.name as home_team_name,
                t1.crest as home_team_crest,
                t2.name as away_team_name,
                t2.crest as away_team_crest
            FROM matches m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE {' AND '.join(conditions)}
            ORDER BY m.matchday, m.date
        """
        
        matches = self.db.fetchall(query)
        
        # Group matches into pairs
        pairs_dict = {}
        stage_display_name = {
            'KNOCKOUT_PLAYOFF': 'Knockout Round Play-off',
            'ROUND_OF_16': 'Round of 16',
            'QUARTER_FINAL': 'Quarter-finals',
            'SEMI_FINAL': 'Semi-finals',
            'FINAL': 'Final'
        }
        
        for match in matches:
            home_id = match[1]
            away_id = match[2]
            matchday = match[4]
            api_stage = match[6]
            api_round = match[7]
            
            if api_stage == 'LEAGUE_STAGE':
                continue
            
            pair_key = tuple(sorted([home_id, away_id]))
            
            if pair_key not in pairs_dict:
                pairs_dict[pair_key] = {
                    "matchId": match[0],
                    "team1": {
                        "id": home_id,
                        "name": match[8],
                        "crest": match[9]
                    },
                    "team2": {
                        "id": away_id,
                        "name": match[10],
                        "crest": match[11]
                    },
                    "matchday": matchday,
                    "stage": stage_display_name.get(stage, stage),
                    "status": match[3],
                    "date": match[5] if match[5] and self._is_valid_date(match[5]) else None,
                    "apiStage": api_stage,
                    "apiRound": api_round
                }
            else:
                existing_pair = pairs_dict[pair_key]
                if matchday > existing_pair["matchday"] or (
                    matchday == existing_pair["matchday"] and 
                    match[5] and existing_pair.get("date") and 
                    match[5] > existing_pair["date"]
                ):
                    existing_pair["matchId"] = match[0]
                    existing_pair["matchday"] = matchday
                    existing_pair["status"] = match[3]
                    if match[5] and self._is_valid_date(match[5]):
                        existing_pair["date"] = match[5]
        
        pairs = list(pairs_dict.values())
        
        # Calculate win probability for each pair using the same method as analyze_pair
        # This ensures consistency between pair cards and historical analysis popup
        for pair in pairs:
            try:
                # Use the same calculation as analyze_pair for consistency
                # This uses calculate_league_table which gets the same data as the popup
                common_opponents = self.find_common_opponents(
                    pair["team1"]["id"], 
                    pair["team2"]["id"]
                )
                
                if common_opponents:
                    # Use the same calculation as analyze_pair - get full league table stats
                    # This ensures the probabilities match exactly
                    league_table_result = self.calculate_league_table(
                        pair["team1"]["id"],
                        pair["team2"]["id"],
                        common_opponents
                    )
                    win_prob = league_table_result.get("winProbability", {
                        "team1Win": 0.5,
                        "team2Win": 0.5,
                        "draw": 0.0,
                        "method": "points_per_game"
                    })
                else:
                    # No common opponents, use equal probability
                    win_prob = {
                        "team1Win": 0.5,
                        "team2Win": 0.5,
                        "draw": 0.0,
                        "method": "no_common_opponents"
                    }
                
                pair["winProbability"] = win_prob
            except Exception as e:
                logger.debug(f"Could not calculate win probability for pair {pair['team1']['id']} vs {pair['team2']['id']}: {e}")
                pair["winProbability"] = {
                    "team1Win": 0.5,
                    "team2Win": 0.5,
                    "draw": 0.0,
                    "method": "error"
                }
        
        # Sort by matchday and date
        pairs.sort(key=lambda x: (x.get("matchday", 0), x.get("date", "")))
        
        return pairs
    
    def get_team_historical_matches(self, team_id: int, years_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get historical matches for a team from European competitions.
        
        Args:
            team_id: Team ID
            years_back: Number of years to look back (default: self.historical_years)
            
        Returns:
            List of historical matches
        """
        if years_back is None:
            years_back = self.historical_years
        
        cutoff_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")
        
        # Get matches from database (European competitions)
        matches = self.db.fetchall("""
            SELECT 
                m.id,
                m.home_team_id,
                m.away_team_id,
                m.home_score,
                m.away_score,
                m.date,
                m.status,
                t1.name as home_team_name,
                t2.name as away_team_name
            FROM matches m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE (m.home_team_id = ? OR m.away_team_id = ?)
            AND m.status = 'FINISHED'
            AND m.date >= ?
            ORDER BY m.date DESC
        """, (team_id, team_id, cutoff_date))
        
        historical_matches = []
        for match in matches:
            is_home = match[1] == team_id
            opponent_id = match[2] if is_home else match[1]
            opponent_name = match[8] if is_home else match[7]
            
            team_score = match[3] if is_home else match[4]
            opponent_score = match[4] if is_home else match[3]
            
            # Determine outcome
            if team_score is None or opponent_score is None:
                outcome = 'unknown'
            elif team_score > opponent_score:
                outcome = 'win'
            elif team_score < opponent_score:
                outcome = 'loss'
            else:
                outcome = 'draw'
            
            historical_matches.append({
                "matchId": match[0],
                "opponentId": opponent_id,
                "opponentName": opponent_name,
                "teamScore": team_score,
                "opponentScore": opponent_score,
                "outcome": outcome,
                "date": match[5],
                "isHome": is_home
            })
        
        return historical_matches
    
    def find_common_opponents(self, team1_id: int, team2_id: int) -> Set[int]:
        """Find common opponents between two teams.
        
        Args:
            team1_id: First team ID
            team2_id: Second team ID
            
        Returns:
            Set of common opponent team IDs
        """
        # Get opponents for team 1
        team1_opponents = self.db.fetchall("""
            SELECT DISTINCT 
                CASE 
                    WHEN home_team_id = ? THEN away_team_id
                    ELSE home_team_id
                END as opponent_id
            FROM matches
            WHERE (home_team_id = ? OR away_team_id = ?)
            AND status = 'FINISHED'
        """, (team1_id, team1_id, team1_id))
        
        team1_opponent_set = {row[0] for row in team1_opponents if row[0] != team1_id and row[0] != team2_id}
        
        # Get opponents for team 2
        team2_opponents = self.db.fetchall("""
            SELECT DISTINCT 
                CASE 
                    WHEN home_team_id = ? THEN away_team_id
                    ELSE home_team_id
                END as opponent_id
            FROM matches
            WHERE (home_team_id = ? OR away_team_id = ?)
            AND status = 'FINISHED'
        """, (team2_id, team2_id, team2_id))
        
        team2_opponent_set = {row[0] for row in team2_opponents if row[0] != team1_id and row[0] != team2_id}
        
        # Find intersection
        common_opponents = team1_opponent_set & team2_opponent_set
        
        return common_opponents
    
    def calculate_league_table(self, team1_id: int, team2_id: int, 
                              common_opponents: Set[int]) -> Dict[str, Any]:
        """Calculate a league table based on results against common opponents.
        
        Includes all teams (team1, team2, and all common opponents) and their
        matches against each other.
        
        Args:
            team1_id: First team ID
            team2_id: Second team ID
            common_opponents: Set of common opponent team IDs
            
        Returns:
            League table data with statistics for all teams
        """
        if not common_opponents:
            return {
                "team1": self._get_team_stats(team1_id, []),
                "team2": self._get_team_stats(team2_id, []),
                "commonOpponents": [],
                "fullLeagueTable": []
            }
        
        # Get all matches against common opponents for both teams
        # Use parameterized query to avoid SQL injection
        if not common_opponents:
            return {
                "team1": self._get_team_stats(team1_id, []),
                "team2": self._get_team_stats(team2_id, []),
                "commonOpponents": []
            }
        
        # Build parameter lists properly - convert set to list for consistent ordering
        common_opponents_list = list(common_opponents)
        placeholders = ','.join(['?' for _ in common_opponents_list])
        
        # Team 1 query parameters: 6 team1_id params + common opponents
        team1_params = [team1_id] * 6 + common_opponents_list
        
        team1_matches = self.db.fetchall(f"""
            SELECT 
                CASE 
                    WHEN home_team_id = ? THEN away_team_id
                    ELSE home_team_id
                END as opponent_id,
                CASE 
                    WHEN home_team_id = ? THEN home_score
                    ELSE away_score
                END as team_score,
                CASE 
                    WHEN home_team_id = ? THEN away_score
                    ELSE home_score
                END as opponent_score,
                m.date
            FROM matches m
            WHERE (m.home_team_id = ? OR m.away_team_id = ?)
            AND (
                CASE 
                    WHEN m.home_team_id = ? THEN m.away_team_id
                    ELSE m.home_team_id
                END IN ({placeholders})
            )
            AND m.status = 'FINISHED'
            ORDER BY m.date DESC
        """, tuple(team1_params))
        
        # Team 2 query parameters: 6 team2_id params + common opponents
        team2_params = [team2_id] * 6 + common_opponents_list
        
        team2_matches = self.db.fetchall(f"""
            SELECT 
                CASE 
                    WHEN home_team_id = ? THEN away_team_id
                    ELSE home_team_id
                END as opponent_id,
                CASE 
                    WHEN home_team_id = ? THEN home_score
                    ELSE away_score
                END as team_score,
                CASE 
                    WHEN home_team_id = ? THEN away_score
                    ELSE home_score
                END as opponent_score,
                m.date
            FROM matches m
            WHERE (m.home_team_id = ? OR m.away_team_id = ?)
            AND (
                CASE 
                    WHEN m.home_team_id = ? THEN m.away_team_id
                    ELSE m.home_team_id
                END IN ({placeholders})
            )
            AND m.status = 'FINISHED'
            ORDER BY m.date DESC
        """, tuple(team2_params))
        
        # Get all team information (team1, team2, and common opponents)
        all_team_ids = {team1_id, team2_id} | common_opponents
        team_info = {}
        for team_id in all_team_ids:
            team_data = self.db.fetchone("SELECT id, name, crest FROM teams WHERE id = ?", (team_id,))
            if team_data:
                team_info[team_id] = {
                    "id": team_data[0],
                    "name": team_data[1],
                    "crest": team_data[2]
                }
        
        # Get all matches between all teams in this group
        all_team_ids_list = list(all_team_ids)
        all_placeholders = ','.join(['?' for _ in all_team_ids_list])
        
        # Get all matches where both teams are in our group
        all_matches = self.db.fetchall(f"""
            SELECT 
                m.home_team_id,
                m.away_team_id,
                m.home_score,
                m.away_score,
                m.date
            FROM matches m
            WHERE m.home_team_id IN ({all_placeholders})
            AND m.away_team_id IN ({all_placeholders})
            AND m.status = 'FINISHED'
            ORDER BY m.date DESC
        """, tuple(all_team_ids_list) + tuple(all_team_ids_list))
        
        # Calculate statistics for all teams
        all_teams_stats = {}
        for team_id in all_team_ids:
            all_teams_stats[team_id] = {
                "teamId": team_id,
                "teamName": team_info[team_id]["name"] if team_id in team_info else "Unknown",
                "teamCrest": team_info[team_id]["crest"] if team_id in team_info else None,
                "played": 0,
                "won": 0,
                "drawn": 0,
                "lost": 0,
                "goalsFor": 0,
                "goalsAgainst": 0,
                "goalDifference": 0,
                "points": 0,
                "pointsPercentage": 0.0,
                "solkoffCoefficient": 0.0,
                "strengthPerGame": 0.0,
                "matches": []
            }
        
        # Process all matches
        for match in all_matches:
            home_id = match[0]
            away_id = match[1]
            home_score = match[2]
            away_score = match[3]
            match_date = match[4]
            
            if home_score is None or away_score is None:
                continue
            
            # Update home team stats
            if home_id in all_teams_stats:
                all_teams_stats[home_id]["played"] += 1
                all_teams_stats[home_id]["goalsFor"] += home_score
                all_teams_stats[home_id]["goalsAgainst"] += away_score
                
                if home_score > away_score:
                    all_teams_stats[home_id]["won"] += 1
                    all_teams_stats[home_id]["points"] += 3
                    outcome = "win"
                elif home_score < away_score:
                    all_teams_stats[home_id]["lost"] += 1
                    outcome = "loss"
                else:
                    all_teams_stats[home_id]["drawn"] += 1
                    all_teams_stats[home_id]["points"] += 1
                    outcome = "draw"
                
                all_teams_stats[home_id]["matches"].append({
                    "opponentId": away_id,
                    "opponentName": team_info[away_id]["name"] if away_id in team_info else "Unknown",
                    "opponentCrest": team_info[away_id]["crest"] if away_id in team_info else None,
                    "teamScore": home_score,
                    "opponentScore": away_score,
                    "outcome": outcome,
                    "date": match_date,
                    "isHome": True
                })
            
            # Update away team stats
            if away_id in all_teams_stats:
                all_teams_stats[away_id]["played"] += 1
                all_teams_stats[away_id]["goalsFor"] += away_score
                all_teams_stats[away_id]["goalsAgainst"] += home_score
                
                if away_score > home_score:
                    all_teams_stats[away_id]["won"] += 1
                    all_teams_stats[away_id]["points"] += 3
                    outcome = "win"
                elif away_score < home_score:
                    all_teams_stats[away_id]["lost"] += 1
                    outcome = "loss"
                else:
                    all_teams_stats[away_id]["drawn"] += 1
                    all_teams_stats[away_id]["points"] += 1
                    outcome = "draw"
                
                all_teams_stats[away_id]["matches"].append({
                    "opponentId": home_id,
                    "opponentName": team_info[home_id]["name"] if home_id in team_info else "Unknown",
                    "opponentCrest": team_info[home_id]["crest"] if home_id in team_info else None,
                    "teamScore": away_score,
                    "opponentScore": home_score,
                    "outcome": outcome,
                    "date": match_date,
                    "isHome": False
                })
        
        # Calculate goal differences and additional metrics
        for team_id in all_teams_stats:
            stats = all_teams_stats[team_id]
            stats["goalDifference"] = stats["goalsFor"] - stats["goalsAgainst"]
            
            # Calculate percentage of points won
            max_points = stats["played"] * 3
            stats["pointsPercentage"] = (stats["points"] / max_points * 100) if max_points > 0 else 0
            
            # Calculate Solkoff coefficient (average points per game of opponents)
            # Get all opponents this team has faced in the matches
            opponent_ids = set()
            for match in stats["matches"]:
                opponent_ids.add(match["opponentId"])
            
            # Calculate Solkoff: average points per game of all opponents
            opponent_ppg_list = []
            for opp_id in opponent_ids:
                if opp_id in all_teams_stats:
                    opp_stats = all_teams_stats[opp_id]
                    opp_played = opp_stats.get("played", 0)
                    if opp_played > 0:
                        opp_ppg = opp_stats.get("points", 0) / opp_played
                        opponent_ppg_list.append(opp_ppg)
            
            if opponent_ppg_list:
                solkoff_value = sum(opponent_ppg_list) / len(opponent_ppg_list)
            else:
                solkoff_value = 0.0
            
            stats["solkoffCoefficient"] = round(solkoff_value, 3)
            
            # Calculate Strength ((points percentage / 100) * solkoff)
            # Same formula as main standings: (Points % × Solkoff) / 100
            stats["strengthPerGame"] = (stats["pointsPercentage"] / 100.0) * solkoff_value
        
        # Sort league table: 
        # 1. Points percentage DESC
        # 2. Solkoff coefficient DESC
        # 3. Strength per game DESC
        # 4. Goals scored DESC
        full_league_table = sorted(
            all_teams_stats.values(),
            key=lambda x: (
                -x["pointsPercentage"],
                -x["solkoffCoefficient"],
                -x["strengthPerGame"],
                -x["goalsFor"]
            )
        )
        
        # Calculate win probability for team1 vs team2
        team1_stats = all_teams_stats.get(team1_id, {})
        team2_stats = all_teams_stats.get(team2_id, {})
        
        # Get main league table strength ratings for both teams
        team1_main_strength = self._get_main_league_strength(team1_id)
        team2_main_strength = self._get_main_league_strength(team2_id)
        
        # Calculate win probability using combined strength (50% main league + 50% historical)
        win_probability = self._calculate_win_probability(
            team1_stats, 
            team2_stats,
            team1_main_strength=team1_main_strength,
            team2_main_strength=team2_main_strength
        )
        
        return {
            "team1": team1_stats,
            "team2": team2_stats,
            "commonOpponents": [team_info[opp_id] for opp_id in common_opponents if opp_id in team_info],
            "fullLeagueTable": full_league_table,
            "winProbability": win_probability
        }
    
    def _calculate_stats(self, team_id: int, matches: List[Tuple], 
                        opponent_info: Dict[int, Dict]) -> Dict[str, Any]:
        """Calculate statistics for a team against common opponents.
        
        Args:
            team_id: Team ID
            matches: List of match tuples
            opponent_info: Dictionary of opponent information
            
        Returns:
            Team statistics dictionary
        """
        stats = {
            "teamId": team_id,
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "goalsFor": 0,
            "goalsAgainst": 0,
            "goalDifference": 0,
            "points": 0,
            "matches": []
        }
        
        for match in matches:
            opponent_id = match[0]
            team_score = match[1]
            opponent_score = match[2]
            match_date = match[3]
            
            if team_score is None or opponent_score is None:
                continue
            
            stats["played"] += 1
            stats["goalsFor"] += team_score
            stats["goalsAgainst"] += opponent_score
            
            if team_score > opponent_score:
                stats["won"] += 1
                stats["points"] += 3
                outcome = "win"
            elif team_score < opponent_score:
                stats["lost"] += 1
                outcome = "loss"
            else:
                stats["drawn"] += 1
                stats["points"] += 1
                outcome = "draw"
            
            opponent = opponent_info.get(opponent_id, {})
            stats["matches"].append({
                "opponentId": opponent_id,
                "opponentName": opponent.get("name", "Unknown"),
                "opponentCrest": opponent.get("crest"),
                "teamScore": team_score,
                "opponentScore": opponent_score,
                "outcome": outcome,
                "date": match_date
            })
        
        stats["goalDifference"] = stats["goalsFor"] - stats["goalsAgainst"]
        
        return stats
    
    def _get_team_stats(self, team_id: int, matches: List[Dict]) -> Dict[str, Any]:
        """Get basic team statistics structure.
        
        Args:
            team_id: Team ID
            matches: List of matches (empty if no common opponents)
            
        Returns:
            Team statistics dictionary
        """
        team_data = self.db.fetchone("SELECT id, name, crest FROM teams WHERE id = ?", (team_id,))
        if not team_data:
            return {
                "teamId": team_id,
                "teamName": "Unknown",
                "teamCrest": None,
                "played": 0,
                "won": 0,
                "drawn": 0,
                "lost": 0,
                "goalsFor": 0,
                "goalsAgainst": 0,
                "goalDifference": 0,
                "points": 0,
                "matches": []
            }
        
        return {
            "teamId": team_data[0],
            "teamName": team_data[1],
            "teamCrest": team_data[2],
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "goalsFor": 0,
            "goalsAgainst": 0,
            "goalDifference": 0,
            "points": 0,
            "matches": []
        }
    
    def _calculate_quick_win_probability(self, team1_id: int, team2_id: int) -> Dict[str, Any]:
        """Calculate quick win probability for a pair without full analysis.
        
        This is a lightweight version that uses only direct historical matches
        between the two teams if available, otherwise falls back to basic stats.
        
        Args:
            team1_id: First team ID
            team2_id: Second team ID
            
        Returns:
            Dictionary with win probabilities
        """
        # Try to find direct matches between the two teams
        direct_matches = self.db.fetchall("""
            SELECT 
                m.home_team_id,
                m.away_team_id,
                m.home_score,
                m.away_score
            FROM matches m
            WHERE ((m.home_team_id = ? AND m.away_team_id = ?) 
                   OR (m.home_team_id = ? AND m.away_team_id = ?))
            AND m.status = 'FINISHED'
            AND m.home_score IS NOT NULL
            AND m.away_score IS NOT NULL
            ORDER BY m.date DESC
            LIMIT 10
        """, (team1_id, team2_id, team2_id, team1_id))
        
        if direct_matches and len(direct_matches) >= 2:
            # Calculate based on direct head-to-head
            team1_wins = 0
            team2_wins = 0
            draws = 0
            
            for match in direct_matches:
                home_id = match[0]
                away_id = match[1]
                home_score = match[2]
                away_score = match[3]
                
                if home_score > away_score:
                    if home_id == team1_id:
                        team1_wins += 1
                    else:
                        team2_wins += 1
                elif home_score < away_score:
                    if home_id == team1_id:
                        team2_wins += 1
                    else:
                        team1_wins += 1
                else:
                    draws += 1
            
            total = len(direct_matches)
            if total > 0:
                # Calculate win probabilities (excluding draws)
                non_draw_matches = team1_wins + team2_wins
                if non_draw_matches > 0:
                    team1_win_prob = team1_wins / non_draw_matches
                    team2_win_prob = team2_wins / non_draw_matches
                else:
                    # If all matches were draws, split evenly
                    team1_win_prob = team2_win_prob = 0.5
            else:
                team1_win_prob = team2_win_prob = 0.5
            
            return {
                "team1Win": round(team1_win_prob, 3),
                "team2Win": round(team2_win_prob, 3),
                "draw": 0.0,
                "method": "head_to_head"
            }
        
        # Fallback: use common opponents analysis (lightweight)
        try:
            common_opponents = self.find_common_opponents(team1_id, team2_id)
            if common_opponents:
                # Get basic stats from common opponents
                team1_matches = self.db.fetchall("""
                    SELECT m.home_score, m.away_score, m.home_team_id
                    FROM matches m
                    WHERE m.status = 'FINISHED'
                    AND m.home_score IS NOT NULL
                    AND m.away_score IS NOT NULL
                    AND ((m.home_team_id = ? AND m.away_team_id IN ({placeholders}))
                         OR (m.away_team_id = ? AND m.home_team_id IN ({placeholders})))
                    ORDER BY m.date DESC
                    LIMIT 20
                """.format(placeholders=','.join(['?' for _ in common_opponents])),
                (team1_id,) + tuple(common_opponents) + (team1_id,) + tuple(common_opponents))
                
                team2_matches = self.db.fetchall("""
                    SELECT m.home_score, m.away_score, m.home_team_id
                    FROM matches m
                    WHERE m.status = 'FINISHED'
                    AND m.home_score IS NOT NULL
                    AND m.away_score IS NOT NULL
                    AND ((m.home_team_id = ? AND m.away_team_id IN ({placeholders}))
                         OR (m.away_team_id = ? AND m.home_team_id IN ({placeholders})))
                    ORDER BY m.date DESC
                    LIMIT 20
                """.format(placeholders=','.join(['?' for _ in common_opponents])),
                (team2_id,) + tuple(common_opponents) + (team2_id,) + tuple(common_opponents))
                
                team1_stats = self._get_quick_stats(team1_id, team1_matches)
                team2_stats = self._get_quick_stats(team2_id, team2_matches)
                
                return self._calculate_win_probability(team1_stats, team2_stats)
        except Exception as e:
            logger.debug(f"Error in quick win probability calculation: {e}")
        
        # Final fallback: equal probability (normalized to sum to 1.0)
        return {
            "team1Win": 0.5,
            "team2Win": 0.5,
            "draw": 0.0,
            "method": "no_data"
        }
    
    def _get_quick_stats(self, team_id: int, matches: List[Tuple]) -> Dict[str, Any]:
        """Get quick stats from matches list."""
        stats = {
            "played": len(matches),
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "points": 0
        }
        
        for match in matches:
            home_id = match[2]
            home_score = match[0]
            away_score = match[1]
            
            is_home = (home_id == team_id)
            team_score = home_score if is_home else away_score
            opp_score = away_score if is_home else home_score
            
            if team_score > opp_score:
                stats["won"] += 1
                stats["points"] += 3
            elif team_score < opp_score:
                stats["lost"] += 1
            else:
                stats["drawn"] += 1
                stats["points"] += 1
        
        return stats
    
    def _get_main_league_strength(self, team_id: int) -> float:
        """Get main league table strength rating for a team.
        
        Args:
            team_id: Team ID
            
        Returns:
            Strength rating from main league table, or 0.0 if not available
        """
        try:
            result = self.db.fetchone("""
                SELECT 
                    CASE 
                        WHEN s.played > 0 THEN 
                            (s.points * 100.0 / (s.played * 3)) * CAST(COALESCE(sc.solkoff_value, 0) AS REAL) / 100.0
                        ELSE 0
                    END as strength_score
                FROM standings s
                LEFT JOIN solkoff_coefficients sc ON s.team_id = sc.team_id
                WHERE s.team_id = ?
            """, (team_id,))
            
            if result and result[0] is not None:
                return float(result[0])
            return 0.0
        except Exception as e:
            logger.debug(f"Could not get main league strength for team {team_id}: {e}")
            return 0.0
    
    def _calculate_win_probability(self, team1_stats: Dict, team2_stats: Dict, 
                                   team1_main_strength: float = None, 
                                   team2_main_strength: float = None) -> Dict[str, Any]:
        """Calculate win probability for team1 vs team2 based on combined strength ratings.
        
        Uses 50% weight from main league table strength and 50% weight from historical mini-table strength.
        
        Args:
            team1_stats: Statistics for team 1 from historical mini-table
            team2_stats: Statistics for team 2 from historical mini-table
            team1_main_strength: Main league table strength rating for team 1
            team2_main_strength: Main league table strength rating for team 2
            
        Returns:
            Dictionary with win probabilities
        """
        # Get historical mini-table strength ratings
        team1_historical_strength = team1_stats.get("strengthPerGame", 0.0) if team1_stats else 0.0
        team2_historical_strength = team2_stats.get("strengthPerGame", 0.0) if team2_stats else 0.0
        
        # Use main league strength if provided, otherwise default to 0
        team1_main = team1_main_strength if team1_main_strength is not None else 0.0
        team2_main = team2_main_strength if team2_main_strength is not None else 0.0
        
        # Combine strengths: 50% main league + 50% historical mini-table
        team1_combined_strength = (team1_main * 0.5) + (team1_historical_strength * 0.5)
        team2_combined_strength = (team2_main * 0.5) + (team2_historical_strength * 0.5)
        
        # Calculate win probabilities based on combined strength (excluding draws)
        total_combined_strength = team1_combined_strength + team2_combined_strength
        
        if total_combined_strength == 0:
            # Equal probability if no data
            team1_win_prob = 0.5
            team2_win_prob = 0.5
            
            return {
                "team1Win": round(team1_win_prob, 3),
                "team2Win": round(team2_win_prob, 3),
                "draw": 0.0,
                "method": "equal_strength"
            }
        
        # Normalize strengths to sum to 1.0 (excluding draws)
        team1_win_prob = team1_combined_strength / total_combined_strength
        team2_win_prob = team2_combined_strength / total_combined_strength
        
        return {
            "team1Win": round(team1_win_prob, 3),
            "team2Win": round(team2_win_prob, 3),
            "draw": 0.0,
            "method": "combined_strength",
            "team1MainStrength": round(team1_main, 3),
            "team2MainStrength": round(team2_main, 3),
            "team1HistoricalStrength": round(team1_historical_strength, 3),
            "team2HistoricalStrength": round(team2_historical_strength, 3)
        }
    
    def analyze_pair(self, team1_id: int, team2_id: int) -> Dict[str, Any]:
        """Analyze a play-off pair and return league table.
        
        Args:
            team1_id: First team ID
            team2_id: Second team ID
            
        Returns:
            Complete analysis with league table
        """
        # Find common opponents
        common_opponents = self.find_common_opponents(team1_id, team2_id)
        
        # Calculate league table
        league_table = self.calculate_league_table(team1_id, team2_id, common_opponents)
        
        # Get team information
        team1_info = self.db.fetchone("SELECT id, name, crest FROM teams WHERE id = ?", (team1_id,))
        team2_info = self.db.fetchone("SELECT id, name, crest FROM teams WHERE id = ?", (team2_id,))
        
        return {
            "team1": {
                "id": team1_info[0] if team1_info else team1_id,
                "name": team1_info[1] if team1_info else "Unknown",
                "crest": team1_info[2] if team1_info else None
            },
            "team2": {
                "id": team2_info[0] if team2_info else team2_id,
                "name": team2_info[1] if team2_info else "Unknown",
                "crest": team2_info[2] if team2_info else None
            },
            "commonOpponentsCount": len(common_opponents),
            "historicalYears": self.historical_years,
            "leagueTable": league_table
        }

