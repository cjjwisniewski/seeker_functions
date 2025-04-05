import pytest
import datetime
import time
from unittest.mock import patch, MagicMock, call, ANY
import azure.functions as func
import pytz
from azure.data.tables import TableServiceClient, TableClient, TableEntity
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Import the function to test
# Add the parent directory to the path to allow importing the function
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from checkCardtraderStock import main as checkCardtraderStock_main
from checkCardtraderStock import get_cardtrader_session # If needed for direct testing

# Constants matching the main function
TIMESTAMPS_TABLE_NAME = "userCheckTimestamps"
BLUEPRINTS_TABLE_NAME = "blueprintscardtrader"
USER_TABLE_PREFIX = "user"
CHECK_INTERVAL_HOURS = 24
RATE_LIMIT_SECONDS = 1.1
CARDTRADER_MARKETPLACE_URL = "https://api.cardtrader.com/v2/marketplace/products" # Match URL from main script

# --- Fixtures ---

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;") # Example Azurite string
    monkeypatch.setenv("CARDTRADER_API_KEY", "test-api-key")

@pytest.fixture
def mock_table_service_client(monkeypatch):
    """Mock TableServiceClient and its methods."""
    mock_service_client = MagicMock(spec=TableServiceClient)
    mock_table_clients = {} # Dictionary to store mocks per table name

    def get_table_client_side_effect(table_name):
        if table_name not in mock_table_clients:
            # Create a default mock table client
            mock_client = MagicMock(spec=TableClient)
            mock_client.table_name = table_name
            # Default behaviors (can be overridden in tests)
            mock_client.list_entities.return_value = []
            mock_client.get_entity.side_effect = ResourceNotFoundError("Entity not found")
            mock_client.create_table.side_effect = HttpResponseError(message="TableAlreadyExists", status_code=409) # Simulate exists by default
            mock_table_clients[table_name] = mock_client
        return mock_table_clients[table_name]

    mock_service_client.get_table_client.side_effect = get_table_client_side_effect
    mock_service_client.list_tables.return_value = [] # Default: no tables

    # Patch the class method 'from_connection_string' to return our mock instance
    monkeypatch.setattr("checkCardtraderStock.TableServiceClient.from_connection_string", MagicMock(return_value=mock_service_client))

    # Add the dictionary to the mock service client for easy access in tests
    mock_service_client._mock_table_clients = mock_table_clients
    return mock_service_client


@pytest.fixture
def mock_requests_session(monkeypatch):
    """Mock requests.Session and its get method."""
    mock_session_instance = MagicMock()
    # Default: 200 OK, empty list (Out Of Stock)
    mock_response = MagicMock(status_code=200, url="mock://url")
    mock_response.json.return_value = []
    mock_session_instance.get.return_value = mock_response

    # Patch requests.Session constructor to return our mock instance
    monkeypatch.setattr("checkCardtraderStock.requests.Session", MagicMock(return_value=mock_session_instance))
    return mock_session_instance

@pytest.fixture
def mock_timer():
    """Mock TimerRequest."""
    # Create a mock TimerRequest object
    timer_mock = MagicMock(spec=func.TimerRequest)
    timer_mock.past_due = False
    # If your function uses timer properties like schedule_status, mock them too
    # timer_mock.schedule_status = {'Last': datetime.datetime.now(pytz.utc) - datetime.timedelta(minutes=6)}
    return timer_mock

@pytest.fixture
def mock_datetime_now(monkeypatch):
    """Mock datetime.datetime.now to return a fixed UTC time."""
    fixed_time = datetime.datetime(2025, 4, 5, 12, 0, 0, tzinfo=pytz.utc)

    # Mock the datetime class itself within the target module
    mock_datetime_class = MagicMock(spec=datetime.datetime)
    mock_datetime_class.now.return_value = fixed_time
    mock_datetime_class.fromisoformat.side_effect = lambda s: datetime.datetime.fromisoformat(s) # Use real fromisoformat
    mock_datetime_class.min = datetime.datetime.min

    monkeypatch.setattr("checkCardtraderStock.datetime.datetime", mock_datetime_class)
    # Also patch timedelta if necessary, but usually it's fine
    monkeypatch.setattr("checkCardtraderStock.datetime.timedelta", datetime.timedelta)

    return fixed_time

