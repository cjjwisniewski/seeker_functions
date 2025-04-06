import pytest
import os
from unittest.mock import patch, MagicMock
import azure.functions as func
from urllib.parse import urlparse, parse_qs, urlunparse, urljoin
import requests

# Add parent directory to path to import callback function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from callback import main as callback_main

# --- Constants ---
MOCK_FRONTEND_URL = "http://localhost:5173"
MOCK_CALLBACK_URL = "https://test-app.azurewebsites.net/api/callback"
MOCK_CLIENT_ID = "test_client_id"
MOCK_CLIENT_SECRET = "test_client_secret"
MOCK_REQUIRED_GUILD_ID = "test_guild_id"
MOCK_REQUIRED_ROLE_ID = "test_role_id"
MOCK_USER_TABLE_FUNC_URL = "https://test-app.azurewebsites.net/api/createusertable" # Example

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("DISCORD_CLIENT_ID", MOCK_CLIENT_ID)
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", MOCK_CLIENT_SECRET)
    monkeypatch.setenv("DISCORD_REDIRECT_URI", MOCK_CALLBACK_URL)
    monkeypatch.setenv("REQUIRED_GUILD_ID", MOCK_REQUIRED_GUILD_ID)
    monkeypatch.setenv("REQUIRED_ROLE_ID", MOCK_REQUIRED_ROLE_ID)
    monkeypatch.setenv("FRONTEND_URL", MOCK_FRONTEND_URL)
    monkeypatch.setenv("PUBLIC_USER_TABLE_FUNCTION_URL", MOCK_USER_TABLE_FUNC_URL)

@pytest.fixture
def mock_requests_post():
    """Fixture to mock requests.post."""
    with patch('callback.requests.post') as mock_post:
        yield mock_post

@pytest.fixture
def mock_requests_get():
    """Fixture to mock requests.get."""
    with patch('callback.requests.get') as mock_get:
        yield mock_get

def create_mock_request(params=None):
    """Helper to create a mock HttpRequest."""
    if params is None:
        params = {}
    return func.HttpRequest(
        method='GET',
        url=f'{MOCK_CALLBACK_URL}?{params}',
        params=params,
        headers={},
        body=None
    )

# --- Test Cases ---

