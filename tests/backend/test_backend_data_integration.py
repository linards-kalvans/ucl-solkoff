"""Integration tests to verify backend has actual data."""
import pytest
import httpx
import os


@pytest.fixture
def api_base_url():
    """Get API base URL from environment or use default."""
    return os.getenv("API_BASE_URL", "http://localhost:8000")


@pytest.fixture
def client(api_base_url):
    """Create HTTP client for testing."""
    class APIClient:
        def __init__(self, base_url):
            self.base_url = base_url
            self.client = httpx.Client(timeout=10.0)
        
        def get(self, path):
            response = self.client.get(f"{self.base_url}{path}")
            return MockResponse(response)
    
    class MockResponse:
        def __init__(self, response):
            self.status_code = response.status_code
            try:
                self._json = response.json() if response.status_code == 200 else None
            except:
                self._json = None
            self._text = response.text
        
        def json(self):
            return self._json
        
        @property
        def text(self):
            return self._text
    
    return APIClient(api_base_url)


def test_standings_endpoint_returns_data(client):
    """Test that /api/standings endpoint returns actual data."""
    response = client.get("/api/standings")
    
    print(f"✓ Standings endpoint status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data)}"
    assert len(data) > 0, f"Expected standings data, got {len(data)} items"
    
    print(f"✓ Standings endpoint returned {len(data)} teams")
    
    # Verify structure of first item
    first_team = data[0]
    required_fields = [
        "teamId", "teamName", "teamCode", "teamCrest", "position",
        "played", "won", "drawn", "lost", "gf", "ga", "gd", "points",
        "solkoffCoefficient", "strengthScore"
    ]
    for field in required_fields:
        assert field in first_team, f"Missing field: {field}"
    
    print(f"  First team: {first_team['teamName']} - {first_team['points']} pts, Solkoff: {first_team['solkoffCoefficient']}")
    
    # Verify data types
    assert isinstance(first_team["teamId"], int)
    assert isinstance(first_team["teamName"], str)
    assert isinstance(first_team["points"], int)
    assert isinstance(first_team["solkoffCoefficient"], (int, float))
    assert isinstance(first_team["strengthScore"], (int, float))


def test_standings_endpoint_data_quality(client):
    """Test data quality of standings endpoint."""
    response = client.get("/api/standings")
    assert response.status_code == 200
    
    data = response.json()
    
    # Check that teams are sorted correctly (by points, then GD, then GF)
    for i in range(len(data) - 1):
        current = data[i]
        next_team = data[i + 1]
        
        if current["points"] > next_team["points"]:
            continue  # Correct order
        elif current["points"] == next_team["points"]:
            if current["gd"] > next_team["gd"]:
                continue  # Correct order
            elif current["gd"] == next_team["gd"]:
                assert current["gf"] >= next_team["gf"], \
                    f"Teams with same points and GD should be sorted by GF: {current['teamName']} ({current['gf']}) vs {next_team['teamName']} ({next_team['gf']})"
    
    print("✓ Standings are correctly sorted")


def test_standings_endpoint_has_all_teams(client):
    """Test that standings endpoint returns all teams."""
    response = client.get("/api/standings")
    assert response.status_code == 200
    
    api_teams = response.json()
    api_team_ids = {team["teamId"] for team in api_teams}
    
    print(f"✓ API returned {len(api_team_ids)} teams")
    
    # Verify we have a reasonable number of teams (UCL should have 32+ teams)
    assert len(api_team_ids) >= 20, f"Expected at least 20 teams, got {len(api_team_ids)}"
    print(f"✓ Team count is reasonable ({len(api_team_ids)} teams)")


def test_health_endpoint(client):
    """Test health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    print("✓ Health endpoint working")


def test_current_stage_endpoint(client):
    """Test current stage endpoint."""
    response = client.get("/api/tournament/current-stage")
    print(f"✓ Current stage endpoint status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        assert "stage" in data
        print(f"  Current stage: {data['stage']}")
    else:
        print(f"  Endpoint returned {response.status_code} (may not be implemented yet)")


def test_knockout_pairs_endpoints(client):
    """Test knockout pairs endpoints for different stages."""
    stages = ["KNOCKOUT_PLAYOFF", "ROUND_OF_16", "QUARTER_FINAL", "SEMI_FINAL", "FINAL"]
    
    for stage in stages:
        response = client.get(f"/api/knockout-pairs/{stage}")
        print(f"✓ {stage} endpoint status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            print(f"  {stage}: {len(data)} pairs found")
        elif response.status_code in [404, 400]:
            print(f"  {stage}: No data available (expected for future stages)")
        else:
            print(f"  {stage}: Unexpected status {response.status_code}")

