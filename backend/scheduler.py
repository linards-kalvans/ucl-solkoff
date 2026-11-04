"""Background scheduler for periodic data updates."""
import os
import logging
from typing import Optional
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from backend.database import Database
from backend.api_client import APIClient
from backend.api_cache import APICache
from backend.data_service import DataService
from backend.solkoff_calculator import SolkoffCalculator
from backend.elo_calculator import EloCalculator
from backend.competitions import parse_competition_list

load_dotenv()

logger = logging.getLogger(__name__)


class DataScheduler:
    """Scheduler for periodic data updates."""
    
    def __init__(self, db: Database, competition_id: str = "CL",
                 competitions: Optional[list] = None):
        """Initialize scheduler.

        Args:
            db: Database instance
            competition_id: Primary competition ID (backwards-compatible single
                competition; also used as the default for the refresh loop)
            competitions: Optional explicit list of competitions to refresh. When
                omitted, only ``competition_id`` is refreshed (the application
                startup supplies all supported competitions explicitly).

        Raises:
            ValueError: If API key is not configured
        """
        self.db = db
        self.competition_id = competition_id
        if competitions is None:
            self.competitions = parse_competition_list(
                os.getenv("COMPETITIONS"), fallback=[competition_id]
            )
        else:
            self.competitions = list(competitions)

        # Initialize API cache
        cache_ttl = int(os.getenv("API_CACHE_TTL", "3600"))  # Default 1 hour
        self.api_cache = APICache(self.db, default_ttl_seconds=cache_ttl)
        
        # A missing football-data credential disables only CL network refresh,
        # never the ESPN-backed EL/UCL path or the whole scheduler.
        try:
            self.api_client = APIClient(cache=self.api_cache, use_cache=True)
        except ValueError as e:
            self.api_client = None
            logger.error(f"Failed to initialize football-data client: {e}")
            logger.warning(
                "EXTERNAL_API_KEY is not configured; CL refresh is disabled. "
                "EL/UCL ESPN refresh continues."
            )

        self.data_service = DataService(self.db, self.api_client)
        # Backwards-compatible primary calculator (single-competition callers and
        # tests replace this attribute directly).
        self.calculator = SolkoffCalculator(self.db, competition_id=self.competition_id)
        # Per-competition calculators only needed when refreshing more than one.
        self.calculators = {}
        if len(self.competitions) > 1:
            for code in self.competitions:
                if code == self.competition_id:
                    self.calculators[code] = self.calculator
                else:
                    self.calculators[code] = SolkoffCalculator(self.db, competition_id=code)
        self.elo_calculator = EloCalculator(self.db)
        self.scheduler = BackgroundScheduler()
    
    def update_data(self):
        """Update all data and recalculate Solkoff coefficients.

        Each competition is refreshed independently: one provider failure is
        logged and does not stop the others, and a failed refresh never erases
        already-stored rows. Elo is replayed once after the loop (shared across
        European competitions by design).
        """
        for code in self.competitions:
            try:
                logger.info(f"Starting data update for {code}...")
                self.data_service.sync_all(code)
                logger.info(f"{code} data synced successfully")

                calculator = self.calculators.get(code, self.calculator)
                calculator.calculate_all()
                logger.info(f"{code} Solkoff coefficients calculated successfully")
            except httpx.HTTPStatusError as e:
                self._log_http_error(code, e)
            except ValueError as e:
                logger.error(f"Configuration error updating {code}: {e}")
            except Exception as e:
                logger.error(f"Error updating {code}: {e}", exc_info=True)

        try:
            self.elo_calculator.calculate_all()
            logger.info("Elo ratings calculated successfully")
        except Exception as e:
            logger.error(f"Error calculating Elo ratings: {e}", exc_info=True)

    @staticmethod
    def _log_http_error(code: str, e: httpx.HTTPStatusError):
        """Log a provider HTTP failure with actionable guidance."""
        if e.response.status_code == 403:
            logger.error(
                f"403 Forbidden for {code}: API authentication failed. "
                "Please check your EXTERNAL_API_KEY in .env file. "
                "Get a free API key from https://www.football-data.org/client/register"
            )
        elif e.response.status_code == 429:
            logger.error(
                f"429 Too Many Requests for {code}: Rate limit exceeded. "
                "The scheduler will retry on the next interval. "
                "Consider increasing UPDATE_INTERVAL or API_CACHE_TTL in .env"
            )
        else:
            logger.error(f"HTTP error updating {code}: {e}", exc_info=True)
    
    def start(self, interval_seconds: int = 86400):
        """Start the scheduler.
        
        Args:
            interval_seconds: Update interval in seconds (default: 86400 = 1 day)
        """
        trigger = IntervalTrigger(seconds=interval_seconds)
        self.scheduler.add_job(
            self.update_data,
            trigger=trigger,
            id='update_data',
            name='Update European competition data and Solkoff coefficients',
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

