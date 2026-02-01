"""Fetcher for historical football data from openfootball/champions-league GitHub repository."""
import logging
import tempfile
import shutil
from typing import Optional, Dict, Any, List
from pathlib import Path
from git import Repo

logger = logging.getLogger(__name__)


class GitHubDataFetcher:
    """Clones and extracts data from openfootball/champions-league repository."""
    
    REPO_URL = "https://github.com/openfootball/champions-league.git"
    
    def __init__(self, cache_dir: Optional[str] = None):
        """Initialize GitHub data fetcher.
        
        Args:
            cache_dir: Directory to cache fetched files (optional, not used with clone approach)
        """
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cloned_repo_path: Optional[Path] = None
        self._temp_dir: Optional[Path] = None
    
    def clone_repository(self) -> Path:
        """Clone the repository to a temporary location.
        
        Returns:
            Path to the cloned repository
            
        Raises:
            RuntimeError: If cloning fails
        """
        if self.cloned_repo_path and self.cloned_repo_path.exists():
            logger.debug("Repository already cloned, reusing existing clone")
            return self.cloned_repo_path
        
        try:
            # Create temporary directory
            self._temp_dir = Path(tempfile.mkdtemp(prefix="ucl-solkoff-github-"))
            self.cloned_repo_path = self._temp_dir / "champions-league"
            
            logger.info(f"Cloning repository to {self.cloned_repo_path}")
            
            # Clone repository using GitPython (shallow clone for speed)
            Repo.clone_from(
                self.REPO_URL,
                str(self.cloned_repo_path),
                depth=1,  # Shallow clone for speed
                progress=None  # Disable progress output
            )
            
            logger.info("Repository cloned successfully")
            return self.cloned_repo_path
            
        except ImportError:
            raise RuntimeError("GitPython is not installed. Please install it with: pip install GitPython")
        except Exception as e:
            # Clean up on error
            self.cleanup()
            raise RuntimeError(f"Failed to clone repository: {e}")
    
    def cleanup(self):
        """Remove the temporary cloned repository."""
        if self._temp_dir and self._temp_dir.exists():
            try:
                logger.debug(f"Cleaning up temporary directory: {self._temp_dir}")
                shutil.rmtree(self._temp_dir)
                self._temp_dir = None
                self.cloned_repo_path = None
                logger.debug("Temporary directory removed")
            except Exception as e:
                logger.warning(f"Error cleaning up temporary directory: {e}")
    
    def check_season_exists(self, season: str) -> bool:
        """Check if a season directory exists in the cloned repository.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            True if season directory exists
        """
        if not self.cloned_repo_path:
            return False
        
        season_path = self.cloned_repo_path / season
        return season_path.exists() and season_path.is_dir()
    
    def list_files_in_season(self, season: str) -> List[str]:
        """List available files in a season directory.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            
        Returns:
            List of file names in the season directory
        """
        if not self.cloned_repo_path:
            return []
        
        season_path = self.cloned_repo_path / season
        if not season_path.exists():
            return []
        
        files = []
        for file_path in season_path.iterdir():
            if file_path.is_file() and file_path.suffix == ".txt":
                files.append(file_path.name)
        
        return files
    
    def fetch_season_file(self, season: str, competition: str = "CL") -> Optional[str]:
        """Read a season file from the cloned repository.
        
        Args:
            season: Season identifier (e.g., "2023-24")
            competition: Competition type (CL, EL, UCL)
            
        Returns:
            File content as string, or None if not found
        """
        if not self.cloned_repo_path:
            logger.warning("Repository not cloned. Call clone_repository() first.")
            return None
        
        # Check if season directory exists
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
        
        season_path = self.cloned_repo_path / season
        
        # Try files that actually exist in the directory
        for file_name in file_names_to_try:
            if file_name in available_files:
                file_path = season_path / file_name
                try:
                    content = file_path.read_text(encoding='utf-8')
                    logger.info(f"Read {season} {competition} from cloned repo ({file_name})")
                    return content
                except Exception as e:
                    logger.debug(f"Error reading {file_path}: {e}")
                    continue
        
        # If no specific file found, try any .txt file in the directory
        # (some seasons might have a single file with all competitions)
        for file_name in available_files:
            if file_name.endswith('.txt') and file_name not in file_names_to_try:
                file_path = season_path / file_name
                try:
                    content = file_path.read_text(encoding='utf-8')
                    # Check if content contains our competition
                    content_lower = content.lower()
                    comp_keywords = {
                        "CL": ["champions league", "uefa champions"],
                        "EL": ["europa league", "uefa europa"],
                        "UCL": ["conference league", "uefa conference", "europa conference"]
                    }
                    keywords = comp_keywords.get(competition, [])
                    if any(keyword in content_lower for keyword in keywords):
                        logger.info(f"Read {season} {competition} from cloned repo ({file_name})")
                        return content
                except Exception as e:
                    logger.debug(f"Error reading {file_path}: {e}")
                    continue
        
        logger.warning(f"Could not find file for season {season}, competition {competition}")
        return None
    
    def list_available_seasons(self) -> List[str]:
        """List available seasons in the cloned repository.
        
        Returns:
            List of season identifiers
        """
        if not self.cloned_repo_path:
            return []
        
        seasons = []
        for item in self.cloned_repo_path.iterdir():
            if item.is_dir() and item.name.startswith("20"):
                # Check if it's a season directory (format: YYYY-YY)
                name = item.name
                if len(name) == 7 and name[4] == "-":
                    seasons.append(name)
        
        seasons.sort(reverse=True)  # Most recent first
        logger.info(f"Found {len(seasons)} seasons in repository")
        return seasons
    
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
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup temporary directory."""
        self.cleanup()

