"""Tests for Solkoff calculator module."""
import pytest
from unittest.mock import Mock
from backend.solkoff_calculator import SolkoffCalculator
from backend.database import Database


@pytest.fixture
def mock_db():
    """Create mock database."""
    db = Mock(spec=Database)
    return db


@pytest.fixture
def calculator(mock_db):
    """Create Solkoff calculator instance."""
    return SolkoffCalculator(mock_db)


def test_calculator_initialization(calculator, mock_db):
    """Test calculator initialization."""
    assert calculator.db == mock_db


def test_get_opponents_home_matches(calculator, mock_db):
    """Test getting opponents from home matches."""
    mock_db.fetchall.side_effect = [
        [(2,), (3,)],  # Home matches (away_team_id)
        []  # Away matches
    ]
    
    opponents = calculator.get_opponents(1)
    
    assert opponents == {2, 3}
    assert mock_db.fetchall.call_count == 2


def test_get_opponents_away_matches(calculator, mock_db):
    """Test getting opponents from away matches."""
    mock_db.fetchall.side_effect = [
        [],  # Home matches
        [(2,), (4,)]  # Away matches (home_team_id)
    ]
    
    opponents = calculator.get_opponents(1)
    
    assert opponents == {2, 4}


def test_get_opponents_both(calculator, mock_db):
    """Test getting opponents from both home and away matches."""
    mock_db.fetchall.side_effect = [
        [(2,), (3,)],  # Home matches
        [(4,), (5,)]  # Away matches
    ]
    
    opponents = calculator.get_opponents(1)
    
    assert opponents == {2, 3, 4, 5}


def test_calculate_solkoff_no_opponents(calculator, mock_db):
    """Test Solkoff calculation with no opponents."""
    mock_db.fetchall.side_effect = [
        [],  # No home matches
        []  # No away matches
    ]
    
    result = calculator.calculate_solkoff(1)
    
    assert result == 0


def test_calculate_solkoff_with_opponents(calculator, mock_db):
    """Test Solkoff calculation averages opponent points per game."""
    # Mock get_opponents result
    mock_db.fetchall.side_effect = [
        [(2,), (3,)],  # Home matches
        [],  # Away matches
        [(10, 5), (15, 5)]  # (points, played) -> 2.0 and 3.0 PPG
    ]
    
    result = calculator.calculate_solkoff(1)
    
    assert result == 2.5  # (2.0 + 3.0) / 2


def test_calculate_solkoff_missing_standings(calculator, mock_db):
    """Test Solkoff calculation when opponent has no standings."""
    mock_db.fetchall.side_effect = [
        [(2,), (3,)],  # Home matches
        [],  # Away matches
        [(10, 5), None]  # One opponent has stats, other doesn't
    ]
    
    result = calculator.calculate_solkoff(1)
    
    assert result == 2.0  # Only counts the opponent with a standings row


def test_calculate_solkoff_ignores_zero_games(calculator, mock_db):
    """An opponent with zero games played cannot contribute a PPG value."""
    mock_db.fetchall.side_effect = [
        [(2,), (3,)],  # Home matches
        [],  # Away matches
        [(10, 5), (15, 0)]  # Second opponent has no games to divide by
    ]
    
    result = calculator.calculate_solkoff(1)
    
    assert result == 2.0


def test_calculate_all(calculator, mock_db):
    """Test calculating Solkoff for all teams."""
    # Mock: get all teams, then for each team get opponents and points
    mock_db.fetchall.side_effect = [
        [(1,), (2,)],  # All teams
        [(2,)],  # Team 1 opponents (home)
        [],  # Team 1 opponents (away)
        [(10, 5)],  # Team 1 opponent (points, played)
        [],  # Team 2 opponents (home)
        [],  # Team 2 opponents (away)
    ]
    
    calculator.calculate_all()
    
    # Should execute INSERT for each team
    assert mock_db.execute.call_count == 2
    mock_db.commit.assert_called_once()


def test_calculate_all_stores_values(calculator, mock_db):
    """Test that calculate_all stores values correctly."""
    mock_db.fetchall.side_effect = [
        [(1,)],  # All teams
        [(2,)],  # Team 1 opponents
        [],
        [(15, 5)],  # Opponent (points, played) -> 3.0 PPG
    ]
    
    calculator.calculate_all()
    
    # Check that execute was called with the current competition-keyed contract
    call_args = mock_db.execute.call_args
    assert "INSERT INTO solkoff_coefficients" in call_args[0][0]
    params = call_args[0][1]
    assert params[0] == "CL"  # competition_id
    assert params[1] == 1  # team_id
    assert params[2] == 3.0  # solkoff_value (average opponent PPG)
    assert params[3] is not None  # calculated_at timestamp

