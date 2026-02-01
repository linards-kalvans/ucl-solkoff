"""DuckDB database connection and schema management."""
import os
import logging
import duckdb
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


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
                stage TEXT,
                round TEXT,
                group_name TEXT,
                competition_id TEXT,
                FOREIGN KEY (home_team_id) REFERENCES teams(id),
                FOREIGN KEY (away_team_id) REFERENCES teams(id)
            )
        """)
        
        # Migration: Add new columns to existing matches table if they don't exist
        self._migrate_matches_table()
        
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
                solkoff_value REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        
        # Migration: Update existing INTEGER column to REAL if needed
        try:
            # Check if table exists by trying to describe it
            columns_info = self.conn.execute("DESCRIBE solkoff_coefficients").fetchall()
            col_info = next((col for col in columns_info if col[0] == 'solkoff_value'), None)
            if col_info:
                col_type = str(col_info[1]).upper()
                if 'INTEGER' in col_type:
                    # DuckDB doesn't support ALTER COLUMN, so we need to recreate the table
                    logger.info("Migrating solkoff_coefficients table: converting INTEGER to REAL")
                    # Create new table with REAL type
                    self.conn.execute("""
                        CREATE TABLE IF NOT EXISTS solkoff_coefficients_new (
                            team_id INTEGER PRIMARY KEY,
                            solkoff_value REAL NOT NULL,
                            calculated_at TEXT NOT NULL,
                            FOREIGN KEY (team_id) REFERENCES teams(id)
                        )
                    """)
                    # Copy data, converting INTEGER to REAL (though values will need recalculation)
                    self.conn.execute("""
                        INSERT INTO solkoff_coefficients_new (team_id, solkoff_value, calculated_at)
                        SELECT team_id, CAST(solkoff_value AS REAL), calculated_at
                        FROM solkoff_coefficients
                    """)
                    # Drop old table
                    self.conn.execute("DROP TABLE solkoff_coefficients")
                    # Rename new table
                    self.conn.execute("ALTER TABLE solkoff_coefficients_new RENAME TO solkoff_coefficients")
                    self.conn.commit()
                    logger.info("Migration completed: solkoff_value is now REAL. Note: Values should be recalculated.")
        except Exception as e:
            # Table might not exist yet, which is fine
            logger.debug(f"Could not migrate solkoff_coefficients schema (this is OK if table doesn't exist): {e}")
        
        # API cache table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                response_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        
        self.conn.commit()
    
    def _migrate_matches_table(self):
        """Add new columns to existing matches table if they don't exist."""
        try:
            # Get column names from existing table using DuckDB's DESCRIBE
            columns_info = self.conn.execute("DESCRIBE matches").fetchall()
            existing_columns = {col[0] for col in columns_info}
            
            # Add stage column if missing
            if 'stage' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE matches ADD COLUMN stage TEXT")
                    logger.info("Added 'stage' column to matches table")
                except Exception as e:
                    logger.debug(f"Could not add 'stage' column: {e}")
            
            # Add round column if missing
            if 'round' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE matches ADD COLUMN round TEXT")
                    logger.info("Added 'round' column to matches table")
                except Exception as e:
                    logger.debug(f"Could not add 'round' column: {e}")
            
            # Add group_name column if missing
            if 'group_name' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE matches ADD COLUMN group_name TEXT")
                    logger.info("Added 'group_name' column to matches table")
                except Exception as e:
                    logger.debug(f"Could not add 'group_name' column: {e}")
            
            # Add competition_id column if missing
            if 'competition_id' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE matches ADD COLUMN competition_id TEXT")
                    logger.info("Added 'competition_id' column to matches table")
                    # Set default value for existing rows to 'CL' (Champions League)
                    self.conn.execute("UPDATE matches SET competition_id = 'CL' WHERE competition_id IS NULL")
                    self.conn.commit()
                except Exception as e:
                    logger.debug(f"Could not add 'competition_id' column: {e}")
                    
        except Exception as e:
            # Table might not exist yet, which is fine (will be created by CREATE TABLE IF NOT EXISTS)
            logger.debug(f"Could not check matches table columns (table may not exist yet): {e}")
    
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
