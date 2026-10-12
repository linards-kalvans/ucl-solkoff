"""FastAPI application main entry point."""
import os
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from backend.database import Database
from backend.scheduler import DataScheduler
from backend.api_cache import APICache
from backend.playoff_analyzer import PlayoffAnalyzer
from backend.solkoff_calculator import get_season_bounds
from backend.season_service import (
    build_breakdown,
    build_standings,
    current_season_year,
    discover_seasons,
    is_supported_year,
)

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global database and scheduler instances
db: Database = None
scheduler: DataScheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    global db, scheduler
    
    # Startup
    # Use Railway's persistent volume or local path
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "./data")
    db_path = os.getenv("DB_PATH", os.path.join(data_dir, "ucl.db"))
    # Ensure data directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    competition_id = os.getenv("COMPETITION_ID", "CL")
    
    db = Database(db_path=db_path)
    
    # Cleanup expired cache entries on startup
    api_cache = APICache(db)
    api_cache.cleanup_expired()
    
    try:
        scheduler = DataScheduler(db, competition_id)
        
        # Start scheduler
        update_interval = int(os.getenv("UPDATE_INTERVAL", "3600"))
        scheduler.start(interval_seconds=update_interval)
        
        # Trigger initial update
        try:
            scheduler.trigger_update()
        except Exception as e:
            logger.warning(f"Initial data update failed: {e}")
            logger.warning("The application will continue, but data may not be available until API key is configured.")

        # Sync historical data in the background on first boot (when DB is empty).
        # Historical seasons don't change, so this only needs to run once per fresh volume.
        def _maybe_sync_historical():
            try:
                row = db.fetchone(
                    "SELECT COUNT(*) FROM matches WHERE competition_id IN ('CL', 'UCL', 'EL') AND date < '2024-01-01'"
                )
                historical_count = row[0] if row else 0
                if historical_count < 100:
                    years_back = int(os.getenv("HISTORICAL_YEARS", "10"))
                    logger.info(f"Historical matches missing ({historical_count} found) — starting background sync for {years_back} years")
                    scheduler.data_service.sync_historical_matches(years_back=years_back)
                    scheduler.elo_calculator.calculate_all()
                    logger.info("Background historical sync complete — Elo ratings updated")
                else:
                    logger.info(f"Historical data already present ({historical_count} matches), skipping sync")
            except Exception as e:
                logger.error(f"Background historical sync failed: {e}", exc_info=True)

        import threading
        threading.Thread(target=_maybe_sync_historical, daemon=True).start()
    except ValueError as e:
        logger.error(f"Failed to initialize scheduler: {e}")
        logger.error("Please set EXTERNAL_API_KEY in your .env file to enable data updates.")
        logger.error("The application will start, but data updates will be disabled.")
        scheduler = None
    
    yield
    
    # Shutdown
    if scheduler:
        scheduler.stop()
    if db:
        db.close()


# Initialize FastAPI app
app = FastAPI(title="UCL Solkoff API", version="1.0.0", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (frontend)
frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")
    
    @app.get("/")
    async def root():
        """Serve the frontend index page."""
        index_path = os.path.join(frontend_path, "index.html")
        if os.path.exists(index_path):
            response = FileResponse(index_path)
            # Add no-cache headers to force browser reload
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        return {"message": "UCL Solkoff API", "version": "1.0.0"}
    
    # Serve other frontend files (CSS, JS, etc.)
    @app.get("/{filename}")
    async def serve_frontend_file(filename: str):
        """Serve frontend static files."""
        # Don't interfere with API routes
        if filename.startswith("api"):
            raise HTTPException(status_code=404, detail="Not found")
        
        file_path = os.path.join(frontend_path, filename)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            response = FileResponse(file_path)
            # Add no-cache headers for CSS, JS, and HTML files to force browser reload
            if filename.endswith(('.css', '.js', '.html')):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
        
        # For SPA routing, serve index.html for non-API routes
        if not filename.startswith("api/"):
            index_path = os.path.join(frontend_path, "index.html")
            if os.path.exists(index_path):
                response = FileResponse(index_path)
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
                return response
        
        raise HTTPException(status_code=404, detail="Not found")
else:
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {"message": "UCL Solkoff API", "version": "1.0.0"}


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "database": db is not None}


