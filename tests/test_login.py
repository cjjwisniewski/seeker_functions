import pytest
import os
from unittest.mock import patch, MagicMock
import azure.functions as func
from urllib.parse import urlparse, parse_qs

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from login import main as login_main

# --- Constants ---
MOCK_CLIENT_ID = "test_client_id_123"
MOCK_REDIRECT_URI = "https://test-app.azurewebsites.net/api/callback"
EXPECTED_AUTH_URL_BASE = "https://discord.com/api/oauth2/authorize"
EXPECTED_SCOPES = "identify guilds guilds.members.read"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("DISCORD_CLIENT_ID", MOCK_CLIENT_ID)
    monkeypatch.setenv("DISCORD_REDIRECT_URI", MOCK_REDIRECT_URI)

def create_mock_request(params=None):
    """Helper to create a mock HttpRequest."""
    return func.HttpRequest(
        method='GET',
        url=f'/api/login?{params if params else ""}',
        headers={},
        params=params or {},
        body=None
    )

# --- Test Cases ---

def test_login_success_redirect_no_state():
    """Test successful login redirect with default state."""
    # Arrange
    req = create_mock_request()

    # Act
    response = login_main(req)

    # Assert
    assert response.status_code == 302
    assert 'Location' in response.headers
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query_params = parse_qs(parsed_url.query)

    assert parsed_url.scheme == 'https'
    assert parsed_url.netloc == 'discord.com'
    assert parsed_url.path == '/api/oauth2/authorize'
    assert query_params['client_id'] == [MOCK_CLIENT_ID]
    assert query_params['redirect_uri'] == [MOCK_REDIRECT_URI]
    assert query_params['response_type'] == ['code']
    assert query_params['scope'] == [EXPECTED_SCOPES]
    assert query_params['state'] == ['/'] # Default state
    assert query_params['prompt'] == ['consent']

def test_login_success_redirect_with_state():
    """Test successful login redirect with a specific state parameter."""
    # Arrange
    state_value = "/dashboard/settings"
    req = create_mock_request(params={'state': state_value})

    # Act
    response = login_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query_params = parse_qs(parsed_url.query)

    assert query_params['state'] == [state_value] # Check state is passed correctly
    # Other params should be the same
    assert query_params['client_id'] == [MOCK_CLIENT_ID]
    assert query_params['redirect_uri'] == [MOCK_REDIRECT_URI]
    assert query_params['scope'] == [EXPECTED_SCOPES]

def test_login_missing_client_id(monkeypatch):
    """Test login when DISCORD_CLIENT_ID is not set."""
    # Arrange
    monkeypatch.delenv("DISCORD_CLIENT_ID")
    req = create_mock_request()

    # Act
    response = login_main(req)

    # Assert
    assert response.status_code == 500
    assert b"Server configuration error" in response.get_body()

def test_login_missing_redirect_uri(monkeypatch):
    """Test login when DISCORD_REDIRECT_URI is not set."""
    # Arrange
    monkeypatch.delenv("DISCORD_REDIRECT_URI")
    req = create_mock_request()

    # Act
    response = login_main(req)

    # Assert
    assert response.status_code == 500
    assert b"Server configuration error" in response.get_body()
