import pytest
import os
from unittest.mock import patch, MagicMock, call
import azure.functions as func
from azure.data.tables import TableServiceClient, TableEntity
from azure.core.exceptions import HttpResponseError
from datetime import datetime, timezone, timedelta
import requests

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from getCardtraderBlueprints import main as getCardtraderBlueprints_main, get_next_set, process_blueprint

# --- Constants ---
MOCK_CONN_STR = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
MOCK_API_KEY = "test-api-key"
SETS_TABLE = "setscardtrader"
BLUEPRINTS_TABLE = "blueprintscardtrader"

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
    mock_sets_client = MagicMock()
    mock_blueprints_client = MagicMock()

    # Link mocks
    monkeypatch.setattr("getCardtraderBlueprints.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))

    def get_client_side_effect(table_name):
        if table_name == SETS_TABLE:
            return mock_sets_client
        elif table_name == BLUEPRINTS_TABLE:
            return mock_blueprints_client
        else:
            raise ValueError(f"Unexpected table name: {table_name}")

    mock_service_client.get_table_client.side_effect = get_client_side_effect
    # Simulate table exists by default
    mock_service_client.create_table.side_effect = HttpResponseError(message="TableAlreadyExists", status_code=409)


    # Attach clients for inspection
    mock_service_client._mock_sets_client = mock_sets_client
    mock_service_client._mock_blueprints_client = mock_blueprints_client
    return mock_service_client

@pytest.fixture
def mock_requests_session(monkeypatch):
    """Mock requests.Session and its get method."""
    mock_session_instance = MagicMock()
    # Default: 200 OK, empty list
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = []
    mock_session_instance.get.return_value = mock_response

    # Patch requests.Session constructor and mount
    mock_session_class = MagicMock(return_value=mock_session_instance)
    monkeypatch.setattr("getCardtraderBlueprints.requests.Session", mock_session_class)
    # Mock the retry adapter part if needed, but focus on session.get for now
    # monkeypatch.setattr("getCardtraderBlueprints.HTTPAdapter", MagicMock())
    # monkeypatch.setattr("getCardtraderBlueprints.Retry", MagicMock())

    return mock_session_instance

@pytest.fixture
def mock_timer():
    """Mock TimerRequest."""
    timer_mock = MagicMock(spec=func.TimerRequest)
    timer_mock.past_due = False
    return timer_mock

@pytest.fixture
def mock_datetime_now(monkeypatch):
    """Mock datetime.now to return a fixed UTC time."""
    fixed_time = datetime(2025, 4, 6, 10, 0, 0, tzinfo=timezone.utc)
    mock_dt = MagicMock(spec=datetime)
    mock_dt.now.return_value = fixed_time
    mock_dt.utcnow.return_value = fixed_time.replace(tzinfo=None) # utcnow is naive
    # Allow other datetime methods to work
    mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
    mock_dt.min = datetime.min

    monkeypatch.setattr("getCardtraderBlueprints.datetime", mock_dt)
    return fixed_time


# --- Helper Data ---
def create_set_entity(id, code, last_updated=None):
    entity = TableEntity({
        'PartitionKey': 'mtg', 'RowKey': code.lower(),
        'id': id, 'code': code, 'name': f"Set {code}"
    })
    if last_updated:
        entity['blueprints_last_updated'] = last_updated
    return entity

def create_blueprint_data(bp_id, name, set_code, collector_num='1', rarity='C', scryfall_id='sid', img='url', tcg_id='tid', cm_ids=None, langs=None, conds=None, foil=None):
    if cm_ids is None: cm_ids = []
    if langs is None: langs = ['en', 'de']
    if conds is None: conds = ['NM', 'LP']
    if foil is None: foil = [True, False]

    return {
        'id': bp_id,
        'name': name,
        'fixed_properties': {
            'collector_number': collector_num,
            'mtg_rarity': rarity,
        },
        'scryfall_id': scryfall_id,
        'image_url': img,
        'tcg_player_id': tcg_id,
        'card_market_ids': cm_ids,
        'editable_properties': [
            {'name': 'mtg_language', 'possible_values': langs},
            {'name': 'condition', 'possible_values': conds},
            {'name': 'mtg_foil', 'possible_values': foil},
        ]
    }

# --- Test Cases ---

def test_get_next_set_no_sets(mock_table_service_client):
    """Test get_next_set when there are no sets."""
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_sets_client.list_entities.return_value = []
    with pytest.raises(ValueError): # Expecting error as min() on empty sequence
         get_next_set(mock_sets_client)

def test_get_next_set_new_set_exists(mock_table_service_client):
    """Test get_next_set selects a set without a timestamp."""
    mock_sets_client = mock_table_service_client._mock_sets_client
    set_old = create_set_entity(1, 'OLD', last_updated='2024-01-01T00:00:00Z')
    set_new = create_set_entity(2, 'NEW') # No timestamp
    mock_sets_client.list_entities.return_value = [set_old, set_new]

    next_set = get_next_set(mock_sets_client)
    assert next_set['id'] == 2
    assert next_set['code'] == 'NEW'

def test_get_next_set_oldest_set(mock_table_service_client):
    """Test get_next_set selects the set with the oldest timestamp."""
    mock_sets_client = mock_table_service_client._mock_sets_client
    ts_now = datetime.now(timezone.utc)
    ts_oldest = (ts_now - timedelta(days=2)).isoformat()
    ts_middle = (ts_now - timedelta(days=1)).isoformat()

    set_middle = create_set_entity(1, 'MID', last_updated=ts_middle)
    set_oldest = create_set_entity(2, 'OLD', last_updated=ts_oldest)
    mock_sets_client.list_entities.return_value = [set_middle, set_oldest]

    next_set = get_next_set(mock_sets_client)
    assert next_set['id'] == 2
    assert next_set['code'] == 'OLD'

def test_process_blueprint_basic():
    """Test basic processing of blueprint data into an entity."""
    bp_data = create_blueprint_data(101, "Test Card", "TST", collector_num='5', rarity='R')
    set_code = "TST"
    entity = process_blueprint(bp_data, set_code)

    assert entity['PartitionKey'] == set_code
    assert entity['RowKey'] == '101' # Blueprint ID as RowKey
    assert entity['id'] == 101
    assert entity['name'] == "Test Card"
    assert entity['collector_number'] == '5'
    assert entity['rarity'] == 'R'
    assert entity['possible_languages'] == "['en', 'de']" # Stringified list
    assert entity['possible_conditions'] == "['NM', 'LP']"
    assert entity['foil_available'] == 'True' # Stringified boolean
    assert 'last_updated' in entity

def test_main_success_run(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now):
    """Test a successful run processing one set with a few blueprints."""
    # Arrange
    set_to_process = create_set_entity(5, 'RUN') # New set
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_blueprints_client = mock_table_service_client._mock_blueprints_client
    mock_sets_client.list_entities.return_value = [set_to_process]

    bp1_data = create_blueprint_data(1, "Card A", "RUN", collector_num='1a')
    bp2_data = create_blueprint_data(2, "Card B", "RUN", collector_num='2b')
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [bp1_data, bp2_data]
    mock_requests_session.get.return_value = mock_response

    # Act
    getCardtraderBlueprints_main(mock_timer)

    # Assert
    # 1. Correct set selected
    mock_sets_client.list_entities.assert_called_once()
    # 2. API called for blueprints
    mock_requests_session.get.assert_called_once_with(
        "https://api.cardtrader.com/api/v2/blueprints/export",
        headers={"Authorization": f"Bearer {MOCK_API_KEY}", "Accept": "application/json"},
        params={"expansion_id": 5}
    )
    # 3. Blueprints table checked/created
    mock_table_service_client.create_table.assert_called_with(table_name=BLUEPRINTS_TABLE)
    # 4. Blueprints submitted in transaction
    assert mock_blueprints_client.submit_transaction.call_count == 1
    submitted_ops = mock_blueprints_client.submit_transaction.call_args[0][0]
    assert len(submitted_ops) == 2
    assert submitted_ops[0][0] == 'upsert'
    assert submitted_ops[0][1]['PartitionKey'] == 'RUN'
    assert submitted_ops[0][1]['RowKey'] == '1' # BP ID 1
    assert submitted_ops[1][0] == 'upsert'
    assert submitted_ops[1][1]['PartitionKey'] == 'RUN'
    assert submitted_ops[1][1]['RowKey'] == '2' # BP ID 2
    # 5. Set timestamp updated
    mock_sets_client.update_entity.assert_called_once()
    updated_set_entity = mock_sets_client.update_entity.call_args[0][0]
    assert updated_set_entity['id'] == 5
    assert updated_set_entity['blueprints_last_updated'] == mock_datetime_now.isoformat()

def test_main_api_error(mock_table_service_client, mock_requests_session, mock_timer):
    """Test run where the Cardtrader API returns an error."""
    # Arrange
    set_to_process = create_set_entity(6, 'ERR')
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_blueprints_client = mock_table_service_client._mock_blueprints_client
    mock_sets_client.list_entities.return_value = [set_to_process]

    mock_response = MagicMock(status_code=500, text="Server Error")
    mock_requests_session.get.return_value = mock_response

    # Act
    getCardtraderBlueprints_main(mock_timer)

    # Assert
    # 1. API was called
    mock_requests_session.get.assert_called_once()
    # 2. No blueprints submitted
    mock_blueprints_client.submit_transaction.assert_not_called()
    # 3. Set timestamp NOT updated
    mock_sets_client.update_entity.assert_not_called()

def test_main_no_blueprints_returned(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now):
    """Test run where the API returns an empty list of blueprints."""
    # Arrange
    set_to_process = create_set_entity(7, 'EMP')
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_blueprints_client = mock_table_service_client._mock_blueprints_client
    mock_sets_client.list_entities.return_value = [set_to_process]

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [] # Empty list
    mock_requests_session.get.return_value = mock_response

    # Act
    getCardtraderBlueprints_main(mock_timer)

    # Assert
    # 1. API was called
    mock_requests_session.get.assert_called_once()
    # 2. No blueprints submitted (submit_transaction not called)
    mock_blueprints_client.submit_transaction.assert_not_called()
    # 3. Set timestamp IS updated (processed 0 blueprints successfully)
    mock_sets_client.update_entity.assert_called_once()
    updated_set_entity = mock_sets_client.update_entity.call_args[0][0]
    assert updated_set_entity['id'] == 7
    assert updated_set_entity['blueprints_last_updated'] == mock_datetime_now.isoformat()

def test_main_blueprint_processing_error(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now):
    """Test run where processing one blueprint fails but others succeed."""
    # Arrange
    set_to_process = create_set_entity(8, 'PROC')
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_blueprints_client = mock_table_service_client._mock_blueprints_client
    mock_sets_client.list_entities.return_value = [set_to_process]

    bp_good1 = create_blueprint_data(10, "Good 1", "PROC")
    bp_bad = create_blueprint_data(11, "Bad", "PROC")
    bp_bad['fixed_properties'] = None # Cause a TypeError in process_blueprint
    bp_good2 = create_blueprint_data(12, "Good 2", "PROC")

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [bp_good1, bp_bad, bp_good2]
    mock_requests_session.get.return_value = mock_response

    # Act
    getCardtraderBlueprints_main(mock_timer)

    # Assert
    # 1. API called
    mock_requests_session.get.assert_called_once()
    # 2. Transaction submitted only with good blueprints
    assert mock_blueprints_client.submit_transaction.call_count == 1
    submitted_ops = mock_blueprints_client.submit_transaction.call_args[0][0]
    assert len(submitted_ops) == 2 # Only the good ones
    assert submitted_ops[0][1]['id'] == 10
    assert submitted_ops[1][1]['id'] == 12
    # 3. Set timestamp IS updated (run completed, despite error)
    mock_sets_client.update_entity.assert_called_once()
    updated_set_entity = mock_sets_client.update_entity.call_args[0][0]
    assert updated_set_entity['id'] == 8
    assert updated_set_entity['blueprints_last_updated'] == mock_datetime_now.isoformat()

def test_main_batching(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now):
    """Test that blueprints are submitted in batches."""
    # Arrange
    set_to_process = create_set_entity(9, 'BATCH')
    mock_sets_client = mock_table_service_client._mock_sets_client
    mock_blueprints_client = mock_table_service_client._mock_blueprints_client
    mock_sets_client.list_entities.return_value = [set_to_process]

    # Create 150 blueprints (batch size is 100)
    blueprints_data = [create_blueprint_data(i, f"Card {i}", "BATCH") for i in range(150)]
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = blueprints_data
    mock_requests_session.get.return_value = mock_response

    # Act
    getCardtraderBlueprints_main(mock_timer)

    # Assert
    # 1. API called
    mock_requests_session.get.assert_called_once()
    # 2. Two transactions submitted (100 + 50)
    assert mock_blueprints_client.submit_transaction.call_count == 2
    # Check batch sizes
    assert len(mock_blueprints_client.submit_transaction.call_args_list[0][0][0]) == 100
    assert len(mock_blueprints_client.submit_transaction.call_args_list[1][0][0]) == 50
    # 3. Set timestamp updated
    mock_sets_client.update_entity.assert_called_once()
