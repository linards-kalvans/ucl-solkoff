"""Fetcher for historical football data from openfootball/champions-league GitHub repository."""
import logging
import httpx
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class GitHubDataFetcher:
    """Fetches raw text files from openfootball/champions-league repository."""
    
    BASE_URL = "https://raw.githubusercontent.com/openfootball/champions-league/master"
    REPO_API_URL = "https://api.github.com/repos/openfootball/champions-league/contents"
    
    def __init__(self, cache_dir: Optional[str] = None):
        """Initialize GitHub data fetcher.
        
        Args:
            cache_dir: Directory to cache fetched files (optional)
        """
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.client = httpx.Client(timeout=30.0, follow_redirects=True)
        self.last_request_time = 0
        self.min_request_interval = 1.0  # 1 second between requests to respect rate limits
    
    def _rate_limit(self):
        """Enforce minimum time between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
    
    def _get_cache_path(self, season: str, competition: str) -> Optional[Path]:
        """Get cache file path for a season and competition.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            competition: Competition type (CL, EL, UCL)
            
        Returns:
            Path to cache file or None if caching disabled
        """
        if not self.cache_dir:
            return None
        
        # Map competition codes to directory names
        comp_map = {
            "CL": "champions-league",
            "EL": "europa-league",
            "UCL": "conference-league"
        }
        comp_dir = comp_map.get(competition, competition.lower())
        
        cache_file = self.cache_dir / comp_dir / f"{season}.txt"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        return cache_file
    
    def check_season_exists(self, season: str) -> bool:
        """Check if a season directory exists in the repository.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            True if season directory exists
        """
        self._rate_limit()
        
        try:
            # Check if season directory exists via GitHub API
            url = f"{self.REPO_API_URL}/{season}"
            response = self.client.get(url)
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                return False
            else:
                logger.debug(f"Unexpected status {response.status_code} when checking season {season}")
                return False
        except Exception as e:
            logger.debug(f"Error checking season {season}: {e}")
            return False
    
    def list_files_in_season(self, season: str) -> List[str]:
        """List available files in a season directory.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            List of file names in the season directory
        """
        self._rate_limit()
        
        try:
            url = f"{self.REPO_API_URL}/{season}"
            response = self.client.get(url)
            if response.status_code != 200:
                return []
            
            contents = response.json()
            files = []
            for item in contents:
                if item.get("type") == "file" and item.get("name", "").endswith(".txt"):
                    files.append(item.get("name"))
            
            return files
        except Exception as e:
            logger.debug(f"Error listing files for season {season}: {e}")
            return []
    
    def fetch_season_file(self, season: str, competition: str = "CL") -> Optional[str]:
        """Fetch a season file from GitHub.
        
        Args:
            season: Season identifier (e.g., "2023-24" or "2023-24")
            competition: Competition type (CL, EL, UCL)
            
        Returns:
            File content as string, or None if not found
        """
        # Check cache first
        cache_path = self._get_cache_path(season, competition)
        if cache_path and cache_path.exists():
            logger.debug(f"Loading {season} {competition} from cache")
            return cache_path.read_text(encoding='utf-8')
        
        # First, check if season directory exists
        if not self.check_season_exists(season):
            logger.debug(f"Season {season} does not exist in repository")
            return None
        
        # List available files in the season directory
        available_files = self.list_files_in_season(season)
        if not available_files:
            logger.debug(f"No .txt files found in season {season}")
            return None
        
        logger.debug(f"Available files in {season}: {available_files}")
        
        # Map competition codes to file names
        # Note: The repository uses short names like cl.txt, el.txt, conf.txt
        comp_map = {
            "CL": ["cl.txt", "champions-league.txt", "champions.txt"],
            "EL": ["el.txt", "europa-league.txt", "europa.txt"],
            "UCL": ["conf.txt", "conference-league.txt", "conference.txt", "ucl.txt", "ecl.txt"]
        }
        
        # Try competition-specific file names first
        file_names_to_try = comp_map.get(competition, [f"{competition.lower()}.txt"])
        
        # Also try generic patterns
        file_names_to_try.extend([
            "README.txt",
            "champions-league.txt",  # Fallback for CL
            "europa-league.txt",     # Fallback for EL
            "conference-league.txt"  # Fallback for UCL
        ])
        
        # Try files that actually exist in the directory
        for file_name in file_names_to_try:
            if file_name in available_files:
                url = f"{self.BASE_URL}/{season}/{file_name}"
                self._rate_limit()
                
                try:
                    response = self.client.get(url)
                    if response.status_code == 200:
                        content = response.text
                        
                        # Cache the content
                        if cache_path:
                            cache_path.write_text(content, encoding='utf-8')
                        
                        logger.info(f"Fetched {season} {competition} from GitHub ({file_name})")
                        return content
                except Exception as e:
                    logger.debug(f"Error fetching {url}: {e}")
                    continue
        
        # If no specific file found, try any .txt file in the directory
        # (some seasons might have a single file with all competitions)
        for file_name in available_files:
            if file_name.endswith('.txt') and file_name not in file_names_to_try:
                url = f"{self.BASE_URL}/{season}/{file_name}"
                self._rate_limit()
                
                try:
                    response = self.client.get(url)
                    if response.status_code == 200:
                        content = response.text
                        # Check if content contains our competition
                        content_lower = content.lower()
                        comp_keywords = {
                            "CL": ["champions league", "uefa champions"],
                            "EL": ["europa league", "uefa europa"],
                            "UCL": ["conference league", "uefa conference", "europa conference"]
                        }
                        keywords = comp_keywords.get(competition, [])
                        if any(keyword in content_lower for keyword in keywords):
                            # Cache the content
                            if cache_path:
                                cache_path.write_text(content, encoding='utf-8')
                            
                            logger.info(f"Fetched {season} {competition} from GitHub ({file_name})")
                            return content
                except Exception as e:
                    logger.debug(f"Error fetching {url}: {e}")
                    continue
        
        logger.warning(f"Could not find file for season {season}, competition {competition}")
        return None
    
    def list_available_seasons(self, competition: str = "CL") -> list[str]:
        """List available seasons in the repository.
        
        Args:
            competition: Competition type (CL, EL, UCL)
            
        Returns:
            List of season identifiers
        """
        self._rate_limit()
        
        try:
            response = self.client.get(self.REPO_API_URL)
            if response.status_code != 200:
                logger.warning(f"Could not list repository contents: {response.status_code}")
                return []
            
            contents = response.json()
            seasons = []
            
            for item in contents:
                if item.get("type") == "dir" and item.get("name", "").startswith("20"):
                    # Check if it's a season directory (format: YYYY-YY)
                    name = item.get("name", "")
                    if len(name) == 7 and name[4] == "-":
                        seasons.append(name)
            
            seasons.sort(reverse=True)  # Most recent first
            logger.info(f"Found {len(seasons)} seasons for {competition}")
            return seasons
            
        except Exception as e:
            logger.error(f"Error listing seasons: {e}")
            return []
    
    def fetch_all_competitions_for_season(self, season: str) -> Dict[str, Optional[str]]:
        """Fetch all competition files for a season.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            Dictionary mapping competition codes to file contents
        """
        results = {}
        competitions = ["CL", "EL", "UCL"]
        
        for comp in competitions:
            content = self.fetch_season_file(season, comp)
            if content:
                results[comp] = content
            else:
                results[comp] = None
        
        return results
    
    def close(self):
        """Close HTTP client."""
        if self.client:
            self.client.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

