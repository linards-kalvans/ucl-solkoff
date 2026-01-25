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
                COALESCE(sc.solkoff_value, 0) as solkoff_coefficient,
                s.points * COALESCE(sc.solkoff_value, 0) as strength_score
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
                "solkoffCoefficient": row[13],
                "strengthScore": row[14] or 0
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
        
        solkoff_value = solkoff_result[0] if solkoff_result and solkoff_result[0] is not None else 0
        
        # Get opponent details with their points and match outcomes
        # First get all opponents from home matches with scores
        home_opponents = db.fetchall("""
            SELECT 
                m.away_team_id as opponent_team_id,
                t2.name as opponent_team_name,
                t2.crest as opponent_team_crest,
                COALESCE(s2.points, 0) as opponent_points,
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
            home_score = row[4]
            away_score = row[5]
            match_date = row[6]
            
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
                    'points': row[3],
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
            home_score = row[4]
            away_score = row[5]
            match_date = row[6]
            
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
                    'points': row[3],
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
        opponents_data.sort(key=lambda x: (-x['points'], x['teamName']))
        
        # Format opponents data
        opponents = opponents_data
        total_matches = sum(opp['matchesPlayed'] for opp in opponents)
        
        # Calculate total opponent points (should match solkoff_value)
        total_opponent_points = sum(opp["points"] for opp in opponents)
        
        return {
            "teamId": team_info[0],
            "teamName": team_info[1],
            "teamCode": team_info[2],
            "teamCrest": team_info[3],
            "solkoffCoefficient": solkoff_value,
            "opponents": opponents,
            "totalOpponentPoints": total_opponent_points,
            "matchesCount": total_matches,
            "opponentsCount": len(opponents)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Solkoff details for team {team_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

