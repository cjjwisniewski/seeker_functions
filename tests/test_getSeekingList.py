import pytest
import os
import json
from unittest.mock import patch, MagicMock, ANY
import azure.functions as func
from azure.data.tables import TableServiceClient, TableEntity
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from getSeekingList import main as getSeekingList_main, is_admin

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
USER_ID_SELF = "selfuser123"
USER_ID_TARGET = "targetuser456"
ADMIN_USER_ID = "adminuser789"
NON_ADMIN_USER_ID = "nonadmin000"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", MOCK_CONN_STR)
    monkeypatch.setenv("ADMIN_USER_IDS", f"{ADMIN_USER_ID}, otheradmin")

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)
    mock_table_clients = {} # Store mock clients per table name

    def get_client_side_effect(table_name):
        if table_name not in mock_table_clients:
            mock_client = MagicMock(spec=TableServiceClient) # Mock TableClient spec
            mock_client.table_name = table_name
            # Default behavior: empty list
            mock_client.list_entities.return_value = iter([])
            mock_table_clients[table_name] = mock_client
        return mock_table_clients[table_name]

    monkeypatch.setattr("getSeekingList.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))
    mock_service_client.get_table_client.side_effect = get_client_side_effect

    # Attach clients dict for inspection/modification in tests
    mock_service_client._mock_table_clients = mock_table_clients
    return mock_service_client

def create_mock_request(method="GET", authenticated_user_id=USER_ID_SELF, params=None, origin="http://localhost:5173"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin}
    if authenticated_user_id:
        headers['x-ms-client-principal-id'] = authenticated_user_id

    return func.HttpRequest(
        method=method,
        url=f'/api/getseekinglist?{params if params else ""}',
        headers=headers,
        params=params or {},
        body=None
    )

def create_card_entity(pk, rk, name="Test Card", lang="en", finish="nonfoil", collector_num="001", stock=False, stock_str="False"):
    """Helper to create a TableEntity for a card."""
    return TableEntity({
        'PartitionKey': pk, 'RowKey': rk,
        'id': rk, # Use RowKey as id for simplicity in test setup
        'name': name, 'set_code': pk, 'collector_number': collector_num,
        'language': lang, 'finish': finish, 'image_uri': 'test.png',
        'cardtrader_stock': stock_str, # Store as string initially like in DB
        'tcgplayer_stock': 'unknown',
        'cardmarket_stock': 'unknown',
        'ebay_stock': 'unknown',
        # Add other fields if needed
    })

# --- Test Cases ---

def test_get_self_list_success(mock_table_service_client):
    """Test successfully getting the authenticated user's own list."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_user_client = mock_table_service_client.get_table_client(USER_ID_SELF)
    card1 = create_card_entity("SET1", "001_en_foil", name="Card 1", finish="foil", stock=True, stock_str="True")
    card2 = create_card_entity("SET2", "002_fr_nonfoil", name="Card 2", lang="fr", stock=False, stock_str="False")
    mock_user_client.list_entities.return_value = iter([card1, card2])

    # Act
    response = getSeekingList_main(req)

    # Assert
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID_SELF)
    mock_user_client.list_entities.assert_called_once()

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert "cards" in body
    assert len(body["cards"]) == 2

    # Check card data transformation (especially boolean conversion)
    assert body["cards"][0]["id"] == "001_en_foil"
    assert body["cards"][0]["name"] == "Card 1"
    assert body["cards"][0]["cardtrader_stock"] is True # Should be boolean True
    assert body["cards"][1]["id"] == "002_fr_nonfoil"
    assert body["cards"][1]["name"] == "Card 2"
    assert body["cards"][1]["cardtrader_stock"] is False # Should be boolean False

    assert 'Access-Control-Allow-Origin' in response.headers
    assert response.headers.get('Access-Control-Allow-Credentials') == 'true'

def test_get_admin_list_success(mock_table_service_client):
    """Test admin successfully getting another user's list."""
    # Arrange
    req = create_mock_request(authenticated_user_id=ADMIN_USER_ID, params={'targetUserId': USER_ID_TARGET})
    mock_target_client = mock_table_service_client.get_table_client(USER_ID_TARGET)
    card1 = create_card_entity("TGT", "111_en_nonfoil", name="Target Card")
    mock_target_client.list_entities.return_value = iter([card1])

    # Act
    response = getSeekingList_main(req)

    # Assert
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=USER_ID_TARGET)
    mock_target_client.list_entities.assert_called_once()

    assert response.status_code == 200
    body = json.loads(response.get_body(as_text=True))
    assert len(body["cards"]) == 1
    assert body["cards"][0]["name"] == "Target Card"
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_non_admin_list_forbidden(mock_table_service_client):
    """Test non-admin attempting to get another user's list."""
    # Arrange
    req = create_mock_request(authenticated_user_id=NON_ADMIN_USER_ID, params={'targetUserId': USER_ID_TARGET})

    # Act
    response = getSeekingList_main(req)

    # Assert
    assert response.status_code == 403
    assert 'Forbidden' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers
    # Ensure no table client was requested for the target
    assert USER_ID_TARGET not in mock_table_service_client._mock_table_clients

