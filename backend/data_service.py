"""Service for fetching and storing match and standings data."""
import logging
from datetime import datetime
from typing import Dict, Any, List
from backend.database import Database
from backend.api_client import APIClient
from backend.github_data_fetcher import GitHubDataFetcher
from backend.football_txt_parser import FootballTxtParser
from backend.team_matcher import TeamMatcher

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
        self.github_fetcher = GitHubDataFetcher(cache_dir="./data/github_cache")
        self.txt_parser = FootballTxtParser()
        self.team_matcher = TeamMatcher(db)
    
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
                        # Prefer tla (3-letter code), fallback to shortName, then None
                        code = team.get("tla") or team.get("shortName")
                        teams.add((
                            team.get("id"),
                            team.get("name"),
                            code,
                            team.get("crest")
                        ))
        
        # Also get teams from matches
        matches_data = self.api_client.get_competition_matches(competition_id)
        if "matches" in matches_data:
            for match in matches_data["matches"]:
                home_team = match.get("homeTeam", {})
                away_team = match.get("awayTeam", {})
                
                # Prefer tla, fallback to shortName
                home_code = home_team.get("tla") or home_team.get("shortName")
                away_code = away_team.get("tla") or away_team.get("shortName")
                
                teams.add((
                    home_team.get("id"),
                    home_team.get("name"),
                    home_code,
                    home_team.get("crest")
                ))
                teams.add((
                    away_team.get("id"),
                    away_team.get("name"),
                    away_code,
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
                        matchday, date, status, stage, round, group_name, competition_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        status = excluded.status,
                        stage = excluded.stage,
                        round = excluded.round,
                        group_name = excluded.group_name,
                        competition_id = excluded.competition_id
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
                    group_name,
                    competition_id
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
    
    def sync_historical_matches(self, years_back: int = 10, delay_between_requests: float = 3.0):
        """Sync historical matches from European competitions for the past N years.
        
        Fetches matches from:
        - Champions League (CL)
        - Europa League (EL)
        - Conference League (UCL)
        
        Args:
            years_back: Number of years to look back (default: 10)
            delay_between_requests: Delay in seconds between API requests (default: 3.0)
        """
        from datetime import datetime
        import time
        
        current_year = datetime.now().year
        current_season_start = current_year if datetime.now().month >= 8 else current_year - 1
        
        # European competition IDs
        # Note: Conference League code is "UCL" in football-data.org API (not "EC")
        competitions = {
            "CL": "Champions League",
            "EL": "Europa League",
            "UCL": "Conference League"
        }
        
        logger.info(f"Starting historical data sync for {years_back} years")
        
        # Count how many seasons we actually need to fetch
        seasons_to_fetch = []
        for comp_id, comp_name in competitions.items():
            for year_offset in range(years_back + 1):
                season_year = current_season_start - year_offset
                season_start = f"{season_year}-08-01"
                season_end = f"{season_year + 1}-07-31"
                
                existing_count = self.db.fetchone("""
                    SELECT COUNT(*) 
                    FROM matches 
                    WHERE competition_id = ?
                    AND date >= ?
                    AND date <= ?
                """, (comp_id, season_start, season_end))
                
                existing_matches = existing_count[0] if existing_count else 0
                
                # Consider a season complete if we have at least 50 matches (reasonable threshold)
                # This accounts for partial data or incomplete seasons
                if existing_matches < 50:
                    # Determine if this is current season (use API) or historical (use GitHub)
                    is_current_season = (season_year == current_season_start)
                    seasons_to_fetch.append((comp_id, comp_name, season_year, is_current_season))
        
        total_seasons = len(seasons_to_fetch)
        logger.info(f"Found {total_seasons} seasons to fetch")
        
        # Clone GitHub repository once for all historical seasons
        github_cloned = False
        try:
            # Check if we need GitHub data
            has_historical_seasons = any(not is_current for _, _, _, is_current in seasons_to_fetch)
            
            if has_historical_seasons:
                logger.info("Cloning GitHub repository for historical data...")
                self.github_fetcher.clone_repository()
                github_cloned = True
                logger.info("GitHub repository cloned successfully")
        except Exception as e:
            logger.error(f"Failed to clone GitHub repository: {e}")
            logger.warning("Will skip historical seasons that require GitHub data")
        
        try:
            for idx, (comp_id, comp_name, season_year, is_current) in enumerate(seasons_to_fetch):
                try:
                    logger.info(f"Fetching {comp_name} season {season_year}/{season_year+1} ({'current' if is_current else 'historical'})")
                    
                    if is_current:
                        # Use football-data.org API for current season
                        if idx > 0:
                            time.sleep(delay_between_requests)
                        self._sync_season_from_api(comp_id, comp_name, season_year)
                    else:
                        # Use GitHub for historical seasons
                        if not github_cloned:
                            logger.warning(f"Skipping {comp_name} season {season_year}/{season_year+1} - GitHub repo not available")
                            continue
                        self._sync_season_from_github(comp_id, comp_name, season_year)
                        
                except Exception as e:
                    error_msg = str(e)
                    # Check if it's a rate limit error
                    if "429" in error_msg or "rate limit" in error_msg.lower() or "Too Many Requests" in error_msg:
                        logger.warning(f"Rate limited for {comp_name} season {season_year}/{season_year+1}. Waiting 10 seconds before continuing...")
                        time.sleep(10)
                        continue
                    else:
                        logger.warning(f"Error syncing {comp_name} season {season_year}/{season_year+1}: {e}")
                        time.sleep(delay_between_requests)
                        continue
        finally:
            # Always cleanup GitHub repository
            if github_cloned:
                logger.info("Cleaning up cloned GitHub repository...")
                self.github_fetcher.cleanup()
        
        logger.info("Historical data sync completed")
    
    def _sync_season_from_api(self, comp_id: str, comp_name: str, season_year: int):
        """Sync a season from football-data.org API.
        
        Args:
            comp_id: Competition ID
            comp_name: Competition name
            season_year: Season start year
        """
        matches_data = self.api_client.get_competition_matches_by_season(comp_id, season_year)
        
        if "matches" not in matches_data:
            logger.debug(f"No matches found for {comp_name} season {season_year}/{season_year+1}")
            return
        
        matches_inserted = 0
        matches_skipped = 0
        
        for match in matches_data["matches"]:
            match_id = match.get("id")
            home_team = match.get("homeTeam", {})
            away_team = match.get("awayTeam", {})
            
            home_team_id = home_team.get("id")
            away_team_id = away_team.get("id")
            
            if not match_id or not home_team_id or not away_team_id:
                matches_skipped += 1
                continue
            
            # Store teams
            try:
                # Prefer tla (3-letter code), fallback to shortName
                home_code = home_team.get("tla") or home_team.get("shortName")
                away_code = away_team.get("tla") or away_team.get("shortName")
                
                self.db.execute("""
                    INSERT INTO teams (id, name, code, crest)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        code = excluded.code,
                        crest = excluded.crest
                """, (
                    home_team_id,
                    home_team.get("name"),
                    home_code,
                    home_team.get("crest")
                ))
                self.db.execute("""
                    INSERT INTO teams (id, name, code, crest)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        code = excluded.code,
                        crest = excluded.crest
                """, (
                    away_team_id,
                    away_team.get("name"),
                    away_code,
                    away_team.get("crest")
                ))
            except Exception as e:
                logger.debug(f"Error storing teams for match {match_id}: {e}")
            
            # Extract match details
            full_time = match.get("score", {}).get("fullTime", {})
            stage = match.get("stage")
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
            
            try:
                self.db.execute("""
                    INSERT INTO matches (
                        id, home_team_id, away_team_id, home_score, away_score,
                        matchday, date, status, stage, round, group_name, competition_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        status = excluded.status,
                        stage = excluded.stage,
                        round = excluded.round,
                        group_name = excluded.group_name,
                        competition_id = excluded.competition_id
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
                    group_name,
                    comp_id
                ))
                matches_inserted += 1
            except Exception as e:
                logger.debug(f"Skipping match {match_id}: {e}")
                matches_skipped += 1
        
        self.db.commit()
        logger.info(f"Synced {matches_inserted} matches for {comp_name} season {season_year}/{season_year+1} from API (skipped {matches_skipped})")
    
    def _sync_season_from_github(self, comp_id: str, comp_name: str, season_year: int):
        """Sync a season from GitHub openfootball repository.
        
        Args:
            comp_id: Competition ID
            comp_name: Competition name
            season_year: Season start year
        """
        # Convert season year to format used by GitHub (e.g., 2023 -> "2023-24")
        season_str = f"{season_year}-{str(season_year + 1)[-2:]}"
        
        # Fetch file from GitHub
        content = self.github_fetcher.fetch_season_file(season_str, comp_id)
        if not content:
            logger.warning(f"Could not fetch {comp_name} season {season_str} from GitHub")
            return
        
        # Parse the content
        parsed_data = self.txt_parser.parse(content, competition=comp_id, season=season_str)
        
        if not parsed_data or not parsed_data.get("matches"):
            logger.warning(f"No matches parsed from {comp_name} season {season_str}")
            return
        
        matches_inserted = 0
        matches_skipped = 0
        teams_inserted = 0
        
        # Store teams first
        for team_data in parsed_data.get("teams", []):
            team_name = team_data.get("name")
            if not team_name:
                continue
            
            try:
                team_id = self.team_matcher.find_or_create_team_id(team_name, comp_id)
                if team_id:
                    teams_inserted += 1
            except Exception as e:
                logger.debug(f"Error storing team {team_name}: {e}")
        
        # Store matches
        for match_data in parsed_data.get("matches", []):
            try:
                home_team_name = match_data.get("home_team")
                away_team_name = match_data.get("away_team")
                
                if not home_team_name or not away_team_name:
                    matches_skipped += 1
                    continue
                
                # Get or create team IDs
                home_team_id = self.team_matcher.find_or_create_team_id(home_team_name, comp_id)
                away_team_id = self.team_matcher.find_or_create_team_id(away_team_name, comp_id)
                
                if not home_team_id or not away_team_id:
                    matches_skipped += 1
                    continue
                
                # Generate synthetic match ID (negative to avoid conflicts with API IDs)
                # Use hash of teams + date for consistency
                match_key = f"{home_team_id}_{away_team_id}_{match_data.get('date', '')}"
                match_id = abs(hash(match_key)) % (10 ** 9)  # 9-digit ID
                match_id = -match_id  # Make negative to avoid conflicts
                
                # Parse date
                match_date = match_data.get("date")
                if not match_date:
                    # Try to infer approximate date from season and round
                    # Use a default date based on season and round
                    # This is a fallback - ideally all matches should have dates
                    if match_data.get("round"):
                        round_name = match_data.get("round", "").lower()
                        # Approximate dates for knockout rounds
                        if "round of 16" in round_name or "last 16" in round_name:
                            match_date = f"{season_year + 1}-02-15"  # Mid-February
                        elif "quarter" in round_name:
                            match_date = f"{season_year + 1}-04-01"  # Early April
                        elif "semi" in round_name:
                            match_date = f"{season_year + 1}-04-25"  # Late April
                        elif "final" in round_name:
                            match_date = f"{season_year + 1}-05-28"  # Late May
                    else:
                        # Group stage - use mid-season date
                        match_date = f"{season_year}-10-15"
                    
                    if not match_date:
                        matches_skipped += 1
                        continue
                
                # Determine matchday from stage/round
                matchday = None
                if match_data.get("stage") == "GROUP_STAGE":
                    matchday = 1  # Default for group stage
                elif match_data.get("round"):
                    # Assign matchday based on round
                    round_name = match_data.get("round", "").lower()
                    if "round of 16" in round_name or "last 16" in round_name:
                        matchday = 7
                    elif "quarter" in round_name:
                        matchday = 8
                    elif "semi" in round_name:
                        matchday = 9
                    elif "final" in round_name:
                        matchday = 10
                
                self.db.execute("""
                    INSERT INTO matches (
                        id, home_team_id, away_team_id, home_score, away_score,
                        matchday, date, status, stage, round, group_name, competition_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        status = excluded.status,
                        stage = excluded.stage,
                        round = excluded.round,
                        group_name = excluded.group_name,
                        competition_id = excluded.competition_id
                """, (
                    match_id,
                    home_team_id,
                    away_team_id,
                    match_data.get("home_score"),
                    match_data.get("away_score"),
                    matchday,
                    match_date,
                    match_data.get("status", "FINISHED"),
                    match_data.get("stage"),
                    match_data.get("round"),
                    match_data.get("group_name"),
                    comp_id
                ))
                matches_inserted += 1
                
            except Exception as e:
                logger.debug(f"Error storing match: {e}")
                matches_skipped += 1
        
        self.db.commit()
        logger.info(f"Synced {matches_inserted} matches and {teams_inserted} teams for {comp_name} season {season_str} from GitHub (skipped {matches_skipped})")
    
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