@pytest.fixture
def mock_time(monkeypatch):
    """Mock time.time and time.sleep."""
    mock_sleep = MagicMock()
    # Use a list to allow modification within the side effect function
    call_count = [0]
    start_time = time.time()

    def time_side_effect():
        # Simulate time advancing slightly with each call
        current_time = start_time + call_count[0] * 0.1
        call_count[0] += 1
        return current_time

    mock_time_func = MagicMock(side_effect=time_side_effect)

    monkeypatch.setattr('checkCardtraderStock.time.sleep', mock_sleep)
    monkeypatch.setattr('checkCardtraderStock.time.time', mock_time_func)
    return {'sleep': mock_sleep, 'time': mock_time_func}


# --- Helper Function ---
def create_card_entity(pk, rk, name="Test Card", lang="en", finish="nonfoil", stock=False):
    """Helper to create a TableEntity for a card."""
    return TableEntity({
        'PartitionKey': pk, 'RowKey': rk,
        'name': name, 'language': lang, 'finish': finish,
        'cardtrader_stock': stock
        # Add other fields if your function logic depends on them
    })

# --- Test Cases ---

def test_no_user_tables_found(mock_table_service_client, mock_timer, mock_datetime_now, caplog):
    """Test scenario where no tables starting with USER_TABLE_PREFIX exist."""
    # Setup: list_tables returns non-user tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name="someothertable"),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]
    # Setup: Ensure timestamp table mock exists
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    mock_timestamps_client.create_table.side_effect = HttpResponseError(message="TableAlreadyExists", status_code=409)

    with caplog.at_level(logging.INFO):
        checkCardtraderStock_main(mock_timer)

    # Assertions:
    # Check logs
    assert f"Found 0 user tables." in caplog.text
    assert f"No user tables found starting with prefix '{USER_TABLE_PREFIX}'. Exiting." in caplog.text
    # Ensure essential table clients were requested
    mock_table_service_client.get_table_client.assert_any_call(TIMESTAMPS_TABLE_NAME)
    mock_table_service_client.get_table_client.assert_any_call(BLUEPRINTS_TABLE_NAME)
    # Ensure no user table client was requested
    user_table_calls = [
        call_args for call_args in mock_table_service_client.get_table_client.call_args_list
        if call_args[0][0].startswith(USER_TABLE_PREFIX)
    ]
    assert not user_table_calls
    # Ensure timestamp table wasn't queried for entities or updated
    mock_timestamps_client.list_entities.assert_not_called()
    mock_timestamps_client.upsert_entity.assert_not_called()


def test_user_never_checked_card_in_stock(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now, mock_time, caplog):
    """Test checking a user who has no timestamp entry; card is found in stock."""
    user_id = "userNeverChecked"
    user_table_name = f"{USER_TABLE_PREFIX}{user_id}"

    # Setup: List tables including the user table
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=user_table_name),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]

    # Setup: Timestamps table is empty
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    mock_timestamps_client.list_entities.return_value = []

    # Setup: User table contains one card (foil)
    mock_user_client = mock_table_service_client.get_table_client(user_table_name)
    card_entity = create_card_entity('SET', '123_en_foil', name="Test Foil", lang='en', finish='foil', stock=False)
    mock_user_client.list_entities.return_value = [card_entity]

    # Setup: Blueprint table contains the blueprint with an ID
    mock_blueprints_client = mock_table_service_client.get_table_client(BLUEPRINTS_TABLE_NAME)
    blueprint_entity = TableEntity({'id': 999})
    mock_blueprints_client.get_entity.return_value = blueprint_entity

    # Setup: Cardtrader API response (In Stock - non-empty list)
    mock_response = MagicMock(status_code=200, url="mock://cardtrader/foil")
    mock_response.json.return_value = [{'id': 1, 'price': 10.0, 'quantity': 1}]
    mock_requests_session.get.return_value = mock_response

    # Execute
    with caplog.at_level(logging.DEBUG): # Use DEBUG to see API call logs
        checkCardtraderStock_main(mock_timer)

    # Assertions
    # 1. Correct user selected
    assert f"Selected user table to check: {user_table_name}" in caplog.text
    # 2. Blueprint was fetched
    mock_blueprints_client.get_entity.assert_called_once_with(partition_key='SET', row_key='123_en_foil')
    # 3. Rate limit sleep was NOT called (first API call)
    mock_time['sleep'].assert_not_called()
    # 4. API was called with correct params for foil card
    expected_params = {'blueprint_id': 999, 'language': 'en', 'foil': 'true'}
    mock_requests_session.get.assert_called_once_with(CARDTRADER_MARKETPLACE_URL, params=expected_params, timeout=10)
    assert f"Calling Cardtrader API: {CARDTRADER_MARKETPLACE_URL} with params: {expected_params}" in caplog.text
    assert f"API success for blueprint 999 with params {expected_params}. Stock found: True" in caplog.text
    # 5. User card entity was updated (stock changed from False to True)
    mock_user_client.update_entity.assert_called_once()
    call_args, call_kwargs = mock_user_client.update_entity.call_args
    updated_entity_arg = call_kwargs.get('entity')
    assert updated_entity_arg['cardtrader_stock'] is True
    assert updated_entity_arg['PartitionKey'] == 'SET'
    assert updated_entity_arg['RowKey'] == '123_en_foil'
    assert call_kwargs.get('mode') == UpdateMode.MERGE
    assert f"Updated stock for Test Foil (SET/123_en_foil) to True" in caplog.text
    # 6. Timestamp was updated for the user
    expected_ts_entity = {
        'PartitionKey': user_id, 'RowKey': 'Timestamp',
        'LastChecked': mock_datetime_now.isoformat()
    }
    mock_timestamps_client.upsert_entity.assert_called_once_with(entity=expected_ts_entity, mode=UpdateMode.REPLACE)
    assert f"Successfully updated timestamp for user {user_id}" in caplog.text
    # 7. Final log message
    assert f"checkCardtraderStock function execution finished for user table {user_table_name}" in caplog.text
    assert "Cards processed: 1" in caplog.text
    assert "API calls: 1" in caplog.text
    assert "Stock updates: 1" in caplog.text


