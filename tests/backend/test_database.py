"""Tests for database module."""
import pytest
import tempfile
import os
from pathlib import Path
from backend.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    # Create a temporary file path
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Remove the file if it exists (DuckDB will create it)
    if os.path.exists(db_path):
        os.unlink(db_path)
    
    db = Database(db_path=db_path)
    yield db
    
    db.close()
    # Clean up database files
    if os.path.exists(db_path):
        os.unlink(db_path)
    if os.path.exists(db_path + '.wal'):
        os.unlink(db_path + '.wal')


def test_database_initialization(temp_db):
    """Test database initialization creates tables."""
    # Check that tables exist
    tables = temp_db.fetchall("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN ('teams', 'matches', 'standings', 'solkoff_coefficients')
    """)
    table_names = [row[0] for row in tables]
    
    assert 'teams' in table_names
    assert 'matches' in table_names
    assert 'standings' in table_names
    assert 'solkoff_coefficients' in table_names


def test_teams_table_structure(temp_db):
    """Test teams table has correct structure."""
    # Insert a test team
    temp_db.execute("""
        INSERT INTO teams (id, name, code) 
        VALUES (1, 'Test Team', 'TT')
    """)
    temp_db.commit()
    
    # Verify insertion
    result = temp_db.fetchone("SELECT name, code FROM teams WHERE id = 1")
    assert result[0] == 'Test Team'
    assert result[1] == 'TT'


def test_matches_table_structure(temp_db):
    """Test matches table has correct structure."""
    # Insert test teams
    temp_db.execute("INSERT INTO teams (id, name) VALUES (1, 'Team A')")
    temp_db.execute("INSERT INTO teams (id, name) VALUES (2, 'Team B')")
    
    # Insert a match
    temp_db.execute("""
        INSERT INTO matches (id, home_team_id, away_team_id, home_score, away_score, matchday)
        VALUES (1, 1, 2, 2, 1, 1)
    """)
    temp_db.commit()
    
    # Verify insertion
    result = temp_db.fetchone("SELECT home_score, away_score FROM matches WHERE id = 1")
    assert result[0] == 2
    assert result[1] == 1


def test_standings_table_structure(temp_db):
    """Test standings table has correct structure."""
    # Insert test team
    temp_db.execute("INSERT INTO teams (id, name) VALUES (1, 'Test Team')")
    
    # Insert standings
    temp_db.execute("""
        INSERT INTO standings (team_id, position, played, won, drawn, lost, 
                             goals_for, goals_against, goal_difference, points)
        VALUES (1, 1, 5, 3, 1, 1, 10, 5, 5, 10)
    """)
    temp_db.commit()
    
    # Verify insertion
    result = temp_db.fetchone("SELECT points, goal_difference FROM standings WHERE team_id = 1")
    assert result[0] == 10
    assert result[1] == 5


def test_solkoff_coefficients_table_structure(temp_db):
    """Test solkoff_coefficients table has correct structure."""
    # Insert test team
    temp_db.execute("INSERT INTO teams (id, name) VALUES (1, 'Test Team')")
    
    # Insert Solkoff coefficient
    temp_db.execute("""
        INSERT INTO solkoff_coefficients (team_id, solkoff_value, calculated_at)
        VALUES (1, 25, '2024-01-01T00:00:00Z')
    """)
    temp_db.commit()
    
    # Verify insertion
    result = temp_db.fetchone("SELECT solkoff_value FROM solkoff_coefficients WHERE team_id = 1")
    assert result[0] == 25


def test_context_manager(temp_db):
    """Test database context manager."""
    with Database(db_path=temp_db.db_path) as db:
        assert db.conn is not None
        result = db.fetchone("SELECT 1")
        assert result[0] == 1

