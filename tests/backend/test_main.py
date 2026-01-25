"""Tests for main FastAPI application."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
from backend.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Create mock database."""
    db = Mock()
    db.fetchall = Mock(return_value=[])
    return db


@pytest.fixture
def mock_scheduler():
    """Create mock scheduler."""
    scheduler = Mock()
    scheduler.start = Mock()
    scheduler.stop = Mock()
    scheduler.trigger_update = Mock()
    return scheduler


@patch('backend.main.Database')
@patch('backend.main.DataScheduler')
def test_root_endpoint(mock_scheduler_class, mock_db_class, client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    # Root endpoint serves frontend HTML if available, or JSON fallback
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        # Frontend is being served
        assert "UEFA Champions League" in response.text or "UCL" in response.text
    else:
        # JSON fallback
        assert "message" in response.json()
        assert response.json()["message"] == "UCL Solkoff API"


@patch('backend.main.Database')
@patch('backend.main.DataScheduler')
def test_health_endpoint(mock_scheduler_class, mock_db_class, client):
    """Test health check endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "status" in response.json()
    assert response.json()["status"] == "healthy"


@patch('backend.main.db')
def test_standings_endpoint(mock_db, client):
    """Test standings endpoint."""
    # Mock database response (now includes team_crest and strength_score columns)
    mock_db.fetchall.return_value = [
        (1, "Team A", "TEA", "https://example.com/logo1.png", 1, 5, 3, 1, 1, 10, 5, 5, 10, 25, 250),
        (2, "Team B", "TEB", "https://example.com/logo2.png", 2, 5, 2, 2, 1, 8, 7, 1, 8, 20, 160)
    ]
    
    response = client.get("/api/standings")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["teamName"] == "Team A"
    assert data[0]["teamCrest"] == "https://example.com/logo1.png"
    assert data[0]["points"] == 10
    assert data[0]["solkoffCoefficient"] == 25
    assert data[0]["strengthScore"] == 250
    assert data[1]["teamName"] == "Team B"
    assert data[1]["teamCrest"] == "https://example.com/logo2.png"
    assert data[1]["solkoffCoefficient"] == 20
    assert data[1]["strengthScore"] == 160


def test_standings_endpoint_no_db(client):
    """Test standings endpoint when database is not initialized."""
    # This test verifies the endpoint handles None db gracefully
    # The actual None check happens in the endpoint code
    # We'll test the error case by mocking db to be None
    with patch('backend.main.db', None):
        response = client.get("/api/standings")
        assert response.status_code == 503


@patch('backend.main.scheduler')
def test_refresh_endpoint(mock_scheduler, client):
    """Test refresh endpoint."""
    mock_scheduler.trigger_update = Mock()
    
    response = client.post("/api/refresh")
    
    assert response.status_code == 200
    assert "message" in response.json()
    mock_scheduler.trigger_update.assert_called_once()


def test_refresh_endpoint_no_scheduler(client):
    """Test refresh endpoint when scheduler is not initialized."""
    with patch('backend.main.scheduler', None):
        response = client.post("/api/refresh")
        assert response.status_code == 503


@patch('backend.main.db')
def test_standings_ordering(mock_db, client):
    """Test that standings are ordered correctly."""
    # Note: SQL ORDER BY is applied, so results come pre-sorted
    # Team A has higher points (10) so should be first
    mock_db.fetchall.return_value = [
        (1, "Team A", "TEA", "https://example.com/logo1.png", 1, 5, 3, 1, 1, 10, 5, 5, 10, 25, 250),  # Higher points
        (2, "Team B", "TEB", "https://example.com/logo2.png", 2, 5, 2, 2, 1, 8, 7, 1, 8, 30, 240)   # Lower points but higher Solkoff
    ]
    
    response = client.get("/api/standings")
    
    assert response.status_code == 200
    data = response.json()
    # Should be ordered by points DESC, then Solkoff DESC (SQL handles this)
    assert data[0]["points"] == 10  # Team A first (higher points)
    assert data[1]["points"] == 8   # Team B second


@patch('backend.main.db')
def test_solkoff_details_endpoint(mock_db, client):
    """Test Solkoff details endpoint."""
    # Mock team info
    mock_db.fetchone.side_effect = [
        (1, "Team A", "TEA", "https://example.com/logo.png"),  # Team info
        (25,)  # Solkoff value
    ]
    
    # Mock opponent data (home and away matches) with scores and dates
    # Format: (opponent_id, name, crest, points, home_score, away_score, date)
    mock_db.fetchall.side_effect = [
        [(2, "Team B", "https://example.com/b.png", 10, 2, 1, "2024-01-01T00:00:00Z")],  # Home opponents
        [(3, "Team C", "https://example.com/c.png", 15, 1, 2, "2024-01-02T00:00:00Z")]   # Away opponents
    ]
    
    response = client.get("/api/teams/1/solkoff-details")
    
    assert response.status_code == 200
    data = response.json()
    assert data["teamId"] == 1
    assert data["teamName"] == "Team A"
    assert data["solkoffCoefficient"] == 25
    assert len(data["opponents"]) == 2
    assert data["totalOpponentPoints"] == 25
    # Check that matches are included
    assert "matches" in data["opponents"][0]
    assert len(data["opponents"][0]["matches"]) == 1


@patch('backend.main.db')
def test_solkoff_details_team_not_found(mock_db, client):
    """Test Solkoff details endpoint with non-existent team."""
    mock_db.fetchone.return_value = None
    
    response = client.get("/api/teams/999/solkoff-details")
    
    assert response.status_code == 404

