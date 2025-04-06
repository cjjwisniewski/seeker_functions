import pytest
import os
import json
from unittest.mock import patch, MagicMock, ANY
import azure.functions as func
import requests

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from userinfo import main as userinfo_main, get_guild_member_url

# --- Constants ---
MOCK_REQUIRED_GUILD_ID = "test_guild_123"
MOCK_REQUIRED_ROLE_ID = "test_role_456"
MOCK_TOKEN = "valid.discord.token"
USER_INFO_URL = 'https://discord.com/api/v10/users/@me'
MEMBER_INFO_URL = get_guild_member_url(MOCK_REQUIRED_GUILD_ID)

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("REQUIRED_GUILD_ID", MOCK_REQUIRED_GUILD_ID)
    monkeypatch.setenv("REQUIRED_ROLE_ID", MOCK_REQUIRED_ROLE_ID)

@pytest.fixture
def mock_requests_get(monkeypatch):
    """Fixture to mock requests.get."""
    with patch('userinfo.requests.get') as mock_get:
        yield mock_get

def create_mock_request(token=MOCK_TOKEN):
    """Helper to create a mock HttpRequest."""
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return func.HttpRequest(
        method='GET',
        url='/api/userinfo',
        headers=headers,
        params={},
        body=None
    )

# --- Test Cases ---

def test_userinfo_success(mock_requests_get):
    """Test successful retrieval of user info with required role."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    user_data = {'id': 'user1', 'username': 'TestUser', 'avatar': 'avatar_hash'}
    member_data = {'roles': [MOCK_REQUIRED_ROLE_ID, 'other_role']}

    mock_user_response = MagicMock(status_code=200, ok=True)
    mock_user_response.json.return_value = user_data
    mock_member_response = MagicMock(status_code=200, ok=True)
    mock_member_response.json.return_value = member_data

    mock_requests_get.side_effect = [mock_user_response, mock_member_response]

    # Act
    response = userinfo_main(req)

    # Assert
    # 1. Check API calls
    expected_headers = {'Authorization': f'Bearer {MOCK_TOKEN}'}
    mock_requests_get.assert_any_call(USER_INFO_URL, headers=expected_headers)
    mock_requests_get.assert_any_call(MEMBER_INFO_URL, headers=expected_headers)
    assert mock_requests_get.call_count == 2

    # 2. Check response
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['id'] == 'user1'
    assert body['username'] == 'TestUser'
    assert body['avatar'] == 'avatar_hash'
    assert MOCK_REQUIRED_ROLE_ID in body['roles']
    assert 'other_role' in body['roles']

def test_userinfo_missing_token(mock_requests_get):
    """Test request with missing Authorization header."""
    # Arrange
    req = create_mock_request(token=None)

    # Act
    response = userinfo_main(req)

    # Assert
    assert response.status_code == 401
    assert b"Unauthorized: Missing token" in response.get_body()
    mock_requests_get.assert_not_called()

def test_userinfo_malformed_token(mock_requests_get):
    """Test request with malformed Authorization header."""
    # Arrange
    req = create_mock_request(token=None)
    req.headers['Authorization'] = 'BearerTokenNoSpace'

    # Act
    response = userinfo_main(req)

    # Assert
    assert response.status_code == 401
    assert b"Unauthorized: Missing token" in response.get_body() # Falls into the 'not access_token' block
    mock_requests_get.assert_not_called()

def test_userinfo_invalid_token_discord_401(mock_requests_get):
    """Test when Discord returns 401 for the user info request."""
    # Arrange
    req = create_mock_request(token="invalid.token")
    mock_user_response = MagicMock(status_code=401, ok=False, text="Invalid token response")
    mock_requests_get.return_value = mock_user_response

    # Act
    response = userinfo_main(req)

    # Assert
    mock_requests_get.assert_called_once_with(USER_INFO_URL, headers={'Authorization': 'Bearer invalid.token'})
    assert response.status_code == 401
    assert b"Unauthorized: Discord API returned 401" in response.get_body()
    assert b"Invalid token response" in response.get_body()

def test_userinfo_discord_user_api_error(mock_requests_get):
    """Test when Discord returns a non-401 error for user info."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    mock_user_response = MagicMock(status_code=500, ok=False, text="Discord server error")
    mock_requests_get.return_value = mock_user_response

    # Act
    response = userinfo_main(req)

    # Assert
    mock_requests_get.assert_called_once_with(USER_INFO_URL, headers={'Authorization': f'Bearer {MOCK_TOKEN}'})
    assert response.status_code == 502 # Should forward as 502 Bad Gateway
    assert b"Discord API error fetching user info" in response.get_body()
    assert b"Discord server error" in response.get_body()