def test_user_checked_recently(mock_table_service_client, mock_timer, mock_datetime_now, caplog):
    """Test scenario where the only user was checked within the CHECK_INTERVAL_HOURS."""
    user_id = "userCheckedRecently"
    user_table_name = f"{USER_TABLE_PREFIX}{user_id}"
    now = mock_datetime_now
    recent_check_time = now - datetime.timedelta(hours=CHECK_INTERVAL_HOURS / 2)

    # Setup: List tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=user_table_name),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]

    # Setup: Timestamps table has recent entry for the user
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    timestamp_entity = TableEntity({
        'PartitionKey': user_id, 'RowKey': 'Timestamp',
        'LastChecked': recent_check_time.isoformat()
    })
    mock_timestamps_client.list_entities.return_value = [timestamp_entity]

    # Execute
    with caplog.at_level(logging.INFO):
        checkCardtraderStock_main(mock_timer)

    # Assertions
    assert "No users require checking at this time." in caplog.text
    # Ensure user table wasn't accessed
    mock_user_client = mock_table_service_client._mock_table_clients.get(user_table_name)
    assert mock_user_client is None or mock_user_client.list_entities.call_count == 0
    # Ensure timestamp wasn't updated again
    mock_timestamps_client.upsert_entity.assert_not_called()


def test_user_checked_long_ago_card_oos(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now, mock_time, caplog):
    """Test checking a user checked long ago; card is Out Of Stock (404)."""
    user_id = "userCheckedLongAgo"
    user_table_name = f"{USER_TABLE_PREFIX}{user_id}"
    now = mock_datetime_now
    old_check_time = now - datetime.timedelta(hours=CHECK_INTERVAL_HOURS * 2)

    # Setup: List tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=user_table_name),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]

    # Setup: Timestamps table has old entry
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    timestamp_entity = TableEntity({
        'PartitionKey': user_id, 'RowKey': 'Timestamp',
        'LastChecked': old_check_time.isoformat()
    })
    mock_timestamps_client.list_entities.return_value = [timestamp_entity]

    # Setup: User table contains one card (nonfoil), already marked as in stock
    mock_user_client = mock_table_service_client.get_table_client(user_table_name)
    card_entity = create_card_entity('SET', '456_fr_nonfoil', name="Test NonFoil", lang='fr', finish='nonfoil', stock=True)
    mock_user_client.list_entities.return_value = [card_entity]

    # Setup: Blueprint table
    mock_blueprints_client = mock_table_service_client.get_table_client(BLUEPRINTS_TABLE_NAME)
    blueprint_entity = TableEntity({'id': 1001})
    mock_blueprints_client.get_entity.return_value = blueprint_entity

    # Setup: Cardtrader API response (Out Of Stock - 404)
    mock_response = MagicMock(status_code=404, url="mock://cardtrader/nonfoil/404")
    mock_response.json.side_effect = ValueError("No JSON content on 404") # Simulate no JSON body on 404
    mock_requests_session.get.return_value = mock_response

    # Execute
    with caplog.at_level(logging.INFO): # Use INFO, check specific logs
        checkCardtraderStock_main(mock_timer)

    # Assertions
    # 1. Correct user selected
    assert f"Selected user table to check: {user_table_name}" in caplog.text
    # 2. Blueprint fetched
    mock_blueprints_client.get_entity.assert_called_once_with(partition_key='SET', row_key='456_fr_nonfoil')
    # 3. API called with correct params for nonfoil
    expected_params = {'blueprint_id': 1001, 'language': 'fr', 'foil': 'false'}
    mock_requests_session.get.assert_called_once_with(CARDTRADER_MARKETPLACE_URL, params=expected_params, timeout=10)
    assert f"Cardtrader API returned 404 (Not Found) for blueprint 1001 with params {expected_params}" in caplog.text
    # 4. User card entity updated (stock changed from True to False)
    mock_user_client.update_entity.assert_called_once()
    call_args, call_kwargs = mock_user_client.update_entity.call_args
    updated_entity_arg = call_kwargs.get('entity')
    assert updated_entity_arg['cardtrader_stock'] is False
    assert updated_entity_arg['PartitionKey'] == 'SET'
    assert updated_entity_arg['RowKey'] == '456_fr_nonfoil'
    assert call_kwargs.get('mode') == UpdateMode.MERGE
    assert f"Updated stock for Test NonFoil (SET/456_fr_nonfoil) to False" in caplog.text
    # 5. Timestamp updated
    expected_ts_entity = {
        'PartitionKey': user_id, 'RowKey': 'Timestamp',
        'LastChecked': mock_datetime_now.isoformat()
    }
    mock_timestamps_client.upsert_entity.assert_called_once_with(entity=expected_ts_entity, mode=UpdateMode.REPLACE)
    assert f"Successfully updated timestamp for user {user_id}" in caplog.text


