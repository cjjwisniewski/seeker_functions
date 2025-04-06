import pytest
import os
import json
from unittest.mock import patch, MagicMock, ANY
import azure.functions as func
from azure.data.tables import TableServiceClient, TableItem, TableEntity
from azure.core.exceptions import HttpResponseError

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from getUserTables import main as getUserTables_main

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", MOCK_CONN_STR)

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)
    mock_table_clients = {} # Store mock clients per table name

    def get_client_side_effect(table_name):
        if table_name not in mock_table_clients:
            mock_client = MagicMock() # Mock TableClient spec
            mock_client.table_name = table_name
            # Default behavior: empty list for item count
            mock_client.list_entities.return_value = iter([])
            mock_table_clients[table_name] = mock_client
        return mock_table_clients[table_name]

    monkeypatch.setattr("getUserTables.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))
    mock_service_client.get_table_client.side_effect = get_client_side_effect

    # Default behavior for list_tables
    mock_service_client.list_tables.return_value = iter([])

    # Attach clients dict for inspection/modification in tests
    mock_service_client._mock_table_clients = mock_table_clients
    return mock_service_client

def create_mock_request(method="GET", origin="http://localhost:5173"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin}
    # No auth header needed for this function based on current code
    return func.HttpRequest(
        method=method,
        url='/api/getusertables',
        headers=headers,
        params={},
        body=None
    )

# --- Test Cases ---

def test_get_user_tables_success_multiple(mock_table_service_client):
    """Test successfully retrieving multiple user tables with item counts."""
    # Arrange
    req = create_mock_request()
    # Simulate list_tables response
    table_list = [
        TableItem({'name': 'user123'}),
        TableItem({'name': 'user456'}),
        TableItem({'name': 'systemTable'}), # Should be ignored
        TableItem({'name': 'user789'}),
    ]
    mock_table_service_client.list_tables.return_value = iter(table_list)

    # Simulate list_entities for item counts
    mock_client_123 = mock_table_service_client.get_table_client('user123')
    mock_client_123.list_entities.return_value = iter([TableEntity(), TableEntity()]) # 2 items
    mock_client_456 = mock_table_service_client.get_table_client('user456')
    mock_client_456.list_entities.return_value = iter([]) # 0 items
    mock_client_789 = mock_table_service_client.get_table_client('user789')
    mock_client_789.list_entities.return_value = iter([TableEntity()] * 5) # 5 items

    # Act
    response = getUserTables_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.list_tables.assert_called_once()
    # Check get_table_client calls only for user tables
    assert mock_table_service_client.get_table_client.call_count == 3
    mock_table_service_client.get_table_client.assert_any_call('user123')
    mock_table_service_client.get_table_client.assert_any_call('user456')
    mock_table_service_client.get_table_client.assert_any_call('user789')
    # Check list_entities calls for counts
    mock_client_123.list_entities.assert_called_once()
    mock_client_456.list_entities.assert_called_once()
    mock_client_789.list_entities.assert_called_once()

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert isinstance(body, list)
    assert len(body) == 3

    expected_data = [
        {'userId': 'user123', 'itemCount': 2},
        {'userId': 'user456', 'itemCount': 0},
        {'userId': 'user789', 'itemCount': 5},
    ]
    # Sort both lists by userId to ensure order doesn't matter
    assert sorted(body, key=lambda x: x['userId']) == sorted(expected_data, key=lambda x: x['userId'])

    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_user_tables_success_none_found(mock_table_service_client):
    """Test successfully returning empty list when no user tables exist."""
    # Arrange
    req = create_mock_request()
    # Simulate list_tables response with no user tables
    table_list = [
        TableItem({'name': 'systemTable'}),
        TableItem({'name': 'anotherTable'}),
    ]
    mock_table_service_client.list_tables.return_value = iter(table_list)

    # Act
    response = getUserTables_main(req)

    # Assert
    mock_table_service_client.list_tables.assert_called_once()
    # No table clients should be requested
    mock_table_service_client.get_table_client.assert_not_called()

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body == []
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_user_tables_list_tables_error(mock_table_service_client):
    """Test handling of error when listing tables."""
    # Arrange
    req = create_mock_request()
    mock_table_service_client.list_tables.side_effect = HttpResponseError("Permission denied", status_code=403)

    # Act
    response = getUserTables_main(req)

    # Assert
    mock_table_service_client.list_tables.assert_called_once()
    assert response.status_code == 500 # Caught by generic Exception
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['message'] == "Internal server error"
    assert "Permission denied" in body['error']
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_user_tables_list_entities_error(mock_table_service_client):
    """Test handling of error when counting items in one table."""
    # Arrange
    req = create_mock_request()
    table_list = [
        TableItem({'name': 'user123'}), # This one will fail count
        TableItem({'name': 'user456'}), # This one will succeed
    ]
    mock_table_service_client.list_tables.return_value = iter(table_list)

    # Simulate list_entities failure for user123
    mock_client_123 = mock_table_service_client.get_table_client('user123')
    mock_client_123.list_entities.side_effect = HttpResponseError("Timeout", status_code=504)
    # Simulate success for user456
    mock_client_456 = mock_table_service_client.get_table_client('user456')
    mock_client_456.list_entities.return_value = iter([TableEntity()]) # 1 item

    # Act
    response = getUserTables_main(req)

    # Assert
    # The loop continues after error, but the overall function fails at the end
    mock_client_123.list_entities.assert_called_once()
    mock_client_456.list_entities.assert_called_once()

    assert response.status_code == 500 # Caught by generic Exception
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body['message'] == "Internal server error"
    assert "Timeout" in body['error'] # Error from the failing call
    assert 'Access-Control-Allow-Origin' in response.headers


def test_get_user_tables_options_request(mock_table_service_client):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS")

    # Act
    response = getUserTables_main(req)

    # Assert
    assert response.status_code == 200
    assert response.get_body() == b''
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers
    mock_table_service_client.from_connection_string.assert_not_called()