def test_userinfo_user_not_in_guild(mock_requests_get):
    """Test when user is not in the required guild (member info returns 404)."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    user_data = {'id': 'user1', 'username': 'TestUser', 'avatar': 'avatar_hash'}

    mock_user_response = MagicMock(status_code=200, ok=True)
    mock_user_response.json.return_value = user_data
    mock_member_response = MagicMock(status_code=404, ok=False, text="Not Found") # Member not found

    mock_requests_get.side_effect = [mock_user_response, mock_member_response]

    # Act
    response = userinfo_main(req)

    # Assert
    # 1. API calls made
    mock_requests_get.assert_any_call(USER_INFO_URL, headers=ANY)
    mock_requests_get.assert_any_call(MEMBER_INFO_URL, headers=ANY)
    # 2. Response should be 403 Forbidden because role check fails (roles is empty)
    assert response.status_code == 403
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['error'] == 'forbidden'
    assert 'required role' in body['message']

def test_userinfo_missing_required_role(mock_requests_get):
    """Test when user is in guild but lacks the required role."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    user_data = {'id': 'user1', 'username': 'TestUser', 'avatar': 'avatar_hash'}
    member_data = {'roles': ['some_other_role', 'another_role']} # Missing required role

    mock_user_response = MagicMock(status_code=200, ok=True)
    mock_user_response.json.return_value = user_data
    mock_member_response = MagicMock(status_code=200, ok=True)
    mock_member_response.json.return_value = member_data

    mock_requests_get.side_effect = [mock_user_response, mock_member_response]

    # Act
    response = userinfo_main(req)

    # Assert
    assert response.status_code == 403
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['error'] == 'forbidden'
    assert 'required role' in body['message']

def test_userinfo_missing_env_vars(monkeypatch, mock_requests_get):
    """Test when required environment variables are missing."""
    # Arrange
    monkeypatch.delenv("REQUIRED_GUILD_ID") # Remove one
    req = create_mock_request(token=MOCK_TOKEN)

    # Act
    response = userinfo_main(req)

    # Assert
    assert response.status_code == 500
    assert b"Server configuration error" in response.get_body()
    mock_requests_get.assert_not_called() # Should fail before API calls

def test_userinfo_discord_member_api_error(mock_requests_get):
    """Test when Discord member info call fails with non-404/403 error."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    user_data = {'id': 'user1', 'username': 'TestUser', 'avatar': 'avatar_hash'}

    mock_user_response = MagicMock(status_code=200, ok=True)
    mock_user_response.json.return_value = user_data
    mock_member_response = MagicMock(status_code=503, ok=False, text="Service Unavailable") # Member info error

    mock_requests_get.side_effect = [mock_user_response, mock_member_response]

    # Act
    response = userinfo_main(req)

    # Assert
    # Should still fail with 403 because roles list is empty
    assert response.status_code == 403
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['error'] == 'forbidden'
    assert 'required role' in body['message']

def test_userinfo_network_error(mock_requests_get):
    """Test handling of requests.exceptions.RequestException."""
    # Arrange
    req = create_mock_request(token=MOCK_TOKEN)
    error_message = "Could not connect to Discord"
    mock_requests_get.side_effect = requests.exceptions.ConnectionError(error_message)

    # Act
    response = userinfo_main(req)

    # Assert
    mock_requests_get.assert_called_once() # Failed on the first call
    assert response.status_code == 502 # Default for RequestException without response
    assert b"RequestException during Discord API call" in response.get_body()
    assert error_message.encode() in response.get_body()
