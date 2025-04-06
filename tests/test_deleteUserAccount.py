import pytest
import os
import json
from unittest.mock import patch, MagicMock
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deleteUserAccount import main as deleteUserAccount_main, is_admin

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
USER_ID_SELF = "selfdeleter123"
USER_ID_TARGET = "targetuser456"
ADMIN_USER_ID = "adminuser789"
NON_ADMIN_USER_ID = "nonadmin000"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", MOCK_CONN_STR)
    monkeypatch.setenv("ADMIN_USER_IDS", f"{ADMIN_USER_ID}, otheradmin") # Set admin IDs

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)

    # Link mocks
    monkeypatch.setattr("deleteUserAccount.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))

    return mock_service_client

def create_mock_request(method="DELETE", authenticated_user_id=USER_ID_SELF, body=None, origin="http://localhost:5173", content_type="application/json"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin}
    if content_type:
        headers['Content-Type'] = content_type
    if authenticated_user_id:
        headers['x-ms-client-principal-id'] = authenticated_user_id

    body_bytes = json.dumps(body).encode('utf-8') if body is not None else b''

    mock_req = MagicMock(spec=func.HttpRequest)
    mock_req.method = method
    mock_req.url = '/api/deleteuseraccount'
    mock_req.headers = headers
    mock_req.params = {}
    # Mock get_body to return bytes
    mock_req.get_body.return_value = body_bytes
    # Mock get_json based on body
    if body is not None and content_type and 'application/json' in content_type:
        try:
            mock_req.get_json.return_value = json.loads(body_bytes)
        except json.JSONDecodeError:
             mock_req.get_json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    else:
        # Simulate behavior when body is empty or not JSON
        mock_req.get_json.side_effect = json.JSONDecodeError("No JSON object could be decoded", "", 0)


    return mock_req

# --- Test Cases ---

def test_delete_self_success(mock_table_service_client):
    """Test successful self-deletion."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF, body=None) # No body for self-delete
    mock_table_service_client.delete_table.return_value = None # Success

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.delete_table.assert_called_once_with(table_name=USER_ID_SELF)
    assert response.status_code == 200 # Or 204 if changed
    assert response.mimetype == "application/json"
    assert f"'{USER_ID_SELF}' deleted successfully" in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_admin_success(mock_table_service_client):
    """Test successful deletion of another user by an admin."""
    # Arrange
    req_body = {'targetUserIdToDelete': USER_ID_TARGET}
    req = create_mock_request(authenticated_user_id=ADMIN_USER_ID, body=req_body)
    mock_table_service_client.delete_table.return_value = None # Success

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.delete_table.assert_called_once_with(table_name=USER_ID_TARGET)
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert f"'{USER_ID_TARGET}' deleted successfully" in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_non_admin_attempt_forbidden(mock_table_service_client):
    """Test non-admin attempting to delete another user."""
    # Arrange
    req_body = {'targetUserIdToDelete': USER_ID_TARGET}
    req = create_mock_request(authenticated_user_id=NON_ADMIN_USER_ID, body=req_body)

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    assert response.status_code == 403
    assert 'Forbidden' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()
    mock_table_service_client.delete_table.assert_not_called()

def test_delete_missing_auth_header(mock_table_service_client):
    """Test request missing the authentication header."""
    # Arrange
    req = create_mock_request(authenticated_user_id=None)

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    assert response.status_code == 401
    assert 'Unauthorized' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_table_not_found_idempotent(mock_table_service_client):
    """Test deletion when the target table doesn't exist (idempotency)."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_table_service_client.delete_table.side_effect = ResourceNotFoundError("Table not found")

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    mock_table_service_client.delete_table.assert_called_once_with(table_name=USER_ID_SELF)
    assert response.status_code == 200 # Success because desired state is achieved
    assert response.mimetype == "application/json"
    assert 'not found (already deleted or never existed)' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_table_fails_other_error(mock_table_service_client):
    """Test deletion failure due to an unexpected storage error."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_table_service_client.delete_table.side_effect = HttpResponseError(message="Storage timeout", status_code=503)

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    mock_table_service_client.delete_table.assert_called_once_with(table_name=USER_ID_SELF)
    assert response.status_code == 503
    assert response.mimetype == "application/json"
    assert 'Storage error during deletion' in response.get_body(as_text=True)
    assert 'Storage timeout' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_options_request(mock_table_service_client):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS", body=None)

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    assert response.status_code == 204 # Should be 204 for OPTIONS success
    assert response.get_body() == b''
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_wrong_method(mock_table_service_client):
    """Test request with an incorrect HTTP method (e.g., GET)."""
    # Arrange
    req = create_mock_request(method="GET")

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    assert response.status_code == 405
    assert 'Method Not Allowed' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_admin_invalid_json(mock_table_service_client):
    """Test admin request with invalid JSON body."""
    # Arrange
    # Create request with non-JSON body but JSON content type
    req = create_mock_request(authenticated_user_id=ADMIN_USER_ID, body=None, content_type="application/json")
    req.get_body.return_value = b'{"targetUserIdToDelete": "target",,}' # Invalid JSON bytes
    req.get_json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    assert response.status_code == 400
    assert 'Bad Request: Invalid JSON format' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_admin_empty_target_id(mock_table_service_client):
    """Test admin request with empty 'targetUserIdToDelete' in body."""
    # Arrange
    req_body = {'targetUserIdToDelete': '  '} # Empty string after strip
    req = create_mock_request(authenticated_user_id=ADMIN_USER_ID, body=req_body)
    # Self-delete should proceed
    mock_table_service_client.delete_table.return_value = None

    # Act
    response = deleteUserAccount_main(req)

    # Assert
    # Since targetUserIdToDelete is empty, it falls back to self-delete for the admin
    mock_table_service_client.delete_table.assert_called_once_with(table_name=ADMIN_USER_ID)
    assert response.status_code == 200
    assert f"'{ADMIN_USER_ID}' deleted successfully" in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

# --- Test is_admin helper ---
@pytest.mark.parametrize("user_id, expected", [
    (ADMIN_USER_ID, True),
    ("otheradmin", True),
    (NON_ADMIN_USER_ID, False),
    (" selfdeleter123 ", False),
    ("", False),
    (None, False),
])
def test_is_admin_logic(mock_env_vars, user_id, expected):
    """Verify the is_admin helper function."""
    assert is_admin(user_id) == expected