def _competition_id() -> str:
    """Configured competition whose stored data is served."""
    return os.getenv("COMPETITION_ID", "CL")


def _resolve_season(season: Optional[int]) -> int:
    """Validate an optional season query parameter.

    ``None`` means the current season (preserving the no-year contract). A
    supplied year must be a supported new-format season; anything else raises
    a 400 rather than silently falling back to another season.
    """
    if season is None:
        return current_season_year()
    if not is_supported_year(season):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported season {season}. Supported seasons are 2024/25 "
                f"through the current season ({current_season_year()}/{str(current_season_year() + 1)[2:]})."
            ),
        )
    return season


@app.get("/api/seasons")
async def get_seasons() -> Dict[str, Any]:
    """List selectable new-format Champions League seasons (newest first)."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")

    try:
        competition_id = _competition_id()
        return {
            "currentSeason": current_season_year(),
            "seasons": discover_seasons(db, competition_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing seasons: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/standings")
async def get_standings(season: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get standings with Solkoff coefficients.

    Args:
        season: Optional season start year. Omit for the current season.

    Returns:
        List of teams with standings and Solkoff coefficients
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")

    requested = _resolve_season(season)

    # Archived seasons are rebuilt read-only from stored results; the live
    # standings table is never touched.
    if requested != current_season_year():
        try:
            return build_standings(db, requested, _competition_id())
        except Exception as e:
            logger.error(f"Error building historical standings: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    try:
        # Get standings with Solkoff coefficients
        # Solkoff is now average PPG of opponents, Strength is (points % * solkoff)
        results = db.fetchall("""
            SELECT 
                s.team_id,
                t.name as team_name,
                t.code as team_code,
                t.crest as team_crest,
                s.position,
                s.played,
                s.won,
                s.drawn,
                s.lost,
                s.goals_for,
                s.goals_against,
                s.goal_difference,
                s.points,
                CAST(COALESCE(sc.solkoff_value, 0) AS REAL) as solkoff_coefficient,
                CASE 
                    WHEN s.played > 0 THEN 
                        (s.points * 100.0 / (s.played * 3)) * CAST(COALESCE(sc.solkoff_value, 0) AS REAL) / 100.0
                    ELSE 0
                END as strength_score
            FROM standings s
            JOIN teams t ON s.team_id = t.id
            LEFT JOIN solkoff_coefficients sc ON s.team_id = sc.team_id
            ORDER BY s.points DESC, s.goal_difference DESC, s.goals_for DESC
        """)
        
        standings = []
        for row in results:
            standings.append({
                "teamId": row[0],
                "teamName": row[1],
                "teamCode": row[2],
                "teamCrest": row[3],
                "position": row[4],
                "played": row[5],
                "won": row[6],
                "drawn": row[7],
                "lost": row[8],
                "gf": row[9],
                "ga": row[10],
                "gd": row[11],
                "points": row[12],
                "solkoffCoefficient": float(row[13]) if row[13] is not None else 0.0,
                "strengthScore": float(row[14]) if row[14] is not None else 0.0
            })
        
        return standings
    
    except Exception as e:
        logger.error(f"Error fetching standings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/refresh")
async def refresh_data():
    """Manually trigger data refresh.
    
    Returns:
        Success message
    """
    if not scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    
    try:
        # Trigger regular update
        scheduler.trigger_update()
        return {"message": "Data refresh triggered successfully"}
    except Exception as e:
        logger.error(f"Error refreshing data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error refreshing data")


@app.post("/api/refresh-historical")
async def refresh_historical_data(years_back: int = 10, delay: float = 3.0):
    """Manually trigger historical data sync from European competitions.
    
    Args:
        years_back: Number of years to look back (default: 10)
        delay: Delay in seconds between API requests (default: 3.0, minimum: 1.0)
    
    Returns:
        Success message
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    # Enforce minimum delay to prevent rate limiting
    delay = max(1.0, delay)
    
    try:
        from backend.api_client import APIClient
        from backend.api_cache import APICache
        from backend.data_service import DataService
        
        # Initialize API client with cache
        cache_ttl = int(os.getenv("API_CACHE_TTL", "3600"))
        api_cache = APICache(db, default_ttl_seconds=cache_ttl)
        api_client = APIClient(cache=api_cache, use_cache=True)
        data_service = DataService(db, api_client)
        
        logger.info(f"Starting historical data sync for {years_back} years with {delay}s delay between requests")
        data_service.sync_historical_matches(years_back=years_back, delay_between_requests=delay)
        
        return {
            "message": f"Historical data sync completed for {years_back} years",
            "delay_used": delay
        }
    except Exception as e:
        logger.error(f"Error syncing historical data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error syncing historical data: {str(e)}")


