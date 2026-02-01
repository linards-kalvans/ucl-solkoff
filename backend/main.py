"""FastAPI application main entry point."""
import os
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any
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
            return FileResponse(index_path)
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
            return FileResponse(file_path)
        
        # For SPA routing, serve index.html for non-API routes
        if not filename.startswith("api/"):
            index_path = os.path.join(frontend_path, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
        
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


@app.get("/api/standings")
async def get_standings() -> List[Dict[str, Any]]:
    """Get standings with Solkoff coefficients.
    
    Returns:
        List of teams with standings and Solkoff coefficients
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
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
        scheduler.trigger_update()
        return {"message": "Data refresh triggered successfully"}
    except Exception as e:
        logger.error(f"Error refreshing data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error refreshing data: {str(e)}")


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
async def get_solkoff_details(team_id: int) -> Dict[str, Any]:
    """Get detailed Solkoff coefficient calculation for a team.
    
    Args:
        team_id: Team ID
        
    Returns:
        Detailed breakdown of Solkoff coefficient calculation
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
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
            ORDER BY m.date DESC
        """, (team_id,))
        
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
            ORDER BY m.date DESC
        """, (team_id,))
        
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
async def get_playoff_pairs() -> List[Dict[str, Any]]:
    """Get current play-off pairs from the competition (all stages).
    
    Returns:
        List of play-off pairs
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(db, api_client)
        
        pairs = analyzer.get_playoff_pairs()
        logger.info(f"Found {len(pairs)} play-off pairs")
        return pairs
    except Exception as e:
        logger.error(f"Error fetching play-off pairs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/tournament/current-stage")
async def get_current_stage() -> Dict[str, Any]:
    """Get the current tournament stage.
    
    Returns:
        Dictionary with 'stage' field indicating current stage:
        'LEAGUE', 'KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL'
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(db, api_client)
        
        current_stage = analyzer.get_current_stage()
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
async def get_knockout_pairs_by_stage(stage: str) -> List[Dict[str, Any]]:
    """Get play-off pairs for a specific stage.
    
    Args:
        stage: Stage identifier ('KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL')
    
    Returns:
        List of play-off pairs for the specified stage
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    valid_stages = ['KNOCKOUT_PLAYOFF', 'ROUND_OF_16', 'QUARTER_FINAL', 'SEMI_FINAL', 'FINAL']
    if stage.upper() not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(valid_stages)}")
    
    try:
        from backend.api_client import APIClient
        from backend.uefa_draws_scraper import UEFADrawsScraper
        
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(db, api_client)
        
        # First try to get pairs from database (actual matches)
        pairs = analyzer.get_pairs_by_stage(stage.upper())
        
        # If no pairs found, try to get draw information from UEFA
        if len(pairs) == 0:
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
async def get_playoff_analysis(team1_id: int, team2_id: int) -> Dict[str, Any]:
    """Get play-off pair analysis with league table based on common opponents.
    
    Args:
        team1_id: First team ID
        team2_id: Second team ID
        
    Returns:
        Analysis with league table
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        from backend.api_client import APIClient
        api_client = APIClient()
        analyzer = PlayoffAnalyzer(db, api_client)
        
        analysis = analyzer.analyze_pair(team1_id, team2_id)
        return analysis
    except Exception as e:
        logger.error(f"Error analyzing play-off pair: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

