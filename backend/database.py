"""DuckDB database connection and schema management."""
import os
import duckdb
from typing import Optional
from pathlib import Path


class Database:
    """Manages DuckDB connection and schema."""
    
    def __init__(self, db_path: str = "./data/ucl.db"):
        """Initialize database connection.
        
        Args:
            db_path: Path to DuckDB database file
        """
        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self.conn: Optional[duckdb.DuckDBPyConnection] = None
        self._connect()
        self._initialize_schema()
    
    def _connect(self):
        """Establish database connection."""
        self.conn = duckdb.connect(self.db_path)
    
    def _initialize_schema(self):
        """Create database tables if they don't exist."""
        # Teams table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT,
                crest TEXT
            )
        """)
        
        # Matches table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY,
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                home_score INTEGER,
                away_score INTEGER,
                matchday INTEGER,
                date TEXT,
                status TEXT,
                FOREIGN KEY (home_team_id) REFERENCES teams(id),
                FOREIGN KEY (away_team_id) REFERENCES teams(id)
            )
        """)
        
        # Standings table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS standings (
                team_id INTEGER PRIMARY KEY,
                position INTEGER,
                played INTEGER,
                won INTEGER,
                drawn INTEGER,
                lost INTEGER,
                goals_for INTEGER,
                goals_against INTEGER,
                goal_difference INTEGER,
                points INTEGER,
                last_updated TEXT,
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        
        # Solkoff coefficients table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS solkoff_coefficients (
                team_id INTEGER PRIMARY KEY,
                solkoff_value INTEGER NOT NULL,
                calculated_at TEXT NOT NULL,
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        
        self.conn.commit()
    
    def execute(self, query: str, parameters: Optional[tuple] = None):
        """Execute a SQL query.
        
        Args:
            query: SQL query string
            parameters: Optional query parameters
        """
        if parameters:
            return self.conn.execute(query, parameters)
        return self.conn.execute(query)
    
    def fetchall(self, query: str, parameters: Optional[tuple] = None):
        """Execute query and fetch all results.
        
        Args:
            query: SQL query string
            parameters: Optional query parameters
            
        Returns:
            List of result rows
        """
        return self.execute(query, parameters).fetchall()
    
    def fetchone(self, query: str, parameters: Optional[tuple] = None):
        """Execute query and fetch one result.
        
        Args:
            query: SQL query string
            parameters: Optional query parameters
            
        Returns:
            Single result row or None
        """
        return self.execute(query, parameters).fetchone()
    
    def commit(self):
        """Commit current transaction."""
        self.conn.commit()
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

