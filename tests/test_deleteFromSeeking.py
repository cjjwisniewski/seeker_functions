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
from deleteFromSeeking import main as deleteFromSeeking_main

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
USER_ID = "testuser123"
PARTITION_KEY = "test_set"
ROW_KEY = "123_en_foil"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", MOCK_CONN_STR)

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)
    mock_table_client = MagicMock()

    # Link mocks
    monkeypatch.setattr("deleteFromSeeking.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))
    mock_service_client.get_table_client.return_value = mock_table_client

    # Attach clients for inspection
    mock_service_client._mock_table_client = mock_table_client
    return mock_service_client

def create_mock_request(method="DELETE", user_id=USER_ID, body=None, origin="http://localhost:5173"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin, 'Content-Type': 'application/json'}
    if user_id:
        headers['x-ms-client-principal-id'] = user_id

    body_bytes = json.dumps(body).encode('utf-8') if body else None

    mock_req = MagicMock(spec=func.HttpRequest)
    mock_req.method = method
    mock_req.url = '/api/deletefromseeking'
    mock_req.headers = headers
    mock_req.params = {}
    # Mock get_body to return bytes
    mock_req.get_body.return_value = body_bytes
    # Mock get_json to return parsed dict or raise ValueError
    if body:
        mock_req.get_json.return_value = body
    else:
        # Simulate behavior when body is empty or invalid JSON
        mock_req.get_json.side_effect = ValueError("No JSON data or invalid format")

    return mock_req


# --- Test Cases ---

def test_delete_success(mock_table_service_client):
    """Test successful deletion of an entity."""
    # Arrange
    req_body = {'partitionKey': PARTITION_KEY, 'rowKey': ROW_KEY}
    req = create_mock_request(body=req_body)
    mock_table_client = mock_table_service_client._mock_table_client
    mock_table_client.delete_entity.return_value = None # Success

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID)
    mock_table_client.delete_entity.assert_called_once_with(partition_key=PARTITION_KEY, row_key=ROW_KEY)
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert 'Card deleted successfully' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_entity_not_found(mock_table_service_client):
    """Test deletion when the entity does not exist."""
    # Arrange
    req_body = {'partitionKey': PARTITION_KEY, 'rowKey': ROW_KEY}
    req = create_mock_request(body=req_body)
    mock_table_client = mock_table_service_client._mock_table_client
    mock_table_client.delete_entity.side_effect = ResourceNotFoundError("Entity not found")

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    mock_table_client.delete_entity.assert_called_once_with(partition_key=PARTITION_KEY, row_key=ROW_KEY)
    assert response.status_code == 404
    assert 'Entity not found' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_delete_missing_user_id(mock_table_service_client):
    """Test request with missing user ID header."""
    # Arrange
    req_body = {'partitionKey': PARTITION_KEY, 'rowKey': ROW_KEY}
    req = create_mock_request(user_id=None, body=req_body)

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    assert response.status_code == 400
    assert 'No user ID provided' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_missing_body(mock_table_service_client):
    """Test request with missing request body."""
    # Arrange
    req = create_mock_request(body=None) # No body

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    assert response.status_code == 400 # Caught by ValueError in get_json
    assert 'Invalid request body format' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_invalid_json_body(mock_table_service_client):
    """Test request with invalid JSON in the body."""
    # Arrange
    # Create a request where get_json raises ValueError
    req = create_mock_request(body={'partitionKey': PARTITION_KEY}) # Simulate getting body first
    req.get_json.side_effect = ValueError("Invalid JSON") # Then get_json fails

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    assert response.status_code == 400
    assert 'Invalid request body format' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()


def test_delete_missing_keys_in_body(mock_table_service_client):
    """Test request with missing partitionKey or rowKey in the body."""
    # Arrange
    req_body_missing_row = {'partitionKey': PARTITION_KEY}
    req_missing_row = create_mock_request(body=req_body_missing_row)

    req_body_missing_part = {'rowKey': ROW_KEY}
    req_missing_part = create_mock_request(body=req_body_missing_part)

    # Act
    response_missing_row = deleteFromSeeking_main(req_missing_row)
    response_missing_part = deleteFromSeeking_main(req_missing_part)

    # Assert
    assert response_missing_row.status_code == 400
    assert 'Missing required fields' in response_missing_row.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response_missing_row.headers

    assert response_missing_part.status_code == 400
    assert 'Missing required fields' in response_missing_part.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response_missing_part.headers

    mock_table_service_client.from_connection_string.assert_not_called()


def test_delete_options_request(mock_table_service_client):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS", body=None)

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    assert response.status_code == 200
    assert response.get_body() == b''
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_delete_table_client_fails(mock_table_service_client):
    """Test scenario where getting the table client fails."""
    # Arrange
    req_body = {'partitionKey': PARTITION_KEY, 'rowKey': ROW_KEY}
    req = create_mock_request(body=req_body)
    # Simulate failure when getting table client
    mock_table_service_client.get_table_client.side_effect = HttpResponseError("Cannot connect")

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    assert response.status_code == 500
    assert 'Internal server error' in response.get_body(as_text=True)
    assert 'HttpResponseError' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID)
    # delete_entity should not be called
    mock_table_client = mock_table_service_client._mock_table_client
    mock_table_client.delete_entity.assert_not_called()

def test_delete_entity_fails_other_error(mock_table_service_client):
    """Test scenario where delete_entity fails with an error other than NotFound."""
    # Arrange
    req_body = {'partitionKey': PARTITION_KEY, 'rowKey': ROW_KEY}
    req = create_mock_request(body=req_body)
    mock_table_client = mock_table_service_client._mock_table_client
    mock_table_client.delete_entity.side_effect = HttpResponseError(message="Forbidden", status_code=403)

    # Act
    response = deleteFromSeeking_main(req)

    # Assert
    mock_table_client.delete_entity.assert_called_once_with(partition_key=PARTITION_KEY, row_key=ROW_KEY)
    assert response.status_code == 500 # Caught by generic Exception handler
    assert 'Internal server error' in response.get_body(as_text=True)
    assert 'HttpResponseError' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
