import pytest
import os
import base64
from unittest.mock import patch, MagicMock
import azure.functions as func
import requests

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logout import main as logout_main

# --- Constants ---
MOCK_CLIENT_ID = "test_client_id_logout"
MOCK_CLIENT_SECRET = "test_client_secret_logout"
MOCK_TOKEN = "valid.bearer.token"
REVOKE_URL = 'https://discord.com/api/v10/oauth2/token/revoke'

# --- Fixtures ---

@pytest.fixture
def mock_env_vars_present(monkeypatch):
    """Mock environment variables present."""
    monkeypatch.setenv("DISCORD_CLIENT_ID", MOCK_CLIENT_ID)
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", MOCK_CLIENT_SECRET)

@pytest.fixture
def mock_env_vars_missing(monkeypatch):
    """Mock environment variables missing."""
    if "DISCORD_CLIENT_ID" in os.environ:
        monkeypatch.delenv("DISCORD_CLIENT_ID")
    if "DISCORD_CLIENT_SECRET" in os.environ:
        monkeypatch.delenv("DISCORD_CLIENT_SECRET")

@pytest.fixture
def mock_requests_post(monkeypatch):
    """Fixture to mock requests.post."""
    with patch('logout.requests.post') as mock_post:
        yield mock_post

def create_mock_request(token=MOCK_TOKEN):
    """Helper to create a mock HttpRequest."""
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return func.HttpRequest(
        method='POST', # Assuming logout is POST, adjust if needed
        url='/api/logout',
        headers=headers,
        params={},
        body=None
    )

# --- Test Cases ---

def test_logout_success_token_revoked(mock_env_vars_present, mock_requests_post):
    """Test successful logout where token is provided and revocation succeeds."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    mock_response = MagicMock(status_code=200)
    mock_response.ok = True
    mock_requests_post.return_value = mock_response

    # Act
    response = logout_main(req)

    # Assert
    # 1. Check Discord API call
    expected_auth_str = f"{MOCK_CLIENT_ID}:{MOCK_CLIENT_SECRET}"
    expected_basic_auth = base64.b64encode(expected_auth_str.encode('utf-8')).decode('utf-8')
    mock_requests_post.assert_called_once_with(
        REVOKE_URL,
        data={'token': MOCK_TOKEN, 'token_type_hint': 'access_token'},
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {expected_basic_auth}'
        }
    )
    # 2. Check response to client
    assert response.status_code == 204 # No Content for success
    assert response.get_body() is None

def test_logout_success_no_token_provided(mock_env_vars_present, mock_requests_post):
    """Test successful logout when no Bearer token is in the header."""
    # Arrange
    req = create_mock_request(token=None) # No Authorization header

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API should NOT be called
    mock_requests_post.assert_not_called()
    # 2. Response to client should still be success
    assert response.status_code == 204
    assert response.get_body() is None

def test_logout_success_malformed_header(mock_env_vars_present, mock_requests_post):
    """Test successful logout with malformed Authorization header."""
    # Arrange
    req = create_mock_request(token=None) # Create basic request
    req.headers['Authorization'] = 'BearerTokenWithoutSpace' # Malformed header

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API should NOT be called
    mock_requests_post.assert_not_called()
    # 2. Response to client should still be success
    assert response.status_code == 204
    assert response.get_body() is None

def test_logout_success_non_bearer_header(mock_env_vars_present, mock_requests_post):
    """Test successful logout with non-Bearer Authorization header."""
    # Arrange
    req = create_mock_request(token=None) # Create basic request
    req.headers['Authorization'] = 'Basic some_other_auth' # Non-Bearer

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API should NOT be called
    mock_requests_post.assert_not_called()
    # 2. Response to client should still be success
    assert response.status_code == 204
    assert response.get_body() is None


def test_logout_success_discord_revocation_fails(mock_env_vars_present, mock_requests_post):
    """Test logout where Discord revocation fails (e.g., 400), but client still gets success."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    mock_response = MagicMock(status_code=400, text="Invalid token")
    mock_response.ok = False
    mock_requests_post.return_value = mock_response

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API was called
    mock_requests_post.assert_called_once()
    # 2. Response to client is still success
    assert response.status_code == 204
    assert response.get_body() is None

def test_logout_success_discord_network_error(mock_env_vars_present, mock_requests_post):
    """Test logout where Discord call raises RequestException, but client still gets success."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    mock_requests_post.side_effect = requests.exceptions.ConnectionError("Network down")

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API was called (attempted)
    mock_requests_post.assert_called_once()
    # 2. Response to client is still success
    assert response.status_code == 204
    assert response.get_body() is None

def test_logout_success_missing_env_vars(mock_env_vars_missing, mock_requests_post):
    """Test logout succeeds for client even if server env vars are missing."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN) # Token provided

    # Act
    response = logout_main(req)

    # Assert
    # 1. Discord API should NOT be called because env vars are missing
    mock_requests_post.assert_not_called()
    # 2. Response to client is still success
    assert response.status_code == 204
    assert response.get_body() is None
