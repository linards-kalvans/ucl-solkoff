"""Tests for API client module."""
import pytest
from unittest.mock import patch, Mock
from backend.api_client import APIClient


@pytest.fixture
def api_client():
    """Create API client instance."""
    return APIClient(api_key="test_key", base_url="https://api.test.com/v4")


def test_api_client_initialization(api_client):
    """Test API client initialization."""
    assert api_client.api_key == "test_key"
    assert api_client.base_url == "https://api.test.com/v4"
    assert "X-Auth-Token" in api_client.headers
    assert api_client.headers["X-Auth-Token"] == "test_key"


def test_api_client_defaults():
    """Test API client uses environment variables as defaults."""
    with patch.dict("os.environ", {
        "EXTERNAL_API_KEY": "env_key",
        "EXTERNAL_API_BASE_URL": "https://env.api.com/v4"
    }):
        client = APIClient()
        assert client.api_key == "env_key"
        assert client.base_url == "https://env.api.com/v4"


@patch('httpx.Client')
def test_get_competition_standings(mock_client_class, api_client):
    """Test getting competition standings."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "competition": {"id": 2001, "name": "Champions League"},
        "standings": []
    }
    mock_response.raise_for_status = Mock()
    
    mock_client = Mock()
    mock_client.__enter__ = Mock(return_value=mock_client)
    mock_client.__exit__ = Mock(return_value=None)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = api_client.get_competition_standings("CL")
    
    assert "competition" in result
    assert "standings" in result
    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    assert "competitions/CL/standings" in call_args[0][0]


@patch('httpx.Client')
def test_get_competition_matches(mock_client_class, api_client):
    """Test getting competition matches."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "matches": [],
        "resultSet": {"count": 0}
    }
    mock_response.raise_for_status = Mock()
    
    mock_client = Mock()
    mock_client.__enter__ = Mock(return_value=mock_client)
    mock_client.__exit__ = Mock(return_value=None)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = api_client.get_competition_matches("CL")
    
    assert "matches" in result
    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    assert "competitions/CL/matches" in call_args[0][0]


@patch('httpx.Client')
def test_get_team(mock_client_class, api_client):
    """Test getting team information."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "id": 1,
        "name": "Test Team",
        "code": "TT"
    }
    mock_response.raise_for_status = Mock()
    
    mock_client = Mock()
    mock_client.__enter__ = Mock(return_value=mock_client)
    mock_client.__exit__ = Mock(return_value=None)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = api_client.get_team(1)
    
    assert result["id"] == 1
    assert result["name"] == "Test Team"
    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    assert "teams/1" in call_args[0][0]


@patch('httpx.Client')
def test_api_error_handling(mock_client_class, api_client):
    """Test API error handling."""
    import httpx
    
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=Mock(), response=Mock(status_code=404)
    )
    
    mock_client = Mock()
    mock_client.__enter__ = Mock(return_value=mock_client)
    mock_client.__exit__ = Mock(return_value=None)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    with pytest.raises(httpx.HTTPStatusError):
        api_client.get_competition_standings("CL")

