import pytest
import os
from unittest.mock import patch, MagicMock, call
import azure.functions as func
from azure.data.tables import TableServiceClient, TableEntity
from azure.core.exceptions import HttpResponseError
from datetime import datetime
import requests

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from getCardtraderSets import main as getCardtraderSets_main

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
MOCK_API_KEY = "test-api-key"
SETS_TABLE_NAME = "setscardtrader"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", MOCK_CONN_STR)
    monkeypatch.setenv("CARDTRADER_API_KEY", MOCK_API_KEY)

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)
    mock_table_client = MagicMock()

    # Link mocks
    monkeypatch.setattr("getCardtraderSets.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))
    mock_service_client.get_table_client.return_value = mock_table_client

    # Simulate table exists by default for create_table check
    mock_service_client.create_table.side_effect = HttpResponseError(message="TableAlreadyExists", status_code=409)

    # Attach clients for inspection
    mock_service_client._mock_table_client = mock_table_client
    return mock_service_client

@pytest.fixture
def mock_requests_get(monkeypatch):
    """Mock requests.get."""
    with patch('getCardtraderSets.requests.get') as mock_get:
        yield mock_get

@pytest.fixture
def mock_timer():
    """Mock TimerRequest."""
    timer_mock = MagicMock(spec=func.TimerRequest)
    timer_mock.past_due = False
    return timer_mock

@pytest.fixture
def mock_datetime_utcnow(monkeypatch):
    """Mock datetime.utcnow to return a fixed time."""
    fixed_time = datetime(2025, 4, 6, 11, 0, 0)
    mock_dt = MagicMock(spec=datetime)
    mock_dt.utcnow.return_value = fixed_time
    # Allow other datetime methods to work if needed
    mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
    monkeypatch.setattr("getCardtraderSets.datetime", mock_dt)
    return fixed_time

# --- Helper Data ---
def create_set_data(id, code, name, game_id=1): # Default to MTG
    return {'id': id, 'code': code, 'name': name, 'game_id': game_id}

# --- Test Cases ---

def test_get_sets_success(mock_table_service_client, mock_requests_get, mock_timer, mock_datetime_utcnow):
    """Test successful fetching and updating of MTG sets."""
    # Arrange
    mock_table_client = mock_table_service_client._mock_table_client
    set_data = [
        create_set_data(1, 'SET1', 'Set One'),
        create_set_data(2, 'SET2', 'Set Two'),
        create_set_data(3, 'OTH', 'Other Game Set', game_id=2), # Non-MTG
        create_set_data(4, 'SET3', 'Set Three'),
    ]
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = set_data
    mock_requests_get.return_value = mock_response

    # Act
    getCardtraderSets_main(mock_timer)

    # Assert
    # 1. API called correctly
    mock_requests_get.assert_called_once_with(
        "https://api.cardtrader.com/api/v2/expansions",
        headers={"Authorization": f"Bearer {MOCK_API_KEY}", "Accept": "application/json"}
    )
    # 2. Table service initialized and table checked/created
    mock_table_service_client.from_connection_string.assert_called_once_with(MOCK_CONN_STR)
    mock_table_service_client.create_table.assert_called_once_with(table_name=SETS_TABLE_NAME)
    mock_table_service_client.get_table_client.assert_called_once_with(table_name=SETS_TABLE_NAME)
    # 3. Correct number of upserts (only MTG sets)
    assert mock_table_client.upsert_entity.call_count == 3
    # 4. Check one of the upserted entities
    expected_entity_set1 = TableEntity(
        PartitionKey="mtg", RowKey="set1",
        id=1, name='Set One', code='SET1',
        last_updated=mock_datetime_utcnow.isoformat()
    )
    # Use ANY for the entity object comparison if direct comparison is tricky
    # mock_table_client.upsert_entity.assert_any_call(entity=ANY)
    # Or check specific calls more carefully:
    calls = mock_table_client.upsert_entity.call_args_list
    assert calls[0][1]['entity']['RowKey'] == 'set1'
    assert calls[0][1]['entity']['id'] == 1
    assert calls[1][1]['entity']['RowKey'] == 'set2'
    assert calls[1][1]['entity']['id'] == 2
    assert calls[2][1]['entity']['RowKey'] == 'set3'
    assert calls[2][1]['entity']['id'] == 4
    assert calls[0][1]['entity']['last_updated'] == mock_datetime_utcnow.isoformat()


def test_get_sets_api_error(mock_table_service_client, mock_requests_get, mock_timer):
    """Test handling of Cardtrader API error."""
    # Arrange
    mock_table_client = mock_table_service_client._mock_table_client
    mock_response = MagicMock(status_code=500, text="Server Error")
    mock_requests_get.return_value = mock_response

    # Act & Assert
    with pytest.raises(Exception, match="API request failed: Server Error"):
        getCardtraderSets_main(mock_timer)

    # Check that no upserts happened
    mock_table_client.upsert_entity.assert_not_called()

def test_get_sets_no_sets_returned(mock_table_service_client, mock_requests_get, mock_timer):
    """Test handling when the API returns an empty list."""
    # Arrange
    mock_table_client = mock_table_service_client._mock_table_client
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [] # Empty list
    mock_requests_get.return_value = mock_response

    # Act
    getCardtraderSets_main(mock_timer)

    # Assert
    # 1. API called
    mock_requests_get.assert_called_once()
    # 2. No upserts happened
    mock_table_client.upsert_entity.assert_not_called()

def test_get_sets_table_creation_success(mock_table_service_client, mock_requests_get, mock_timer):
    """Test scenario where the table needs to be created."""
    # Arrange
    # Simulate table *not* existing initially for create_table check
    mock_table_service_client.create_table.side_effect = None # Reset side effect to success
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [] # No sets needed for this test
    mock_requests_get.return_value = mock_response

    # Act
    getCardtraderSets_main(mock_timer)

    # Assert
    # Check that create_table was called and succeeded (no exception raised)
    mock_table_service_client.create_table.assert_called_once_with(table_name=SETS_TABLE_NAME)

def test_get_sets_processing_error_continues(mock_table_service_client, mock_requests_get, mock_timer, mock_datetime_utcnow):
    """Test that processing continues if one set causes an error during upsert."""
    # Arrange
    mock_table_client = mock_table_service_client._mock_table_client
    set_data = [
        create_set_data(1, 'GOOD1', 'Good One'),
        create_set_data(2, 'BAD', 'Bad One'), # This one will fail
        create_set_data(3, 'GOOD2', 'Good Two'),
    ]
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = set_data
    mock_requests_get.return_value = mock_response

    # Simulate upsert failure only for the 'bad' set
    def upsert_side_effect(*args, **kwargs):
        entity = kwargs.get('entity')
        if entity and entity.get('RowKey') == 'bad':
            raise Exception("Simulated upsert error")
        else:
            return None # Success for others

    mock_table_client.upsert_entity.side_effect = upsert_side_effect

    # Act
    getCardtraderSets_main(mock_timer) # Should not raise exception

    # Assert
    # 1. API called
    mock_requests_get.assert_called_once()
    # 2. Upsert called for all 3 MTG sets
    assert mock_table_client.upsert_entity.call_count == 3
    # 3. Check that the good ones were attempted (and implicitly succeeded based on side effect)
    calls = mock_table_client.upsert_entity.call_args_list
    assert calls[0][1]['entity']['RowKey'] == 'good1'
    assert calls[1][1]['entity']['RowKey'] == 'bad' # The one that failed
    assert calls[2][1]['entity']['RowKey'] == 'good2'
