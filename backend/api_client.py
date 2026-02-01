"""External API client for football-data.org."""
import os
import logging
import time
import httpx
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from backend.api_cache import APICache

load_dotenv()

logger = logging.getLogger(__name__)


class APIClient:
    """Client for fetching data from football-data.org API."""
    
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, 
                 cache: Optional[APICache] = None, use_cache: bool = True):
        """Initialize API client.
        
        Args:
            api_key: API key for authentication (defaults to EXTERNAL_API_KEY env var)
            base_url: Base URL for API (defaults to EXTERNAL_API_BASE_URL env var)
            cache: APICache instance for caching responses
            use_cache: Whether to use caching (default: True)
            
        Raises:
            ValueError: If API key is not provided
        """
        self.api_key = api_key or os.getenv("EXTERNAL_API_KEY", "")
        self.base_url = base_url or os.getenv("EXTERNAL_API_BASE_URL", "https://api.football-data.org/v4")
        self.cache = cache
        self.use_cache = use_cache and cache is not None
        
        if not self.api_key:
            raise ValueError(
                "API key is required. Please set EXTERNAL_API_KEY environment variable "
                "or provide it when initializing APIClient. "
                "Get a free API key from https://www.football-data.org/client/register"
            )
        
        self.headers = {
            "X-Auth-Token": self.api_key,
            "Content-Type": "application/json"
        }
        
        # Rate limiting: track last request time
        self.last_request_time = 0
        self.min_request_interval = float(os.getenv("API_MIN_REQUEST_INTERVAL", "0.1"))  # 100ms between requests
        
        logger.info(f"APIClient initialized with base URL: {self.base_url}, caching: {self.use_cache}")
    
    def _rate_limit(self):
        """Enforce minimum time between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    def _make_request(self, endpoint: str, use_cache: Optional[bool] = None, 
                     cache_ttl: Optional[int] = None) -> Dict[str, Any]:
        """Make HTTP request to API with caching and rate limiting.
        
        Args:
            endpoint: API endpoint path
            use_cache: Override instance cache setting
            cache_ttl: Custom cache TTL in seconds
            
        Returns:
            JSON response data
            
        Raises:
            httpx.HTTPStatusError: If request fails
            ValueError: If API key is invalid or missing
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        should_cache = use_cache if use_cache is not None else self.use_cache
        
        # Check cache first
        if should_cache and self.cache:
            cached_data = self.cache.get(endpoint)
            if cached_data:
                logger.debug(f"Cache hit for: {endpoint}")
                return cached_data
        
        # Rate limiting
        self._rate_limit()
        
        logger.debug(f"Making request to: {url}")
        
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=self.headers)
                    
                    # Handle 429 (Too Many Requests) with exponential backoff
                    if response.status_code == 429:
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (2 ** attempt)
                            logger.warning(
                                f"Rate limited (429) for {endpoint}. "
                                f"Retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(wait_time)
                            continue
                        else:
                            error_msg = (
                                f"429 Too Many Requests: Rate limit exceeded for {url}\n"
                                f"Please wait before making more requests. "
                                f"Consider increasing UPDATE_INTERVAL or using cached data."
                            )
                            logger.error(error_msg)
                            raise httpx.HTTPStatusError(
                                error_msg,
                                request=response.request,
                                response=response
                            )
                    
                    # Handle 403 specifically with helpful message
                    if response.status_code == 403:
                        error_msg = (
                            f"403 Forbidden: Access denied to {url}\n"
                            f"This usually means:\n"
                            f"  1. Your API key is invalid or expired\n"
                            f"  2. Your API key doesn't have access to this endpoint\n"
                            f"  3. You've exceeded your API rate limit\n\n"
                            f"Current API key: {'*' * (len(self.api_key) - 4) + self.api_key[-4:] if len(self.api_key) > 4 else '***'}\n"
                            f"Get a free API key from: https://www.football-data.org/client/register"
                        )
                        logger.error(error_msg)
                        raise httpx.HTTPStatusError(
                            error_msg,
                            request=response.request,
                            response=response
                        )
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    # Cache the response
                    if should_cache and self.cache:
                        self.cache.set(endpoint, data, cache_ttl)
                    
                    return data
                    
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    continue  # Will retry
                # Re-raise with more context
                logger.error(f"HTTP error {e.response.status_code} for {url}: {e}")
                raise
            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"Request error for {url}, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                    continue
                logger.error(f"Request error for {url}: {e}")
                raise
        
        # Should not reach here, but just in case
        raise Exception(f"Failed to make request to {url} after {max_retries} attempts")
    
    def get_competition_standings(self, competition_id: str = "CL") -> Dict[str, Any]:
        """Get standings for a competition.
        
        Args:
            competition_id: Competition ID (default: "CL" for Champions League)
            
        Returns:
            Standings data
        """
        return self._make_request(f"competitions/{competition_id}/standings")
    
    def get_competition_matches(self, competition_id: str = "CL") -> Dict[str, Any]:
        """Get matches for a competition.
        
        Args:
            competition_id: Competition ID (default: "CL" for Champions League)
            
        Returns:
            Matches data
        """
        return self._make_request(f"competitions/{competition_id}/matches")
    
    def get_team(self, team_id: int) -> Dict[str, Any]:
        """Get team information.
        
        Args:
            team_id: Team ID
            
        Returns:
            Team data
        """
        return self._make_request(f"teams/{team_id}")
    
    def get_competition_matches_by_stage(self, competition_id: str = "CL", stage: Optional[str] = None) -> Dict[str, Any]:
        """Get matches for a competition, optionally filtered by stage.
        
        Args:
            competition_id: Competition ID (default: "CL" for Champions League)
            stage: Optional stage filter (e.g., "KNOCKOUT_OUT", "LEAGUE_STAGE")
            
        Returns:
            Matches data
        """
        endpoint = f"competitions/{competition_id}/matches"
        if stage:
            endpoint += f"?stage={stage}"
        return self._make_request(endpoint)
    
    def get_competition_matches_by_round(self, competition_id: str = "CL", round_filter: Optional[str] = None) -> Dict[str, Any]:
        """Get matches for a competition, optionally filtered by round.
        
        Args:
            competition_id: Competition ID (default: "CL" for Champions League)
            round_filter: Optional round filter (e.g., "PLAY_OFF_ROUND", "ROUND_OF_16")
            
        Returns:
            Matches data
        """
        endpoint = f"competitions/{competition_id}/matches"
        if round_filter:
            endpoint += f"?round={round_filter}"
        return self._make_request(endpoint)