def test_callback_success(mock_requests_post, mock_requests_get):
    """Test successful callback flow."""
    # Arrange
    state = "/original/path"
    code = "valid_auth_code"
    access_token = "valid_access_token"
    req = create_mock_request(params={'code': code, 'state': state})

    # Mock token exchange response
    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {'access_token': access_token, 'token_type': 'Bearer'}
    mock_requests_post.return_value = mock_token_response

    # Mock guilds response (user is in the required guild)
    mock_guilds_response = MagicMock()
    mock_guilds_response.status_code = 200
    mock_guilds_response.json.return_value = [{'id': MOCK_REQUIRED_GUILD_ID, 'name': 'Test Guild'}]
    mock_requests_get.side_effect = [mock_guilds_response] # Only guilds call needed now

    # Mock user table function response (optional, assuming it's called)
    mock_table_func_response = MagicMock()
    mock_table_func_response.status_code = 200
    # Need to adjust side_effect if more GET calls are made
    mock_requests_post.side_effect = [mock_token_response, mock_table_func_response]


    # Act
    response = callback_main(req)

    # Assert
    # 1. Token exchange call
    mock_requests_post.assert_any_call(
        'https://discord.com/api/v10/oauth2/token',
        data={
            'client_id': MOCK_CLIENT_ID,
            'client_secret': MOCK_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': MOCK_CALLBACK_URL,
            'scope': 'identify guilds guilds.members.read'
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    # 2. Guilds check call
    mock_requests_get.assert_called_once_with(
        'https://discord.com/api/v10/users/@me/guilds',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    # 3. User table function call (POST in this case)
    mock_requests_post.assert_any_call(
        MOCK_USER_TABLE_FUNC_URL,
        headers={'Authorization': f'Bearer {access_token}'}
    )

    # 4. Redirect to frontend with token in fragment
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    assert parsed_url.scheme == 'http'
    assert parsed_url.netloc == 'localhost:5173'
    assert parsed_url.path == state # Path should match state
    assert f"token={access_token}" in parsed_url.fragment
    assert f"state={state}" in parsed_url.fragment

def test_callback_missing_code(mock_requests_post, mock_requests_get):
    """Test callback when 'code' parameter is missing."""
    # Arrange
    state = "/some/state"
    req = create_mock_request(params={'state': state}) # No code

    # Act
    response = callback_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query = parse_qs(parsed_url.query)
    assert parsed_url.path == '/login'
    assert query['error'] == ['no_code']
    assert 'message' in query

    mock_requests_post.assert_not_called()
    mock_requests_get.assert_not_called()

def test_callback_missing_state(mock_requests_post, mock_requests_get):
    """Test callback when 'state' parameter is missing (should default)."""
    # Arrange
    code = "valid_auth_code"
    access_token = "valid_access_token"
    req = create_mock_request(params={'code': code}) # No state

    # Mock successful API calls
    mock_token_response = MagicMock(status_code=200, json=lambda: {'access_token': access_token})
    mock_guilds_response = MagicMock(status_code=200, json=lambda: [{'id': MOCK_REQUIRED_GUILD_ID}])
    mock_table_func_response = MagicMock(status_code=200)
    mock_requests_post.side_effect = [mock_token_response, mock_table_func_response]
    mock_requests_get.return_value = mock_guilds_response

    # Act
    response = callback_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    assert parsed_url.path == '/' # Default path
    assert f"token={access_token}" in parsed_url.fragment
    assert f"state=/" in parsed_url.fragment # State defaults to '/'

def test_callback_missing_env_vars(monkeypatch, mock_requests_post, mock_requests_get):
    """Test callback when environment variables are missing."""
    # Arrange
    monkeypatch.delenv("DISCORD_CLIENT_ID") # Remove one required var
    req = create_mock_request(params={'code': 'any_code', 'state': '/'})

    # Act
    response = callback_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query = parse_qs(parsed_url.query)
    assert parsed_url.path == '/login'
    assert query['error'] == ['config_error']
    assert 'message' in query

    mock_requests_post.assert_not_called()
    mock_requests_get.assert_not_called()

def test_callback_token_exchange_fails(mock_requests_post, mock_requests_get):
    """Test callback when Discord token exchange fails."""
    # Arrange
    state = "/original/path"
    code = "invalid_auth_code"
    req = create_mock_request(params={'code': code, 'state': state})

    # Mock token exchange failure
    mock_token_response = MagicMock(status_code=400, text="Invalid code")
    mock_token_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_token_response)
    mock_requests_post.return_value = mock_token_response

    # Act
    response = callback_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query = parse_qs(parsed_url.query)
    assert parsed_url.path == '/login'
    assert query['error'] == ['discord_api_error']
    assert 'Communication error' in query['message'][0]

    mock_requests_get.assert_not_called()

def test_callback_user_not_in_guild(mock_requests_post, mock_requests_get):
    """Test callback when user is not in the required guild."""
    # Arrange
    state = "/original/path"
    code = "valid_auth_code"
    access_token = "valid_access_token"
    req = create_mock_request(params={'code': code, 'state': state})

    # Mock token exchange success
    mock_token_response = MagicMock(status_code=200, json=lambda: {'access_token': access_token})
    mock_requests_post.return_value = mock_token_response

    # Mock guilds response (user is NOT in the required guild)
    mock_guilds_response = MagicMock()
    mock_guilds_response.status_code = 200
    mock_guilds_response.json.return_value = [{'id': 'another_guild_id', 'name': 'Another Guild'}]
    mock_requests_get.return_value = mock_guilds_response

    # Act
    response = callback_main(req)

    # Assert
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    query = parse_qs(parsed_url.query)
    assert parsed_url.path == '/login'
    assert query['error'] == ['server_required']
    assert 'required Discord server' in query['message'][0]

def test_callback_user_table_func_fails(mock_requests_post, mock_requests_get):
    """Test callback when the optional user table function call fails."""
    # Arrange
    state = "/original/path"
    code = "valid_auth_code"
    access_token = "valid_access_token"
    req = create_mock_request(params={'code': code, 'state': state})

    # Mock token exchange success
    mock_token_response = MagicMock(status_code=200, json=lambda: {'access_token': access_token})

    # Mock guilds response success
    mock_guilds_response = MagicMock(status_code=200, json=lambda: [{'id': MOCK_REQUIRED_GUILD_ID}])
    mock_requests_get.return_value = mock_guilds_response

    # Mock user table function failure
    mock_table_func_response = MagicMock(status_code=500, text="Internal Server Error")
    mock_table_func_response.ok = False # Ensure 'ok' is False
    mock_requests_post.side_effect = [mock_token_response, mock_table_func_response]

    # Act
    response = callback_main(req)

    # Assert
    # Should still succeed and redirect with token, failure is only logged
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    assert parsed_url.path == state
    assert f"token={access_token}" in parsed_url.fragment
    assert f"state={state}" in parsed_url.fragment

    # Check that the table function was called
    mock_requests_post.assert_any_call(
        MOCK_USER_TABLE_FUNC_URL,
        headers={'Authorization': f'Bearer {access_token}'}
    )

def test_callback_user_table_func_disabled(mock_env_vars, monkeypatch, mock_requests_post, mock_requests_get):
    """Test callback when PUBLIC_USER_TABLE_FUNCTION_URL is not set."""
    # Arrange
    monkeypatch.delenv("PUBLIC_USER_TABLE_FUNCTION_URL") # Disable the optional call
    state = "/original/path"
    code = "valid_auth_code"
    access_token = "valid_access_token"
    req = create_mock_request(params={'code': code, 'state': state})

    # Mock token exchange success
    mock_token_response = MagicMock(status_code=200, json=lambda: {'access_token': access_token})
    mock_requests_post.return_value = mock_token_response # Only token exchange POST

    # Mock guilds response success
    mock_guilds_response = MagicMock(status_code=200, json=lambda: [{'id': MOCK_REQUIRED_GUILD_ID}])
    mock_requests_get.return_value = mock_guilds_response

    # Act
    response = callback_main(req)

    # Assert
    # Should succeed and redirect with token
    assert response.status_code == 302
    redirect_url = response.headers['Location']
    parsed_url = urlparse(redirect_url)
    assert parsed_url.path == state
    assert f"token={access_token}" in parsed_url.fragment
    assert f"state={state}" in parsed_url.fragment

    # Check that the table function was NOT called (only token exchange POST)
    mock_requests_post.assert_called_once()
    assert mock_requests_post.call_args[0][0] == 'https://discord.com/api/v10/oauth2/token'