@app.post("/api/cache/clear")
async def clear_cache():
    """Clear API response cache.
    
    Returns:
        Success message
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        cache = APICache(db)
        cache.clear()
        return {"message": "Cache cleared successfully"}
    except Exception as e:
        logger.error(f"Error clearing cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error clearing cache: {str(e)}")


@app.get("/api/teams/{team_id}/solkoff-details")
async def get_solkoff_details(team_id: int, season: Optional[int] = None) -> Dict[str, Any]:
    """Get detailed Solkoff coefficient calculation for a team.
    
    Args:
        team_id: Team ID
        season: Optional season start year. Omit for the current season.
        
    Returns:
        Detailed breakdown of Solkoff coefficient calculation
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")

    requested = _resolve_season(season)

    # Archived seasons reconstruct the breakdown from stored results only.
    if requested != current_season_year():
        try:
            details = build_breakdown(db, team_id, requested, _competition_id())
        except Exception as e:
            logger.error(f"Error building historical Solkoff details: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")
        if details is None:
            raise HTTPException(status_code=404, detail="Team not found")
        return details

    try:
        # Get team information
        team_info = db.fetchone("""
            SELECT id, name, code, crest
            FROM teams
            WHERE id = ?
        """, (team_id,))
        
        if not team_info:
            raise HTTPException(status_code=404, detail="Team not found")
        
        # Get Solkoff coefficient
        solkoff_result = db.fetchone("""
            SELECT solkoff_value
            FROM solkoff_coefficients
            WHERE team_id = ?
        """, (team_id,))
        
        solkoff_value = float(solkoff_result[0]) if solkoff_result and solkoff_result[0] is not None else 0.0
        
        # Current-season league-stage scope, matching the calculator exactly
        competition_id = os.getenv("COMPETITION_ID", "CL")
        season_start, season_end = get_season_bounds()
        
        # Get opponent details with their points per game and match outcomes
        # First get all opponents from home matches with scores
        home_opponents = db.fetchall("""
            SELECT 
                m.away_team_id as opponent_team_id,
                t2.name as opponent_team_name,
                t2.crest as opponent_team_crest,
                COALESCE(s2.points, 0) as opponent_points,
                COALESCE(s2.played, 0) as opponent_played,
                m.home_score,
                m.away_score,
                m.date
            FROM matches m
            JOIN teams t2 ON m.away_team_id = t2.id
            LEFT JOIN standings s2 ON m.away_team_id = s2.team_id
            WHERE m.home_team_id = ? AND m.status = 'FINISHED'
              AND m.competition_id = ? AND m.stage = 'LEAGUE_STAGE'
              AND m."date" >= ? AND m."date" < ?
            ORDER BY m.date DESC
        """, (team_id, competition_id, season_start, season_end))
        
        # Get all opponents from away matches with scores
        away_opponents = db.fetchall("""
            SELECT 
                m.home_team_id as opponent_team_id,
                t2.name as opponent_team_name,
                t2.crest as opponent_team_crest,
                COALESCE(s2.points, 0) as opponent_points,
                COALESCE(s2.played, 0) as opponent_played,
                m.home_score,
                m.away_score,
                m.date
            FROM matches m
            JOIN teams t2 ON m.home_team_id = t2.id
            LEFT JOIN standings s2 ON m.home_team_id = s2.team_id
            WHERE m.away_team_id = ? AND m.status = 'FINISHED'
              AND m.competition_id = ? AND m.stage = 'LEAGUE_STAGE'
              AND m."date" >= ? AND m."date" < ?
            ORDER BY m.date DESC
        """, (team_id, competition_id, season_start, season_end))
        
        # Combine and aggregate opponents with match details
        opponents_dict = {}
        
        # Process home matches
        for row in home_opponents:
            opp_id = row[0]
            opp_points = row[3]
            opp_played = row[4]
            home_score = row[5]
            away_score = row[6]
            match_date = row[7]
            
            # Home match: team is home
            team_score = home_score
            opp_score = away_score
            
            # Determine outcome
            if team_score is None or opp_score is None:
                outcome = 'unknown'
            elif team_score > opp_score:
                outcome = 'win'
            elif team_score < opp_score:
                outcome = 'loss'
            else:
                outcome = 'draw'
            
            if opp_id not in opponents_dict:
                opponents_dict[opp_id] = {
                    'teamId': row[0],
                    'teamName': row[1],
                    'teamCrest': row[2],
                    'points': opp_points,
                    'played': opp_played,
                    'pointsPerGame': (opp_points / opp_played) if opp_played > 0 else 0.0,
                    'matchesPlayed': 0,
                    'matches': []
                }
            
            opponents_dict[opp_id]['matchesPlayed'] += 1
            opponents_dict[opp_id]['matches'].append({
                'homeScore': home_score,
                'awayScore': away_score,
                'teamScore': team_score,
                'opponentScore': opp_score,
                'outcome': outcome,
                'date': match_date,
                'isHome': True
            })
        
        # Process away matches
        for row in away_opponents:
            opp_id = row[0]
            opp_points = row[3]
            opp_played = row[4]
            home_score = row[5]
            away_score = row[6]
            match_date = row[7]
            
            # Away match: team is away
            team_score = away_score
            opp_score = home_score
            
            # Determine outcome
            if team_score is None or opp_score is None:
                outcome = 'unknown'
            elif team_score > opp_score:
                outcome = 'win'
            elif team_score < opp_score:
                outcome = 'loss'
            else:
                outcome = 'draw'
            
            if opp_id not in opponents_dict:
                opponents_dict[opp_id] = {
                    'teamId': row[0],
                    'teamName': row[1],
                    'teamCrest': row[2],
                    'points': opp_points,
                    'played': opp_played,
                    'pointsPerGame': (opp_points / opp_played) if opp_played > 0 else 0.0,
                    'matchesPlayed': 0,
                    'matches': []
                }
            
            opponents_dict[opp_id]['matchesPlayed'] += 1
            opponents_dict[opp_id]['matches'].append({
                'homeScore': home_score,
                'awayScore': away_score,
                'teamScore': team_score,
                'opponentScore': opp_score,
                'outcome': outcome,
                'date': match_date,
                'isHome': False
            })
        
        opponents_data = list(opponents_dict.values())
        opponents_data.sort(key=lambda x: (-x.get('pointsPerGame', 0), x['teamName']))
        
        # Format opponents data
        opponents = opponents_data
        total_matches = sum(opp['matchesPlayed'] for opp in opponents)
        
        # Calculate average PPG of opponents (should match solkoff_value)
        opponent_ppg_list = [opp.get('pointsPerGame', 0) for opp in opponents if opp.get('played', 0) > 0]
        avg_opponent_ppg = sum(opponent_ppg_list) / len(opponent_ppg_list) if opponent_ppg_list else 0.0
        
        return {
            "teamId": team_info[0],
            "teamName": team_info[1],
            "teamCode": team_info[2],
            "teamCrest": team_info[3],
            "solkoffCoefficient": solkoff_value,
            "opponents": opponents,
            "averageOpponentPPG": round(avg_opponent_ppg, 3),
            "matchesCount": total_matches,
            "opponentsCount": len(opponents)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Solkoff details for team {team_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/playoff-pairs")
async def get_playoff_pairs(season: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get current play-off pairs from the competition (all stages).
    
    Returns:
        List of play-off pairs
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    requested = _resolve_season(season)
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(
            db, api_client, competition_id=_competition_id(),
            season_start_year=requested,
        )
        
        pairs = analyzer.get_playoff_pairs(competition_id=_competition_id())
        logger.info(f"Found {len(pairs)} play-off pairs")
        return pairs
    except Exception as e:
        logger.error(f"Error fetching play-off pairs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/tournament/current-stage")
async def get_current_stage(season: Optional[int] = None) -> Dict[str, Any]:
    """Get the current tournament stage.
    
    Returns:
        Dictionary with 'stage' field indicating current stage:
        'LEAGUE', 'KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL'
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    requested = _resolve_season(season)
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(
            db, api_client, competition_id=_competition_id(),
            season_start_year=requested,
        )
        
        current_stage = analyzer.get_current_stage(_competition_id())
        logger.info(f"Current tournament stage: {current_stage}")
        return {"stage": current_stage}
    except Exception as e:
        logger.error(f"Error determining current stage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/debug/knockout-matches")
async def debug_knockout_matches() -> Dict[str, Any]:
    """Debug endpoint to see what knockout matches are in the database."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Get all non-league matches
        matches = db.fetchall("""
            SELECT 
                m.id,
                m.stage,
                m.round,
                m.matchday,
                m.date,
                m.status,
                m.group_name,
                t1.name as home_team,
                t2.name as away_team
            FROM matches m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE (m.stage IS NOT NULL AND m.stage != 'LEAGUE_STAGE') 
               OR (m.stage IS NULL AND m.matchday >= 7 AND (m.group_name IS NULL OR m.group_name = ''))
            ORDER BY m.matchday, m.date
            LIMIT 50
        """)
        
        result = {
            "total_matches": len(matches),
            "matches": [
                {
                    "id": m[0],
                    "stage": m[1],
                    "round": m[2],
                    "matchday": m[3],
                    "date": m[4],
                    "status": m[5],
                    "group_name": m[6],
                    "home_team": m[7],
                    "away_team": m[8]
                }
                for m in matches
            ]
        }
        return result
    except Exception as e:
        logger.error(f"Error in debug endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/knockout-pairs/{stage}")
async def get_knockout_pairs_by_stage(stage: str, season: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get play-off pairs for a specific stage.
    
    Args:
        stage: Stage identifier ('KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL')
        season: Optional season start year. Omit for the current season.
    
    Returns:
        List of play-off pairs for the specified stage
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    valid_stages = ['KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL']
    if stage.upper() not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(valid_stages)}")

    requested = _resolve_season(season)
    
    try:
        from backend.api_client import APIClient
        from backend.uefa_draws_scraper import UEFADrawsScraper
        
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(
            db, api_client, competition_id=_competition_id(),
            season_start_year=requested,
        )
        
        # First try to get pairs from database (actual matches)
        pairs = analyzer.get_pairs_by_stage(stage.upper(), _competition_id())
        
        # Current draws only exist for the current season; never fetch them for
        # an archived season.
        if len(pairs) == 0 and requested == current_season_year():
            try:
                scraper = UEFADrawsScraper()
                draws = scraper.get_knockout_draws()
                
                # Check if we have draw information for this stage
                stage_draw = next((d for d in draws if d["stage"] == stage.upper()), None)
                if stage_draw:
                    logger.info(f"Found draw information for {stage}: {stage_draw.get('drawDateDisplay', 'N/A')}")
                    # Log draw date - pairs will be available after the draw
            except Exception as scrape_error:
                logger.debug(f"Could not fetch UEFA draw info: {scrape_error}")
        
        logger.info(f"Found {len(pairs)} pairs for stage {stage}")
        return pairs
    except Exception as e:
        logger.error(f"Error fetching pairs for stage {stage}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/playoff-pairs/{team1_id}/{team2_id}/analysis")
async def get_playoff_analysis(team1_id: int, team2_id: int, season: Optional[int] = None) -> Dict[str, Any]:
    """Get play-off pair analysis with league table based on common opponents.
    
    Args:
        team1_id: First team ID
        team2_id: Second team ID
        season: Optional season start year. Omit for the current season.
        
    Returns:
        Analysis with league table
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    requested = _resolve_season(season)
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(
            db, api_client, competition_id=_competition_id(),
            season_start_year=requested,
        )
        
        analysis = analyzer.analyze_pair(team1_id, team2_id)
        return analysis
    except Exception as e:
        logger.error(f"Error analyzing play-off pair: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

