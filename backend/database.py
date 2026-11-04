"""DuckDB database connection and schema management."""
import os
import logging
import threading
import duckdb
from contextlib import contextmanager
from typing import Optional, Set
from pathlib import Path
from backend.competitions import configured_competition

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
        # The application shares this single DuckDB connection between the
        # scheduler, the startup history thread and request handlers. Every
        # public connection operation is guarded by this one reentrant lock so a
        # competing commit cannot interleave with a multi-statement import and
        # commit a partial result. Created before connecting so a failed-schema
        # ``close()`` is safe.
        self._lock = threading.RLock()
        self._connect()
        try:
            self._initialize_schema()
        except BaseException:
            # A failed (e.g. rolled-back) migration must not leave a half-open
            # connection behind; close before reraising so callers see a clean
            # failure and no leaked handle.
            self.close()
            raise
    
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
        
        # Reviewed retired-team identity redirects. A retired row is retained as
        # provenance; this table marks it as resolving to a final active root so
        # runtime writers (matcher, football-data/ESPN ingestion, offline repair)
        # can canonicalize before referencing, rather than resurrecting the
        # retired id. Both columns reference real teams and a row may never point
        # at itself; targets must be final roots (no chains/cycles), enforced by
        # the shared resolver, not guessed here. Idempotent: an existing table is
        # left untouched, so prior migrations are preserved.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS team_redirects (
                retired_team_id INTEGER PRIMARY KEY,
                canonical_team_id INTEGER NOT NULL,
                CHECK (retired_team_id != canonical_team_id),
                FOREIGN KEY (retired_team_id) REFERENCES teams(id),
                FOREIGN KEY (canonical_team_id) REFERENCES teams(id)
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
        
        # Standings table. Live-only snapshot keyed by (competition_id, team_id)
        # so the same club holds distinct rows per competition. The DEFAULT 'CL'
        # keeps legacy test/import inserts that omit competition_id working.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS standings (
                competition_id TEXT NOT NULL DEFAULT 'CL',
                team_id INTEGER,
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
                PRIMARY KEY (competition_id, team_id),
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)

        # Solkoff coefficients table. Live-only snapshot keyed by
        # (competition_id, team_id).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS solkoff_coefficients (
                competition_id TEXT NOT NULL DEFAULT 'CL',
                team_id INTEGER,
                solkoff_value REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (competition_id, team_id),
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)

        # Atomic, idempotent migration of legacy single-competition snapshots to
        # the competition-keyed shape. An old INTEGER solkoff_value is upgraded
        # to REAL in the same rebuild. Legacy rows belong to the validated
        # configured default; NULL match provenance is never attributed here.
        self._migrate_snapshot_tables()

        # Persistent provider identity mappings. A provider's own IDs are never
        # canonical: they resolve to an existing/new canonical team or match row
        # so EL/UCL ingestion reuses shared team identity and fixture rows.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_team_mappings (
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                team_id INTEGER NOT NULL,
                PRIMARY KEY (provider, external_id),
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_match_mappings (
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                match_id INTEGER NOT NULL,
                PRIMARY KEY (provider, external_id),
                FOREIGN KEY (match_id) REFERENCES matches(id)
            )
        """)

        # Elo ratings table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS elo_ratings (
                team_id INTEGER PRIMARY KEY,
                rating REAL NOT NULL,
                matches_played INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # API cache table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                response_data TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        
        # Migration: Add missing columns to existing api_cache table if needed
        try:
            columns_info = self.conn.execute("DESCRIBE api_cache").fetchall()
            existing_columns = {col[0] for col in columns_info}
            
            # Add endpoint column if missing
            if 'endpoint' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE api_cache ADD COLUMN endpoint TEXT")
                    # Set default value for existing rows
                    self.conn.execute("UPDATE api_cache SET endpoint = '' WHERE endpoint IS NULL")
                    logger.info("Added 'endpoint' column to api_cache table")
                except Exception as e:
                    logger.debug(f"Could not add 'endpoint' column: {e}")
            
            # Migrate created_at to cached_at if needed
            if 'created_at' in existing_columns and 'cached_at' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE api_cache ADD COLUMN cached_at TEXT")
                    # Copy data from created_at to cached_at
                    self.conn.execute("UPDATE api_cache SET cached_at = created_at WHERE cached_at IS NULL")
                    # Drop old column
                    self.conn.execute("ALTER TABLE api_cache DROP COLUMN created_at")
                    logger.info("Migrated 'created_at' to 'cached_at' in api_cache table")
                except Exception as e:
                    logger.debug(f"Could not migrate created_at to cached_at: {e}")
            elif 'cached_at' not in existing_columns:
                # If neither exists, add cached_at
                try:
                    self.conn.execute("ALTER TABLE api_cache ADD COLUMN cached_at TEXT")
                    # Set default for existing rows
                    self.conn.execute("UPDATE api_cache SET cached_at = CURRENT_TIMESTAMP::TEXT WHERE cached_at IS NULL")
                    logger.info("Added 'cached_at' column to api_cache table")
                except Exception as e:
                    logger.debug(f"Could not add 'cached_at' column: {e}")
                    
        except Exception as e:
            # Table might not exist yet, which is fine
            logger.debug(f"Could not check api_cache table columns (table may not exist yet): {e}")
        
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
            
            # Add competition_id column if missing. Existing rows keep NULL:
            # their competition provenance is unknown and must never be guessed
            # as CL. New inserts default via the schema/DDL, not a blanket UPDATE.
            if 'competition_id' not in existing_columns:
                try:
                    self.conn.execute("ALTER TABLE matches ADD COLUMN competition_id TEXT")
                    logger.info("Added 'competition_id' column to matches table")
                except Exception as e:
                    logger.debug(f"Could not add 'competition_id' column: {e}")
                    
        except Exception as e:
            # Table might not exist yet, which is fine (will be created by CREATE TABLE IF NOT EXISTS)
            logger.debug(f"Could not check matches table columns (table may not exist yet): {e}")

    def _table_columns(self, table: str) -> Set[str]:
        """Existing column names for ``table`` (empty when the table is absent)."""
        rows = self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {row[0] for row in rows}

    def _primary_key_columns(self, table: str) -> Optional[Set[str]]:
        """Primary-key column names for ``table``, or ``None`` when unknown."""
        try:
            rows = self.conn.execute(
                "SELECT constraint_column_names FROM duckdb_constraints() "
                "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
                (table,),
            ).fetchall()
        except Exception as e:
            logger.debug(f"Could not read primary key for {table}: {e}")
            return None
        if not rows:
            return set()
        names = rows[0][0]
        if isinstance(names, str):
            return {names}
        return set(names)

    def _snapshot_needs_migration(self, table: str) -> bool:
        """True unless ``table`` already has the competition-keyed primary key
        (and, for Solkoff, a REAL ``solkoff_value``)."""
        columns = self._table_columns(table)
        if "competition_id" not in columns:
            return True
        if self._primary_key_columns(table) != {"competition_id", "team_id"}:
            return True
        if table == "solkoff_coefficients" and self._solkoff_value_not_real():
            return True
        return False

    def _solkoff_value_not_real(self) -> bool:
        """True when an existing ``solkoff_value`` is not yet REAL/DOUBLE.

        Guards the legacy INTEGER -> REAL upgrade even when the primary key is
        already competition-keyed, so the conversion cannot be skipped just
        because the key shape happens to be current.
        """
        row = self.conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'solkoff_coefficients' AND column_name = 'solkoff_value'"
        ).fetchone()
        if not row or not row[0]:
            return False
        return str(row[0]).upper() not in ("FLOAT", "DOUBLE", "REAL")

    def _migrate_snapshot_tables(self):
        """Rebuild live snapshot tables to the competition-keyed primary key.

        Idempotent (no-op once migrated) and atomic: every rebuild happens in a
        single transaction that rolls back on any failure, so a partial migration
        can never be observed. Legacy rows are attributed to the validated
        configured default competition; row values and the ``teams`` foreign key
        are preserved.
        """
        standings_needs = self._snapshot_needs_migration("standings")
        solkoff_needs = self._snapshot_needs_migration("solkoff_coefficients")
        if not standings_needs and not solkoff_needs:
            return

        default_competition = configured_competition()
        self.conn.execute("BEGIN TRANSACTION")
        try:
            if standings_needs:
                self._rebuild_standings(default_competition)
            if solkoff_needs:
                self._rebuild_solkoff(default_competition)
            self.conn.execute("COMMIT")
            logger.info(
                "Migrated live snapshot tables to competition-keyed primary keys "
                "(default competition %s)",
                default_competition,
            )
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _rebuild_standings(self, default_competition: str):
        """Copy ``standings`` into a competition-keyed table (values preserved)."""
        self.conn.execute("DROP TABLE IF EXISTS standings_new")
        self.conn.execute("""
            CREATE TABLE standings_new (
                competition_id TEXT NOT NULL DEFAULT 'CL',
                team_id INTEGER,
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
                PRIMARY KEY (competition_id, team_id),
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        self.conn.execute("""
            INSERT INTO standings_new (
                competition_id, team_id, position, played, won, drawn, lost,
                goals_for, goals_against, goal_difference, points, last_updated
            )
            SELECT ?, team_id, position, played, won, drawn, lost,
                   goals_for, goals_against, goal_difference, points, last_updated
            FROM standings
        """, (default_competition,))
        self.conn.execute("DROP TABLE standings")
        self.conn.execute("ALTER TABLE standings_new RENAME TO standings")

    def _rebuild_solkoff(self, default_competition: str):
        """Copy ``solkoff_coefficients`` into a competition-keyed REAL table.

        Also converts a legacy INTEGER ``solkoff_value`` to REAL so the old
        REAL migration can never recreate the obsolete primary key. When the
        source already carries a ``competition_id`` (a REAL-only re-migration),
        that value is preserved instead of being collapsed onto the default, so
        rows from other competitions are never merged or lost.
        """
        source_columns = self._table_columns("solkoff_coefficients")
        if "competition_id" in source_columns:
            competition_expr = "COALESCE(competition_id, ?)"
        else:
            competition_expr = "?"
        self.conn.execute("DROP TABLE IF EXISTS solkoff_coefficients_new")
        self.conn.execute("""
            CREATE TABLE solkoff_coefficients_new (
                competition_id TEXT NOT NULL DEFAULT 'CL',
                team_id INTEGER,
                solkoff_value REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (competition_id, team_id),
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)
        self.conn.execute(f"""
            INSERT INTO solkoff_coefficients_new (
                competition_id, team_id, solkoff_value, calculated_at
            )
            SELECT {competition_expr}, team_id, CAST(solkoff_value AS REAL), calculated_at
            FROM solkoff_coefficients
        """, (default_competition,))
        self.conn.execute("DROP TABLE solkoff_coefficients")
        self.conn.execute(
            "ALTER TABLE solkoff_coefficients_new RENAME TO solkoff_coefficients"
        )

    def execute(self, query: str, parameters: Optional[tuple] = None):
        """Execute a SQL query.

        Args:
            query: SQL query string
            parameters: Optional query parameters
        """
        with self._lock:
            if parameters:
                return self.conn.execute(query, parameters)
            return self.conn.execute(query)

    def fetchall(self, query: str, parameters: Optional[tuple] = None):
        """Execute query and fetch all results.

        The execute and the fetch happen under one lock acquisition so a result
        cursor is materialized before another thread can reuse the shared
        connection (never a stale cursor handed back to the caller).

        Args:
            query: SQL query string
            parameters: Optional query parameters

        Returns:
            List of result rows
        """
        with self._lock:
            return self.execute(query, parameters).fetchall()

    def fetchone(self, query: str, parameters: Optional[tuple] = None):
        """Execute query and fetch one result.

        Execute and fetch are held under one lock acquisition (see
        :meth:`fetchall`); no cursor is exposed to the caller.

        Args:
            query: SQL query string
            parameters: Optional query parameters

        Returns:
            Single result row or None
        """
        with self._lock:
            return self.execute(query, parameters).fetchone()

    @contextmanager
    def transaction(self):
        """Run a block as one atomic transaction while holding the shared lock.

        The lock is held for the whole BEGIN..COMMIT/ROLLBACK so a competing
        thread's commit (e.g. an :class:`~backend.api_cache.APICache` write)
        cannot slip in between and commit a partial import. Do all HTTP/external
        work before entering; never perform network I/O inside this block.
        """
        with self._lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                yield self
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def commit(self):
        """Commit current transaction."""
        with self._lock:
            self.conn.commit()

    def close(self):
        """Close database connection."""
        with self._lock:
            if self.conn:
                self.conn.close()
                self.conn = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