def test_multiple_users_selects_oldest(mock_table_service_client, mock_timer, mock_datetime_now, caplog):
    """Test that the user with the oldest timestamp (beyond interval) is selected."""
    now = mock_datetime_now
    user_id_recent = "userRecent"
    user_id_oldest = "userOldest"
    user_id_never = "userNever"

    # Setup: List tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=f"{USER_TABLE_PREFIX}{user_id_recent}"),
        MagicMock(name=f"{USER_TABLE_PREFIX}{user_id_oldest}"),
        MagicMock(name=f"{USER_TABLE_PREFIX}{user_id_never}"),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]

    # Setup: Timestamps table entries
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    timestamps = [
        TableEntity({'PartitionKey': user_id_recent, 'RowKey': 'Timestamp', 'LastChecked': (now - datetime.timedelta(hours=5)).isoformat()}),
        TableEntity({'PartitionKey': user_id_oldest, 'RowKey': 'Timestamp', 'LastChecked': (now - datetime.timedelta(days=2)).isoformat()}),
        # user_id_never has no entry
    ]
    mock_timestamps_client.list_entities.return_value = timestamps

    # Mock the user table client for the selected user to avoid errors later
    mock_user_client_never = mock_table_service_client.get_table_client(f"{USER_TABLE_PREFIX}{user_id_never}")
    mock_user_client_never.list_entities.return_value = [] # Assume empty for simplicity

    # Execute
    with caplog.at_level(logging.INFO):
        checkCardtraderStock_main(mock_timer)

    # Assertions
    # User 'userNever' should be selected as they have the 'minimum' timestamp
    assert f"Selected user table to check: {USER_TABLE_PREFIX}{user_id_never}" in caplog.text
    # Ensure the selected user's timestamp is updated
    expected_ts_entity = {
        'PartitionKey': user_id_never, 'RowKey': 'Timestamp',
        'LastChecked': mock_datetime_now.isoformat()
    }
    # Check that upsert was called with the correct user ID
    mock_timestamps_client.upsert_entity.assert_called_once_with(entity=expected_ts_entity, mode=UpdateMode.REPLACE)


