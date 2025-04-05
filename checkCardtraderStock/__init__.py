import logging
import azure.functions as func
import os
import datetime
import time
import requests
import pytz
from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Constants for table names - replace with actual names or environment variables
TIMESTAMPS_TABLE_NAME = "userCheckTimestamps" # Table tracking last check time per user
BLUEPRINTS_TABLE_NAME = "blueprintscardtrader" # Table with Cardtrader blueprint IDs
USER_TABLE_PREFIX = "user" # Prefix for user-specific tables

# Cardtrader API settings - use environment variables
CARDTRADER_API_KEY = os.environ.get("CARDTRADER_API_KEY")
# Example URL, replace with the actual marketplace endpoint if different
CARDTRADER_MARKETPLACE_URL = "https://api.cardtrader.com/v2/marketplace/products/{blueprint_id}"

# Configuration
RATE_LIMIT_SECONDS = 1.1 # Slightly more than 1 second to be safe
CHECK_INTERVAL_HOURS = 24 # Check each user at most once per day

def get_cardtrader_session():
    """Creates a requests session with Cardtrader auth headers."""
    if not CARDTRADER_API_KEY:
        raise ValueError("CARDTRADER_API_KEY environment variable not set.")
    
    session = requests.Session()
    session.headers.update({
        'Authorization': f'Bearer {CARDTRADER_API_KEY}',
        'Accept': 'application/json'
    })
    return session

def get_user_id_from_table_name(table_name):
    """Extracts user ID assuming table name format 'user<ID>'."""
    if table_name.startswith(USER_TABLE_PREFIX):
        return table_name[len(USER_TABLE_PREFIX):]
    return None

