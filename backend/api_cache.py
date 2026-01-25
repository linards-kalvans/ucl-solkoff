"""API response caching to reduce rate limit issues."""
import json
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from backend.database import Database


class APICache:
    """Cache API responses in database to reduce API calls."""
    
    def __init__(self, db: Database, default_ttl_seconds: int = 3600):
        """Initialize API cache.
        
        Args:
            db: Database instance
            default_ttl_seconds: Default time-to-live for cached responses (default: 1 hour)
        """
        self.db = db
        self.default_ttl = default_ttl_seconds
        self._initialize_cache_table()
    
    def _initialize_cache_table(self):
        """Create cache table if it doesn't exist."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                response_data TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        self.db.commit()
    
    def _generate_key(self, endpoint: str) -> str:
        """Generate cache key from endpoint.
        
        Args:
            endpoint: API endpoint path
            
        Returns:
            Cache key
        """
        return hashlib.md5(endpoint.encode()).hexdigest()
    
    def get(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Get cached response if available and not expired.
        
        Args:
            endpoint: API endpoint path
            
        Returns:
            Cached response data or None if not found/expired
        """
        cache_key = self._generate_key(endpoint)
        
        result = self.db.fetchone("""
            SELECT response_data, expires_at
            FROM api_cache
            WHERE cache_key = ?
        """, (cache_key,))
        
        if not result:
            return None
        
        response_data, expires_at_str = result
        expires_at = datetime.fromisoformat(expires_at_str)
        
        # Check if expired
        if datetime.utcnow() > expires_at:
            # Delete expired entry
            self.db.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
            self.db.commit()
            return None
        
        # Return cached data
        return json.loads(response_data)
    
    def set(self, endpoint: str, data: Dict[str, Any], ttl_seconds: Optional[int] = None):
        """Cache API response.
        
        Args:
            endpoint: API endpoint path
            data: Response data to cache
            ttl_seconds: Time-to-live in seconds (defaults to instance default)
        """
        cache_key = self._generate_key(endpoint)
        ttl = ttl_seconds or self.default_ttl
        
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=ttl)
        
        response_json = json.dumps(data)
        
        self.db.execute("""
            INSERT INTO api_cache (cache_key, endpoint, response_data, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (cache_key) DO UPDATE SET
                response_data = excluded.response_data,
                cached_at = excluded.cached_at,
                expires_at = excluded.expires_at
        """, (
            cache_key,
            endpoint,
            response_json,
            now.isoformat(),
            expires_at.isoformat()
        ))
        self.db.commit()
    
    def clear(self, endpoint: Optional[str] = None):
        """Clear cache entries.
        
        Args:
            endpoint: Specific endpoint to clear, or None to clear all
        """
        if endpoint:
            cache_key = self._generate_key(endpoint)
            self.db.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
        else:
            self.db.execute("DELETE FROM api_cache")
        self.db.commit()
    
    def cleanup_expired(self):
        """Remove expired cache entries."""
        now = datetime.utcnow().isoformat()
        self.db.execute("DELETE FROM api_cache WHERE expires_at < ?", (now,))
        self.db.commit()