def test_blueprint_not_found(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now, mock_time, caplog):
    """Test scenario where the blueprint for a card is not found in the blueprints table."""
    user_id = "userBlueprintNotFound"
    user_table_name = f"{USER_TABLE_PREFIX}{user_id}"

    # Setup: List tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=user_table_name),
        MagicMock(name=BLUEPRINTS_TABLE_NAME),
        MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]
    # Setup: Timestamps table empty (user never checked)
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    mock_timestamps_client.list_entities.return_value = []
    # Setup: User table with one card, marked as in stock
    mock_user_client = mock_table_service_client.get_table_client(user_table_name)
    card_entity = create_card_entity('NOS', 'ET_en_nonfoil', name="No Blueprint Card", stock=True)
    mock_user_client.list_entities.return_value = [card_entity]
    # Setup: Blueprint table get_entity raises ResourceNotFoundError
    mock_blueprints_client = mock_table_service_client.get_table_client(BLUEPRINTS_TABLE_NAME)
    mock_blueprints_client.get_entity.side_effect = ResourceNotFoundError("Blueprint not found")

    # Execute
    with caplog.at_level(logging.WARNING):
        checkCardtraderStock_main(mock_timer)

    # Assertions
    # 1. Log message indicates blueprint not found
    assert f"Blueprint not found for card No Blueprint Card (NOS/ET_en_nonfoil). Setting stock to False." in caplog.text
    # 2. API was NOT called
    mock_requests_session.get.assert_not_called()
    # 3. User card entity was updated (stock changed from True to False)
    mock_user_client.update_entity.assert_called_once()
    call_args, call_kwargs = mock_user_client.update_entity.call_args
    updated_entity_arg = call_kwargs.get('entity')
    assert updated_entity_arg['cardtrader_stock'] is False
    assert call_kwargs.get('mode') == UpdateMode.MERGE
    # 4. Timestamp was updated for the user
    mock_timestamps_client.upsert_entity.assert_called_once()


def test_rate_limiting(mock_table_service_client, mock_requests_session, mock_timer, mock_datetime_now, mock_time, caplog):
    """Test that time.sleep is called between API calls for multiple cards."""
    user_id = "userMultiCard"
    user_table_name = f"{USER_TABLE_PREFIX}{user_id}"

    # Setup: List tables
    mock_table_service_client.list_tables.return_value = [
        MagicMock(name=user_table_name), MagicMock(name=BLUEPRINTS_TABLE_NAME), MagicMock(name=TIMESTAMPS_TABLE_NAME)
    ]
    # Setup: Timestamps empty
    mock_timestamps_client = mock_table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
    mock_timestamps_client.list_entities.return_value = []
    # Setup: User table with two cards
    mock_user_client = mock_table_service_client.get_table_client(user_table_name)
    card1 = create_card_entity('SET1', '001_en_nonfoil', name="Card 1")
    card2 = create_card_entity('SET1', '002_en_foil', name="Card 2", finish="foil")
    mock_user_client.list_entities.return_value = [card1, card2]
    # Setup: Blueprints table
    mock_blueprints_client = mock_table_service_client.get_table_client(BLUEPRINTS_TABLE_NAME)
    blueprint1 = TableEntity({'id': 111})
    blueprint2 = TableEntity({'id': 222})
    mock_blueprints_client.get_entity.side_effect = [blueprint1, blueprint2] # Return in order
    # Setup: API responses (both OOS - empty list)
    mock_response = MagicMock(status_code=200, url="mock://cardtrader/multi")
    mock_response.json.return_value = []
    mock_requests_session.get.return_value = mock_response

    # Execute
    with caplog.at_level(logging.DEBUG):
        checkCardtraderStock_main(mock_timer)

    # Assertions
    # 1. API called twice
    assert mock_requests_session.get.call_count == 2
    # 2. time.sleep called once (between the two calls)
    mock_time['sleep'].assert_called_once()
    # Check the approximate wait time (should be close to RATE_LIMIT_SECONDS)
    # Note: This depends on the mock_time fixture's side_effect timing
    # sleep_call_args = mock_time['sleep'].call_args[0][0]
    # assert sleep_call_args == pytest.approx(RATE_LIMIT_SECONDS - 0.1, abs=0.05) # Example check
    assert "Rate limiting: waiting" in caplog.text
    # 3. No stock updates occurred (both cards were OOS and started as OOS)
    mock_user_client.update_entity.assert_not_called()
    # 4. Timestamp updated once at the end
    mock_timestamps_client.upsert_entity.assert_called_once()


# --- Add more test cases ---
# - test_api_rate_limit_hit (429 response, should break loop and update timestamp)
# - test_api_other_error (e.g., 500 response, should log error, set stock=False, continue)
# - test_api_network_error (requests.exceptions.RequestException, should log, continue)
# - test_empty_user_table (should just update timestamp)
# - test_user_table_not_found (should log warning and update timestamp) - Needs adjustment in mock_table_service_client fixture
# - test_timestamp_parsing_error (should treat user as never checked)
# - test_card_with_missing_pk_rk (should log warning and skip card)
# - test_update_entity_fails (should log error)
# - test_upsert_timestamp_fails (should log error)
# - test_blueprint_missing_id (should log warning, set stock=False)
