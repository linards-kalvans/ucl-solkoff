"""Service for fetching and storing match and standings data."""
import logging
from datetime import datetime
from typing import Dict, Any, List
from backend.database import Database
from backend.api_client import APIClient

logger = logging.getLogger(__name__)


class DataService:
    """Service for data ingestion and storage."""
    
    def __init__(self, db: Database, api_client: APIClient):
        """Initialize data service.
        
        Args:
            db: Database instance
            api_client: API client instance
        """
        self.db = db
        self.api_client = api_client
    
    def sync_teams(self, competition_id: str = "CL"):
        """Sync teams from API to database.
        
        Args:
            competition_id: Competition ID
        """
        standings_data = self.api_client.get_competition_standings(competition_id)
        
        # Extract teams from standings
        teams = set()
        if "standings" in standings_data:
            for group in standings_data["standings"]:
                if "table" in group:
                    for entry in group["table"]:
                        team = entry.get("team", {})
                        teams.add((
                            team.get("id"),
                            team.get("name"),
                            team.get("tla"),  # Three letter abbreviation
                            team.get("crest")
                        ))
        
        # Also get teams from matches
        matches_data = self.api_client.get_competition_matches(competition_id)
        if "matches" in matches_data:
            for match in matches_data["matches"]:
                home_team = match.get("homeTeam", {})
                away_team = match.get("awayTeam", {})
                
                teams.add((
                    home_team.get("id"),
                    home_team.get("name"),
                    home_team.get("shortName"),
                    home_team.get("crest")
                ))
                teams.add((
                    away_team.get("id"),
                    away_team.get("name"),
                    away_team.get("shortName"),
                    away_team.get("crest")
                ))
        
        # Insert or update teams
        teams_inserted = 0
        teams_skipped = 0
        
        for team_id, name, code, crest in teams:
            if not team_id:
                teams_skipped += 1
                continue
            
            try:
                self.db.execute("""
                    INSERT INTO teams (id, name, code, crest)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        code = excluded.code,
                        crest = excluded.crest
                """, (team_id, name, code, crest))
                teams_inserted += 1
            except Exception as e:
                logger.warning(f"Skipping team {team_id}: {e}")
                teams_skipped += 1
        
        self.db.commit()
        logger.info(f"Synced {teams_inserted} teams, skipped {teams_skipped} invalid teams")
    
    def sync_matches(self, competition_id: str = "CL"):
        """Sync matches from API to database.
        
        Args:
            competition_id: Competition ID
        """
        matches_data = self.api_client.get_competition_matches(competition_id)
        
        if "matches" not in matches_data:
            return
        
        matches_inserted = 0
        matches_skipped = 0
        
        for match in matches_data["matches"]:
            match_id = match.get("id")
            home_team = match.get("homeTeam", {})
            away_team = match.get("awayTeam", {})
            score = match.get("score", {})
            full_time = score.get("fullTime", {})
            
            # Validate required fields
            home_team_id = home_team.get("id")
            away_team_id = away_team.get("id")
            
            # Skip matches with missing team IDs
            if not match_id or not home_team_id or not away_team_id:
                matches_skipped += 1
                continue
            
            try:
                # Extract stage, round, and group information from match
                stage = match.get("stage")
                round_info = match.get("round")  # Can be a string or object
                if isinstance(round_info, dict):
                    round_name = round_info.get("name") or round_info.get("round")
                else:
                    round_name = round_info
                
                group_info = match.get("group")
                if isinstance(group_info, dict):
                    group_name = group_info.get("name") or group_info.get("group")
                else:
                    group_name = group_info
                
                self.db.execute("""
                    INSERT INTO matches (
                        id, home_team_id, away_team_id, home_score, away_score,
                        matchday, date, status, stage, round, group_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        status = excluded.status,
                        stage = excluded.stage,
                        round = excluded.round,
                        group_name = excluded.group_name
                """, (
                    match_id,
                    home_team_id,
                    away_team_id,
                    full_time.get("home"),
                    full_time.get("away"),
                    match.get("matchday"),
                    match.get("utcDate"),
                    match.get("status"),
                    stage,
                    round_name,
                    group_name
                ))
                matches_inserted += 1
            except Exception as e:
                # Log error but continue with other matches
                logger.warning(f"Skipping match {match_id}: {e}")
                matches_skipped += 1
        
        self.db.commit()
        logger.info(f"Synced {matches_inserted} matches, skipped {matches_skipped} invalid matches")
    
    def sync_standings(self, competition_id: str = "CL"):
        """Sync standings from API to database.
        
        Args:
            competition_id: Competition ID
        """
        standings_data = self.api_client.get_competition_standings(competition_id)
        
        if "standings" not in standings_data:
            return
        
        now = datetime.utcnow().isoformat()
        standings_inserted = 0
        standings_skipped = 0
        
        for group in standings_data["standings"]:
            if "table" not in group:
                continue
            
            for entry in group["table"]:
                team = entry.get("team", {})
                team_id = team.get("id")
                
                if not team_id:
                    standings_skipped += 1
                    continue
                
                try:
                    self.db.execute("""
                        INSERT INTO standings (
                            team_id, position, played, won, drawn, lost,
                            goals_for, goals_against, goal_difference, points, last_updated
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (team_id) DO UPDATE SET
                            position = excluded.position,
                            played = excluded.played,
                            won = excluded.won,
                            drawn = excluded.drawn,
                            lost = excluded.lost,
                            goals_for = excluded.goals_for,
                            goals_against = excluded.goals_against,
                            goal_difference = excluded.goal_difference,
                            points = excluded.points,
                            last_updated = excluded.last_updated
                    """, (
                        team_id,
                        entry.get("position"),
                        entry.get("playedGames", 0),
                        entry.get("won", 0),
                        entry.get("draw", 0),
                        entry.get("lost", 0),
                        entry.get("goalsFor", 0),
                        entry.get("goalsAgainst", 0),
                        entry.get("goalDifference", 0),
                        entry.get("points", 0),
                        now
                    ))
                    standings_inserted += 1
                except Exception as e:
                    logger.warning(f"Skipping standings for team {team_id}: {e}")
                    standings_skipped += 1
        
        self.db.commit()
        logger.info(f"Synced {standings_inserted} standings, skipped {standings_skipped} invalid entries")
    
    def sync_all(self, competition_id: str = "CL"):
        """Sync all data (teams, matches, standings).
        
        Args:
            competition_id: Competition ID
        """
        self.sync_teams(competition_id)
        self.sync_matches(competition_id)
        # Also try to fetch knockout stage matches if available
        self.sync_knockout_matches(competition_id)
        self.sync_standings(competition_id)
    
    def sync_knockout_matches(self, competition_id: str = "CL"):
        """Sync knockout stage matches from API.
        
        Tries to fetch matches for different knockout stages to get future draw information.
        
        Args:
            competition_id: Competition ID
        """
        # Try different stage filters to get knockout matches
        stages_to_try = [
            "KNOCKOUT_OUT",
            "KNOCKOUT_ROUND", 
            None  # Get all matches which may include future knockout matches
        ]
        
        for stage in stages_to_try:
            try:
                if stage:
                    matches_data = self.api_client.get_competition_matches_by_stage(competition_id, stage)
                else:
                    matches_data = self.api_client.get_competition_matches(competition_id)
                
                if "matches" not in matches_data:
                    continue
                
                matches_inserted = 0
                for match in matches_data["matches"]:
                    match_id = match.get("id")
                    home_team = match.get("homeTeam", {})
                    away_team = match.get("awayTeam", {})
                    
                    home_team_id = home_team.get("id")
                    away_team_id = away_team.get("id")
                    
                    if not match_id or not home_team_id or not away_team_id:
                        continue
                    
                    # Only process knockout stage matches
                    match_stage = match.get("stage")
                    if match_stage == "LEAGUE_STAGE":
                        continue
                    
                    try:
                        score = match.get("score", {})
                        full_time = score.get("fullTime", {})
                        
                        round_info = match.get("round")
                        if isinstance(round_info, dict):
                            round_name = round_info.get("name") or round_info.get("round")
                        else:
                            round_name = round_info
                        
                        group_info = match.get("group")
                        if isinstance(group_info, dict):
                            group_name = group_info.get("name") or group_info.get("group")
                        else:
                            group_name = group_info
                        
                        self.db.execute("""
                            INSERT INTO matches (
                                id, home_team_id, away_team_id, home_score, away_score,
                                matchday, date, status, stage, round, group_name
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (id) DO UPDATE SET
                                home_score = excluded.home_score,
                                away_score = excluded.away_score,
                                status = excluded.status,
                                stage = excluded.stage,
                                round = excluded.round,
                                group_name = excluded.group_name,
                                matchday = excluded.matchday,
                                date = excluded.date
                        """, (
                            match_id,
                            home_team_id,
                            away_team_id,
                            full_time.get("home"),
                            full_time.get("away"),
                            match.get("matchday"),
                            match.get("utcDate"),
                            match.get("status"),
                            match_stage,
                            round_name,
                            group_name
                        ))
                        matches_inserted += 1
                    except Exception as e:
                        logger.debug(f"Skipping knockout match {match_id}: {e}")
                        continue
                
                if matches_inserted > 0:
                    self.db.commit()
                    logger.info(f"Synced {matches_inserted} knockout matches from stage {stage or 'all'}")
                    break  # If we got matches, no need to try other stages
            except Exception as e:
                logger.debug(f"Could not fetch matches for stage {stage}: {e}")
                continue

