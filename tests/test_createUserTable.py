import pytest
import os
from unittest.mock import patch, MagicMock
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from createUserTable import main as createUserTable_main

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
USER_ID = "testuser123"

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
    monkeypatch.setattr("createUserTable.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))
    mock_service_client.get_table_client.return_value = mock_table_client

    # Attach clients for inspection
    mock_service_client._mock_table_client = mock_table_client
    return mock_service_client

def create_mock_request(method="POST", user_id=USER_ID, origin="http://localhost:5173"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin}
    if user_id:
        headers['x-ms-client-principal-id'] = user_id
    return func.HttpRequest(
        method=method,
        url='/api/createusertable',
        headers=headers,
        params={},
        body=None
    )

# --- Test Cases ---

def test_create_table_success_new_table(mock_table_service_client):
    """Test successful creation of a new table."""
    # Arrange
    req = create_mock_request()
    mock_table_client = mock_table_service_client._mock_table_client
    # Simulate table not found during check, then successful creation
    mock_table_client.query_entities.side_effect = ResourceNotFoundError("Table not found")
    mock_table_service_client.create_table.return_value = None # Success

    # Act
    response = createUserTable_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID)
    mock_table_client.query_entities.assert_called_once_with("", results_per_page=1)
    mock_table_service_client.create_table.assert_called_once_with(USER_ID)
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert 'Table created successfully' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_create_table_success_already_exists(mock_table_service_client):
    """Test successful call when table already exists."""
    # Arrange
    req = create_mock_request()
    mock_table_client = mock_table_service_client._mock_table_client
    # Simulate table exists during check
    mock_table_client.query_entities.return_value = iter([{'PartitionKey': '1', 'RowKey': '1'}]) # Return an iterator with one item

    # Act
    response = createUserTable_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID)
    mock_table_client.query_entities.assert_called_once_with("", results_per_page=1)
    mock_table_service_client.create_table.assert_not_called() # Should not attempt creation
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert 'Table already exists' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_create_table_missing_user_id(mock_table_service_client):
    """Test request with missing user ID header."""
    # Arrange
    req = create_mock_request(user_id=None)

    # Act
    response = createUserTable_main(req)

    # Assert
    assert response.status_code == 400
    assert 'No user ID provided' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_create_table_missing_conn_string(monkeypatch, mock_table_service_client):
    """Test when connection string environment variable is missing."""
    # Arrange
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING")
    req = create_mock_request()

    # Act
    response = createUserTable_main(req)

    # Assert
    # The function currently logs the error but proceeds, failing at from_connection_string
    # Let's assert the expected failure point
    assert response.status_code == 500 # It will raise KeyError -> Exception
    assert 'Internal server error' in response.get_body(as_text=True)
    assert 'AZURE_STORAGE_CONNECTION_STRING' in response.get_body(as_text=True) # Check if error mentions the key
    assert 'Access-Control-Allow-Origin' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()


def test_create_table_options_request(mock_table_service_client):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS")

    # Act
    response = createUserTable_main(req)

    # Assert
    assert response.status_code == 200
    assert response.get_body() == b'' # No body for OPTIONS success
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()

def test_create_table_creation_fails(mock_table_service_client):
    """Test scenario where table creation fails for reasons other than existing."""
    # Arrange
    req = create_mock_request()
    mock_table_client = mock_table_service_client._mock_table_client
    # Simulate table not found during check
    mock_table_client.query_entities.side_effect = ResourceNotFoundError("Table not found")
    # Simulate creation failure
    mock_table_service_client.create_table.side_effect = HttpResponseError(message="Creation failed", status_code=500)

    # Act
    response = createUserTable_main(req)

    # Assert
    mock_table_service_client.create_table.assert_called_once_with(USER_ID)
    assert response.status_code == 500
    assert 'Internal server error' in response.get_body(as_text=True)
    assert 'HttpResponseError' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_create_table_check_fails(mock_table_service_client):
    """Test scenario where checking table existence fails."""
    # Arrange
    req = create_mock_request()
    mock_table_client = mock_table_service_client._mock_table_client
    # Simulate check failure (not ResourceNotFoundError)
    mock_table_client.query_entities.side_effect = HttpResponseError(message="Check failed", status_code=503)

    # Act
    response = createUserTable_main(req)

    # Assert
    mock_table_client.query_entities.assert_called_once_with("", results_per_page=1)
    mock_table_service_client.create_table.assert_not_called()
    assert response.status_code == 500
    assert 'Internal server error' in response.get_body(as_text=True)
    assert 'HttpResponseError' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