def test_get_list_missing_auth_header(mock_table_service_client):
    """Test request missing the authentication header."""
    # Arrange
    req = create_mock_request(authenticated_user_id=None)

    # Act
    response = getSeekingList_main(req)

    # Assert
    assert response.status_code == 401
    assert 'Unauthorized' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers # CORS headers still needed
    mock_table_service_client.from_connection_string.assert_not_called()

def test_get_list_table_not_found(mock_table_service_client):
    """Test when the user's table does not exist (ResourceNotFoundError)."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_user_client = mock_table_service_client.get_table_client(USER_ID_SELF)
    # Simulate table not found when listing entities
    mock_user_client.list_entities.side_effect = ResourceNotFoundError("Table not found")

    # Act
    response = getSeekingList_main(req)

    # Assert
    mock_user_client.list_entities.assert_called_once()
    assert response.status_code == 200 # Returns 200 with empty list
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body["cards"] == []
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_list_empty_table(mock_table_service_client):
    """Test when the user's table exists but is empty."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_user_client = mock_table_service_client.get_table_client(USER_ID_SELF)
    mock_user_client.list_entities.return_value = iter([]) # Empty iterator

    # Act
    response = getSeekingList_main(req)

    # Assert
    mock_user_client.list_entities.assert_called_once()
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))
    assert body["cards"] == []
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_list_storage_error(mock_table_service_client):
    """Test handling of Azure Storage errors during list_entities."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_user_client = mock_table_service_client.get_table_client(USER_ID_SELF)
    mock_user_client.list_entities.side_effect = HttpResponseError(message="Storage unavailable", status_code=503)

    # Act
    response = getSeekingList_main(req)

    # Assert
    mock_user_client.list_entities.assert_called_once()
    assert response.status_code == 503
    assert 'Internal server error accessing data' in response.get_body(as_text=True)
    assert 'Storage unavailable' in response.get_body(as_text=True)
    assert 'Access-Control-Allow-Origin' in response.headers

def test_get_list_options_request(mock_table_service_client):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS", authenticated_user_id=None) # No auth needed for OPTIONS

    # Act
    response = getSeekingList_main(req)

    # Assert
    assert response.status_code == 204 # Should be 204
    assert response.get_body() == b''
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers
    assert response.headers.get('Access-Control-Allow-Credentials') == 'true' # Should be present
    mock_table_service_client.from_connection_string.assert_not_called()

@pytest.mark.parametrize("stock_value_in, expected_out", [
    ("True", True),
    ("true", True),
    ("TRUE", True),
    ("False", False),
    ("false", False),
    ("FALSE", False),
    ("unknown", False), # unknown becomes False
    ("", False),        # empty string becomes False
    (None, False),      # None becomes False
    (True, True),       # Already boolean True
    (False, False),     # Already boolean False
    ("yes", False),     # Non-true string becomes False
    (1, False),         # Non-string becomes False
])
def test_boolean_conversion(mock_table_service_client, stock_value_in, expected_out):
    """Test the string to boolean conversion logic for stock fields."""
    # Arrange
    req = create_mock_request(authenticated_user_id=USER_ID_SELF)
    mock_user_client = mock_table_service_client.get_table_client(USER_ID_SELF)
    # Create entity with the specific input stock value
    entity = TableEntity({
        'PartitionKey': 'TST', 'RowKey': 'bool_test', 'id': 'bool_test',
        'name': 'Bool Test', 'set_code': 'TST', 'collector_number': '001',
        'language': 'en', 'finish': 'nonfoil', 'image_uri': 'test.png',
        'cardtrader_stock': stock_value_in, # Use the input value here
        'tcgplayer_stock': stock_value_in,
        'cardmarket_stock': stock_value_in,
        'ebay_stock': stock_value_in,
    })
    mock_user_client.list_entities.return_value = iter([entity])

    # Act
    response = getSeekingList_main(req)

    # Assert
    assert response.status_code == 200
    body = json.loads(response.get_body(as_text=True))
    assert len(body["cards"]) == 1
    card = body["cards"][0]
    assert card['cardtrader_stock'] is expected_out
    assert card['tcgplayer_stock'] is expected_out
    assert card['cardmarket_stock'] is expected_out
    assert card['ebay_stock'] is expected_out
