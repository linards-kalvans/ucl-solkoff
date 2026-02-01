"""Tests for data service module."""
import pytest
from unittest.mock import Mock, patch
from backend.data_service import DataService
from backend.database import Database
from backend.api_client import APIClient


@pytest.fixture
def mock_db():
    """Create mock database."""
    db = Mock(spec=Database)
    db.execute = Mock()
    db.commit = Mock()
    return db


@pytest.fixture
def mock_api_client():
    """Create mock API client."""
    client = Mock(spec=APIClient)
    return client


@pytest.fixture
def data_service(mock_db, mock_api_client):
    """Create data service instance."""
    return DataService(mock_db, mock_api_client)


def test_data_service_initialization(data_service, mock_db, mock_api_client):
    """Test data service initialization."""
    assert data_service.db == mock_db
    assert data_service.api_client == mock_api_client


def test_sync_teams_from_standings(data_service, mock_db, mock_api_client):
    """Test syncing teams from standings data."""
    mock_api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {"id": 1, "name": "Team A", "tla": "TEA", "crest": "url1"},
                    "position": 1
                },
                {
                    "team": {"id": 2, "name": "Team B", "tla": "TEB", "crest": "url2"},
                    "position": 2
                }
            ]
        }]
    }
    mock_api_client.get_competition_matches.return_value = {"matches": []}
    
    data_service.sync_teams("CL")
    
    # Should execute INSERT for each team
    assert mock_db.execute.call_count >= 2
    mock_db.commit.assert_called_once()


def test_sync_teams_from_matches(data_service, mock_db, mock_api_client):
    """Test syncing teams from matches data."""
    mock_api_client.get_competition_standings.return_value = {"standings": []}
    mock_api_client.get_competition_matches.return_value = {
        "matches": [
            {
                "homeTeam": {"id": 1, "name": "Home", "shortName": "HOM", "crest": "url1"},
                "awayTeam": {"id": 2, "name": "Away", "shortName": "AWY", "crest": "url2"}
            }
        ]
    }
    
    data_service.sync_teams("CL")
    
    # Should execute INSERT for teams from matches
    assert mock_db.execute.call_count >= 2
    mock_db.commit.assert_called_once()


def test_sync_matches(data_service, mock_db, mock_api_client):
    """Test syncing matches."""
    mock_api_client.get_competition_matches.return_value = {
        "matches": [
            {
                "id": 1,
                "homeTeam": {"id": 1, "name": "Home"},
                "awayTeam": {"id": 2, "name": "Away"},
                "score": {"fullTime": {"home": 2, "away": 1}},
                "matchday": 1,
                "utcDate": "2024-01-01T00:00:00Z",
                "status": "FINISHED"
            }
        ]
    }
    
    data_service.sync_matches("CL")
    
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
    
    # Check that correct parameters were passed
    call_args = mock_db.execute.call_args
    assert "INSERT INTO matches" in call_args[0][0]
    assert len(call_args[0][1]) == 11  # 11 parameters (id, home_team_id, away_team_id, home_score, away_score, matchday, date, status, stage, round, group_name)


def test_sync_standings(data_service, mock_db, mock_api_client):
    """Test syncing standings."""
    mock_api_client.get_competition_standings.return_value = {
        "standings": [{
            "table": [
                {
                    "team": {"id": 1, "name": "Team A"},
                    "position": 1,
                    "playedGames": 5,
                    "won": 3,
                    "draw": 1,
                    "lost": 1,
                    "goalsFor": 10,
                    "goalsAgainst": 5,
                    "goalDifference": 5,
                    "points": 10
                }
            ]
        }]
    }
    
    data_service.sync_standings("CL")
    
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
    
    # Check that correct parameters were passed
    call_args = mock_db.execute.call_args
    assert "INSERT INTO standings" in call_args[0][0]
    assert len(call_args[0][1]) == 11  # 11 parameters


def test_sync_all(data_service, mock_db, mock_api_client):
    """Test syncing all data."""
    mock_api_client.get_competition_standings.return_value = {
        "standings": [{"table": []}]
    }
    mock_api_client.get_competition_matches.return_value = {"matches": []}
    
    data_service.sync_all("CL")
    
    # Should call all sync methods
    assert mock_api_client.get_competition_standings.call_count >= 2
    assert mock_api_client.get_competition_matches.call_count >= 1
    assert mock_db.commit.call_count >= 3