def main(timer: func.TimerRequest) -> None:
    start_time = time.time()
    now_utc = datetime.datetime.now(pytz.utc)
    logging.info(f'Python timer trigger function ran at {now_utc.isoformat()}')

    if timer.past_due:
        logging.warning('The timer is past due!')

    # 1. Get connection string
    conn_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_string:
        logging.error("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
        return

    # 2. Initialize TableServiceClient
    try:
        table_service_client = TableServiceClient.from_connection_string(conn_string)
    except Exception as e:
        logging.error(f"Failed to connect to Table Service: {e}")
        return

    # 3. Get clients for timestamps and blueprints tables
    try:
        timestamps_table_client = table_service_client.get_table_client(TIMESTAMPS_TABLE_NAME)
        # Ensure timestamps table exists, create if not
        try:
            timestamps_table_client.create_table()
            logging.info(f"Table '{TIMESTAMPS_TABLE_NAME}' created.")
        except HttpResponseError as e:
            if "TableAlreadyExists" not in str(e):
                raise # Reraise if it's not a 'table already exists' error
            pass # Table already exists, which is fine

        blueprints_table_client = table_service_client.get_table_client(BLUEPRINTS_TABLE_NAME)
    except Exception as e:
        logging.error(f"Failed to get table clients for required tables: {e}")
        return

    # 4. Get list of user table names by listing all tables and filtering by prefix
    user_table_names = []
    try:
        all_tables = table_service_client.list_tables()
        user_table_names = [table.name for table in all_tables if table.name.startswith(USER_TABLE_PREFIX)]
        logging.info(f"Found {len(user_table_names)} user tables.")
    except Exception as e:
        logging.error(f"Failed to list tables: {e}")
        return

    if not user_table_names:
        logging.info("No user tables found starting with prefix '{USER_TABLE_PREFIX}'. Exiting.")
        return

    # 5. & 6. Determine next user table to check
    user_timestamps = {}
    user_ids = [get_user_id_from_table_name(name) for name in user_table_names if get_user_id_from_table_name(name)]

    if not user_ids:
        logging.warning("No valid user IDs could be extracted from table names.")
        return

    try:
        # Query existing timestamps
        entities = timestamps_table_client.list_entities()
        for entity in entities:
             # Assuming PartitionKey is the user_id
             if 'LastChecked' in entity and entity['PartitionKey'] in user_ids:
                 # Attempt to parse the timestamp, handling potential format issues
                 try:
                     last_checked_dt = datetime.datetime.fromisoformat(entity['LastChecked'].replace('Z', '+00:00'))
                     # Ensure it's timezone-aware (UTC)
                     if last_checked_dt.tzinfo is None:
                         last_checked_dt = pytz.utc.localize(last_checked_dt)
                     user_timestamps[entity['PartitionKey']] = last_checked_dt
                 except (ValueError, TypeError) as dt_error:
                     logging.warning(f"Could not parse LastChecked timestamp '{entity.get('LastChecked')}' for user {entity['PartitionKey']}: {dt_error}. Treating as never checked.")
                     user_timestamps[entity['PartitionKey']] = None # Treat invalid date as never checked

    except Exception as e:
        logging.error(f"Failed to query timestamps table: {e}")
        # Decide if we should continue or exit. Let's exit for safety.
        return

    # Determine who needs checking
    eligible_users = []
    check_threshold = now_utc - datetime.timedelta(hours=CHECK_INTERVAL_HOURS)

    for user_id in user_ids:
        last_checked = user_timestamps.get(user_id)
        if last_checked is None: # Never checked
            eligible_users.append({'id': user_id, 'last_checked': datetime.datetime.min.replace(tzinfo=pytz.utc)}) # Prioritize never checked
        elif last_checked < check_threshold: # Checked long enough ago
             eligible_users.append({'id': user_id, 'last_checked': last_checked})

    if not eligible_users:
        logging.info("No users require checking at this time.")
        return

    # Sort eligible users by last checked time (oldest first)
    eligible_users.sort(key=lambda x: x['last_checked'])
    user_to_check = eligible_users[0]
    user_id_to_check = user_to_check['id']
    user_table_to_check = f"{USER_TABLE_PREFIX}{user_id_to_check}"

    logging.info(f"Selected user table to check: {user_table_to_check} (Last checked: {user_to_check['last_checked'].isoformat()})")

    # 8. Get user's table client
    try:
        user_table_client = table_service_client.get_table_client(user_table_to_check)
    except Exception as e:
        logging.error(f"Failed to get table client for {user_table_to_check}: {e}")
        # If the user table doesn't exist, log it and update timestamp as checked to avoid retrying immediately
        logging.warning(f"User table {user_table_to_check} not found. Skipping check and updating timestamp.")
        try:
            timestamp_entity = {
                'PartitionKey': user_id_to_check,
                'RowKey': 'Timestamp', # Fixed RowKey for timestamp entries
                'LastChecked': now_utc.isoformat()
            }
            timestamps_table_client.upsert_entity(entity=timestamp_entity, mode=UpdateMode.REPLACE)
            logging.info(f"Updated timestamp for skipped user {user_id_to_check}.")
        except Exception as ts_e:
            logging.error(f"Failed to update timestamp for skipped user {user_id_to_check}: {ts_e}")
        return

    # 9. Query all cards from user's table
    try:
        user_cards = list(user_table_client.list_entities())
        logging.info(f"Found {len(user_cards)} cards in table {user_table_to_check}.")
    except Exception as e:
        logging.error(f"Failed to list entities for user table {user_table_to_check}: {e}")
        return # Cannot proceed without the card list

    if not user_cards:
        logging.info(f"User table {user_table_to_check} is empty. Updating timestamp.")
        # Update timestamp even if table is empty
        try:
            timestamp_entity = {
                'PartitionKey': user_id_to_check,
                'RowKey': 'Timestamp',
                'LastChecked': now_utc.isoformat()
            }
            timestamps_table_client.upsert_entity(entity=timestamp_entity, mode=UpdateMode.REPLACE)
            logging.info(f"Updated timestamp for user {user_id_to_check} with empty table.")
        except Exception as ts_e:
            logging.error(f"Failed to update timestamp for user {user_id_to_check}: {ts_e}")
        return

    # 10. Initialize Cardtrader session
    try:
        ct_session = get_cardtrader_session()
    except ValueError as ve:
        logging.error(f"Failed to initialize Cardtrader session: {ve}")
        return # Cannot proceed without API key

    # 11. Loop through cards and check stock
    updated_count = 0
    api_call_count = 0
    last_api_call_time = 0

    for card in user_cards:
        card_pk = card.get('PartitionKey')
        card_rk = card.get('RowKey')
        card_name = card.get('name', 'Unknown') # For logging

        if not card_pk or not card_rk:
            logging.warning(f"Skipping card with missing PartitionKey or RowKey in table {user_table_to_check}: {card}")
            continue

        blueprint_id = None
        try:
            # a. Find blueprint ID
            # Assuming blueprint table uses same PK/RK structure for lookup
            blueprint_entity = blueprints_table_client.get_entity(partition_key=card_pk, row_key=card_rk)
            blueprint_id = blueprint_entity.get('id') # Cardtrader blueprint ID is stored in 'id' field
            if not blueprint_id:
                 logging.warning(f"Blueprint found for {card_name} ({card_pk}/{card_rk}) but 'id' field is missing or empty.")

        except ResourceNotFoundError:
            logging.warning(f"Blueprint not found for card {card_name} ({card_pk}/{card_rk}). Setting stock to False.")
            # Update stock to False if blueprint doesn't exist
            if card.get('cardtrader_stock') is not False:
                card['cardtrader_stock'] = False
                try:
                    user_table_client.update_entity(entity=card, mode=UpdateMode.MERGE)
                    updated_count += 1
                except Exception as update_e:
                    logging.error(f"Failed to update stock (to False) for missing blueprint {card_name} ({card_pk}/{card_rk}): {update_e}")
            continue # Move to next card
        except Exception as bp_e:
            logging.error(f"Error fetching blueprint for {card_name} ({card_pk}/{card_rk}): {bp_e}")
            continue # Skip this card on blueprint error

        # b. If blueprint ID found, check stock via API
        if blueprint_id:
            try:
                # i. Rate limit
                current_time = time.time()
                time_since_last_call = current_time - last_api_call_time
                if time_since_last_call < RATE_LIMIT_SECONDS:
                    wait_time = RATE_LIMIT_SECONDS - time_since_last_call
                    logging.debug(f"Rate limiting: waiting {wait_time:.2f} seconds.")
                    time.sleep(wait_time)

                # ii. Call Cardtrader API
                api_url = CARDTRADER_MARKETPLACE_URL.format(blueprint_id=blueprint_id)
                logging.debug(f"Calling Cardtrader API: {api_url}")
                response = ct_session.get(api_url, timeout=10) # Add timeout
                last_api_call_time = time.time()
                api_call_count += 1

                # iii. Parse response
                stock_status = False
                if response.status_code == 200:
                    # Check if the response body indicates stock.
                    # This depends heavily on the API response structure.
                    # Example: Check if the response list is non-empty.
                    # Adjust this logic based on the actual Cardtrader API v2 response.
                    try:
                        data = response.json()
                        # Assuming the response is a list/array of products/offers
                        if isinstance(data, list) and len(data) > 0:
                            stock_status = True
                        # Or maybe it's an object with a 'data' key containing a list:
                        # elif isinstance(data, dict) and 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0:
                        #    stock_status = True
                        logging.debug(f"API success for blueprint {blueprint_id}. Stock found: {stock_status}")
                    except ValueError: # Includes JSONDecodeError
                         logging.error(f"Failed to decode JSON response for blueprint {blueprint_id}. URL: {api_url}, Status: {response.status_code}")
                         stock_status = False # Treat decode error as out of stock
                elif response.status_code == 404:
                     logging.warning(f"Cardtrader API returned 404 Not Found for blueprint {blueprint_id}. URL: {api_url}")
                     stock_status = False # Treat 404 as out of stock
                elif response.status_code == 429:
                    logging.error(f"Cardtrader API rate limit hit (429) for blueprint {blueprint_id}. Stopping check for this user.")
                    # Optionally break the loop or implement backoff
                    break # Stop processing this user for now
                else:
                    logging.error(f"Cardtrader API error for blueprint {blueprint_id}. Status: {response.status_code}, Response: {response.text[:200]}")
                    stock_status = False # Treat other errors as out of stock

                # iv. Update card entity if status changed
                if card.get('cardtrader_stock') != stock_status:
                    card['cardtrader_stock'] = stock_status
                    try:
                        user_table_client.update_entity(entity=card, mode=UpdateMode.MERGE)
                        logging.info(f"Updated stock for {card_name} ({card_pk}/{card_rk}) to {stock_status}")
                        updated_count += 1
                    except Exception as update_e:
                         logging.error(f"Failed to update stock for {card_name} ({card_pk}/{card_rk}): {update_e}")

            except requests.exceptions.RequestException as req_e:
                logging.error(f"Network error calling Cardtrader API for blueprint {blueprint_id}: {req_e}")
                # Decide whether to continue or stop for this user
                continue # Skip this card on network error
            except Exception as api_e:
                logging.error(f"Unexpected error during API check for blueprint {blueprint_id}: {api_e}")
                continue # Skip this card

    # 12. Update timestamp for the checked user
    try:
        timestamp_entity = {
            'PartitionKey': user_id_to_check,
            'RowKey': 'Timestamp', # Fixed RowKey
            'LastChecked': now_utc.isoformat()
        }
        timestamps_table_client.upsert_entity(entity=timestamp_entity, mode=UpdateMode.REPLACE)
        logging.info(f"Successfully updated timestamp for user {user_id_to_check}")
    except Exception as ts_e:
        logging.error(f"Failed to update timestamp for user {user_id_to_check}: {ts_e}")

    # 13. Add comprehensive error handling (done implicitly via try/except blocks)
    end_time = time.time()
    duration = end_time - start_time
    logging.info(f"checkCardtraderStock function execution finished for user table {user_table_to_check}. "
                 f"Cards processed: {len(user_cards)}, API calls: {api_call_count}, Stock updates: {updated_count}. Duration: {duration:.2f} seconds.")
