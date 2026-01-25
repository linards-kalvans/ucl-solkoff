"""Background scheduler for periodic data updates."""
import os
import logging
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from backend.database import Database
from backend.api_client import APIClient
from backend.api_cache import APICache
from backend.data_service import DataService
from backend.solkoff_calculator import SolkoffCalculator

load_dotenv()

logger = logging.getLogger(__name__)


class DataScheduler:
    """Scheduler for periodic data updates."""
    
    def __init__(self, db: Database, competition_id: str = "CL"):
        """Initialize scheduler.
        
        Args:
            db: Database instance
            competition_id: Competition ID
            
        Raises:
            ValueError: If API key is not configured
        """
        self.db = db
        self.competition_id = competition_id
        
        # Initialize API cache
        cache_ttl = int(os.getenv("API_CACHE_TTL", "3600"))  # Default 1 hour
        self.api_cache = APICache(self.db, default_ttl_seconds=cache_ttl)
        
        try:
            self.api_client = APIClient(cache=self.api_cache, use_cache=True)
        except ValueError as e:
            logger.error(f"Failed to initialize API client: {e}")
            logger.error("Please set EXTERNAL_API_KEY in your .env file")
            raise
        
        self.data_service = DataService(self.db, self.api_client)
        self.calculator = SolkoffCalculator(self.db)
        self.scheduler = BackgroundScheduler()
    
    def update_data(self):
        """Update all data and recalculate Solkoff coefficients."""
        try:
            logger.info("Starting data update...")
            
            # Sync all data from API
            self.data_service.sync_all(self.competition_id)
            logger.info("Data synced successfully")
            
            # Recalculate Solkoff coefficients
            self.calculator.calculate_all()
            logger.info("Solkoff coefficients calculated successfully")
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error(
                    "403 Forbidden: API authentication failed. "
                    "Please check your EXTERNAL_API_KEY in .env file. "
                    "Get a free API key from https://www.football-data.org/client/register"
                )
            elif e.response.status_code == 429:
                logger.error(
                    "429 Too Many Requests: Rate limit exceeded. "
                    "The scheduler will retry on the next interval. "
                    "Consider increasing UPDATE_INTERVAL or API_CACHE_TTL in .env"
                )
            else:
                logger.error(f"HTTP error updating data: {e}", exc_info=True)
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
        except Exception as e:
            logger.error(f"Error updating data: {e}", exc_info=True)
    
    def start(self, interval_seconds: int = 3600):
        """Start the scheduler.
        
        Args:
            interval_seconds: Update interval in seconds (default: 3600 = 1 hour)
        """
        trigger = IntervalTrigger(seconds=interval_seconds)
        self.scheduler.add_job(
            self.update_data,
            trigger=trigger,
            id='update_data',
            name='Update Champions League data and Solkoff coefficients',
            replace_existing=True
        )
        self.scheduler.start()
        logger.info(f"Scheduler started with interval of {interval_seconds} seconds")
    
    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")
    
    def trigger_update(self):
        """Manually trigger an update."""
        self.update_data()

