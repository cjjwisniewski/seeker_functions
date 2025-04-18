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
# Base URL for the marketplace endpoint
CARDTRADER_MARKETPLACE_URL = "https://api.cardtrader.com/api/v2/marketplace/products"

# Configuration
RATE_LIMIT_SECONDS = 1.1 # Slightly more than 1 second to be safe
CHECK_INTERVAL_HOURS = 24 # Check each user at most once per day

# Mapping for known Scryfall set codes to Cardtrader set codes
SCRYFALL_TO_CARDTRADER_SET_MAP = {
    '4bb': '4ebb',
    # Add other known mappings here as needed
}

def get_cardtrader_session():
    """Creates a requests session with Cardtrader auth headers."""
    if not CARDTRADER_API_KEY:
        raise ValueError("CARDTRADER_API_KEY environment variable not set.")
    
    session = requests.Session()
    # --- Use the PowerShell User-Agent ---
    user_agent = 'Mozilla/5.0 (Windows NT; Windows NT 10.0; en-US) WindowsPowerShell/5.1.22621.4435' # Exact string from Invoke-RestMethod log
    session.headers.update({
        'Authorization': f'Bearer {CARDTRADER_API_KEY}',
        'Accept': 'application/json', # Keep this for now, as it's standard for JSON APIs
        'User-Agent': user_agent # Set the PowerShell User-Agent
    })
    # --- End modification ---
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
             if 'CardtraderLastChecked' in entity and entity['PartitionKey'] in user_ids:
                 # Attempt to parse the timestamp, handling potential format issues
                 try:
                     last_checked_dt = datetime.datetime.fromisoformat(entity['CardtraderLastChecked'].replace('Z', '+00:00'))
                     # Ensure it's timezone-aware (UTC)
                     if last_checked_dt.tzinfo is None:
                         last_checked_dt = pytz.utc.localize(last_checked_dt)
                     user_timestamps[entity['PartitionKey']] = last_checked_dt
                 except (ValueError, TypeError) as dt_error:
                     logging.warning(f"Could not parse CardtraderLastChecked timestamp '{entity.get('CardtraderLastChecked')}' for user {entity['PartitionKey']}: {dt_error}. Treating as never checked.")
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
                'CardtraderLastChecked': now_utc.isoformat()
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
                'CardtraderLastChecked': now_utc.isoformat()
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
        blueprint_entity = None
        blueprint_id = None
        try:
            # a. Find blueprint ID by querying using PartitionKey (set_code) and name
            
            # Apply set code mapping if necessary
            blueprint_set_code = SCRYFALL_TO_CARDTRADER_SET_MAP.get(card_pk, card_pk)
            if blueprint_set_code != card_pk:
                logging.debug(f"Mapped Scryfall set code '{card_pk}' to Cardtrader set code '{blueprint_set_code}' for blueprint query.")

            # Escape single quotes in card name for the query filter
            escaped_card_name = card_name.replace("'", "''")
            query_filter = f"PartitionKey eq '{blueprint_set_code}' and name eq '{escaped_card_name}'"
            logging.debug(f"Querying blueprints table with filter: {query_filter}")

            results = list(blueprints_table_client.query_entities(query_filter=query_filter, select=["id"])) # Select only the 'id' field
            
            if len(results) == 1:
                blueprint_entity = results[0]
                blueprint_id = blueprint_entity.get('id')
                if not blueprint_id:
                    logging.warning(f"Blueprint found for {card_name} ({card_pk}) but 'id' field is missing or empty.")
            elif len(results) > 1:
                logging.warning(f"Multiple blueprints found for {card_name} ({card_pk}). Using the first result.")
                blueprint_entity = results[0] # Take the first one for now
                blueprint_id = blueprint_entity.get('id')
                if not blueprint_id:
                     logging.warning(f"First blueprint result for {card_name} ({card_pk}) has missing or empty 'id' field.")
            else: # len(results) == 0
                 logging.warning(f"Blueprint not found for card {card_name} ({card_pk}) using name query. Setting stock=False, price=None, cardtrader_id=None.")
                 # Update stock, price, and cardtrader_id if blueprint doesn't exist
                 needs_update = False
                 if card.get('cardtrader_stock') is not False:
                     card['cardtrader_stock'] = False
                     needs_update = True
                 if card.get('cardtrader_low_price') is not None:
                     card['cardtrader_low_price'] = None
                     needs_update = True
                 if card.get('cardtrader_id') is not None: # Check if cardtrader_id needs to be cleared
                     card['cardtrader_id'] = None
                     needs_update = True

                 if needs_update:
                     try:
                         user_table_client.update_entity(entity=card, mode=UpdateMode.MERGE)
                         logging.info(f"Updated missing blueprint {card_name} ({card_pk}/{card_rk}) to stock=False, price=None, cardtrader_id=None.")
                         updated_count += 1
                     except Exception as update_e:
                         logging.error(f"Failed to update stock/price/id for missing blueprint {card_name} ({card_pk}/{card_rk}): {update_e}")
                 continue # Move to next card

        except Exception as bp_e:
            logging.error(f"Error querying blueprint for {card_name} ({card_pk}/{card_rk}): {bp_e}")
            # Update stock to False on blueprint query error? Or just skip? Let's skip for now.
            # If needed, add stock update logic here similar to the 'not found' case.
            continue # Skip this card on blueprint query error

        # b. If blueprint ID found, check stock via API
        # (No ResourceNotFoundError expected here anymore, handled by query result check)
        # except Exception as bp_e: <--- This block is removed as it's covered above
        #     logging.error(f"Error fetching blueprint for {card_name} ({card_pk}/{card_rk}): {bp_e}")
        #     continue # Skip this card on blueprint error

        # b. If blueprint ID found, store it and check stock via API
        if blueprint_id: # Proceed only if a blueprint ID was successfully found
            # Store the found blueprint ID in the card entity
            card['cardtrader_id'] = blueprint_id

            try:
                # i. Rate limit
                current_time = time.time()
                time_since_last_call = current_time - last_api_call_time
                if time_since_last_call < RATE_LIMIT_SECONDS:
                    wait_time = RATE_LIMIT_SECONDS - time_since_last_call
                    logging.debug(f"Rate limiting: waiting {wait_time:.2f} seconds.")
                    time.sleep(wait_time)

                # ii. Call Cardtrader API with query parameters
                target_language_original = card.get('language', '').lower()
                target_finish = card.get('finish', '').lower()
                language_map = {'zhs': 'zh-CN', 'zht': 'zh-TW'}
                target_language_api = language_map.get(target_language_original, target_language_original)
                api_params = {'blueprint_id': blueprint_id}
                if target_language_api:
                    api_params['language'] = target_language_api
                if target_finish == 'foil':
                    api_params['foil'] = 'true'
                elif target_finish == 'nonfoil':
                     api_params['foil'] = 'false'

                # --- TEMPORARY TEST: Use requests.get directly ---
                test_headers = {
                    'Authorization': f'Bearer {CARDTRADER_API_KEY}',
                    'Accept': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
                    # Note: We are *not* explicitly setting Accept-Encoding or Connection here
                }
                logging.info(f"[TEST] Attempting direct API call for BP {blueprint_id}. URL: {CARDTRADER_MARKETPLACE_URL}, Params: {api_params}, Headers: {test_headers}")
                response = requests.get(CARDTRADER_MARKETPLACE_URL, params=api_params, headers=test_headers, timeout=10)
                # --- END TEMPORARY TEST ---

                last_api_call_time = time.time()
                api_call_count += 1

                # Log the raw response details immediately after
                logging.info(f"API Response Status for BP {blueprint_id}: {response.status_code}")
                logging.info(f"Prepared Request URL by 'requests': {response.request.url}") # Log the prepared URL
                try:
                    logging.info(f"API Response Text for BP {blueprint_id}: {response.text[:500]}")
                except Exception as log_ex:
                     logging.error(f"Error logging response text for BP {blueprint_id}: {log_ex}")

                # iii. Parse response
                stock_status = False
                low_price = None # Initialize low_price for this card check
                if response.status_code == 200:
                    # Check if the response body indicates stock and find lowest price.
                    # Example: Check if the response list is non-empty.
                    # API filters results based on query params (language, foil).
                    # We just need to check if the result list is non-empty.
                    try:
                        data = response.json()
                        # The API returns an object with blueprint_id as key and an array of listings as value
                        # Example for in-stock: {"42024":[{listing1}, {listing2}]}
                        # Example for out-of-stock: {"33922":[]}
                        str_blueprint_id = str(blueprint_id)
                        items = data.get(str_blueprint_id)

                        if isinstance(items, list) and len(items) > 0:
                            stock_status = True
                            # Find the lowest price in cents
                            min_price_cents = min(item['price_cents'] for item in items if 'price_cents' in item)
                            low_price = min_price_cents # Store as integer (cents)
                            logging.info(f"API success for blueprint {blueprint_id} with params {api_params}. Stock found: {stock_status}, Lowest Price (cents): {low_price}")
                        else:
                            # Stock is false if key exists but list is empty, or key doesn't exist
                            stock_status = False
                            low_price = None
                            logging.info(f"API success for blueprint {blueprint_id} with params {api_params}. Stock found: {stock_status} (Empty list or key missing)")

                    except ValueError: # Includes JSONDecodeError
                         logging.error(f"Failed to decode JSON response for blueprint {blueprint_id} with params {api_params}. URL: {response.url}, Status: {response.status_code}")
                         stock_status = False
                         low_price = None
                    except Exception as parse_e:
                         logging.error(f"Error processing Cardtrader response for blueprint {blueprint_id} with params {api_params}: {parse_e}")
                         stock_status = False
                         low_price = None
                elif response.status_code == 404:
                     # 404 likely means no items match the specific query (blueprint_id + lang + foil)
                     logging.info(f"Cardtrader API returned 404 (Not Found) for blueprint {blueprint_id} with params {api_params}. Assuming out of stock. URL: {response.url}")
                     stock_status = False
                     low_price = None
                elif response.status_code == 429:
                    logging.error(f"Cardtrader API rate limit hit (429) for blueprint {blueprint_id} with params {api_params}. Stopping check for this user.")
                    # Optionally break the loop or implement backoff
                    break # Stop processing this user for now
                else:
                    logging.error(f"Cardtrader API error for blueprint {blueprint_id} with params {api_params}. Status: {response.status_code}, Response: {response.text[:200]}")
                    stock_status = False
                    low_price = None # Ensure price is None on error

                # iv. Update card entity if status or price changed
                current_stock = card.get('cardtrader_stock')
                current_price = card.get('cardtrader_low_price') # Can be None
                current_blueprint_id = card.get('cardtrader_id') # Get current ID (might be None if just added)

                # Ensure low_price from API is int or None for comparison
                # Azure Table Storage might return price as float if previously stored that way, handle it.
                if isinstance(current_price, float):
                    current_price = int(current_price) # Convert potential float from storage to int

                # Check if stock, price, OR blueprint ID needs updating
                needs_update = (
                    current_stock != stock_status or
                    current_price != low_price or
                    current_blueprint_id != blueprint_id # Compare stored ID with the one we just found/confirmed
                )

                if needs_update:
                    card['cardtrader_stock'] = stock_status
                    card['cardtrader_low_price'] = low_price # Store as int (cents) or None
                    # card['cardtrader_id'] = blueprint_id # Already set above when blueprint_id was found
                    try:
                        user_table_client.update_entity(entity=card, mode=UpdateMode.MERGE)
                        logging.info(f"Updated card {card_name} ({card_pk}/{card_rk}) to stock={stock_status}, price={low_price}, cardtrader_id={blueprint_id}")
                        updated_count += 1
                    except Exception as update_e:
                         logging.error(f"Failed to update stock/price/id for {card_name} ({card_pk}/{card_rk}): {update_e}")

            except requests.exceptions.RequestException as req_e:
                logging.error(f"Network error calling Cardtrader API for blueprint {blueprint_id} with params {api_params}: {req_e}")
                # Decide whether to continue or stop for this user
                continue # Skip this card on network error
            except Exception as api_e:
                logging.error(f"Unexpected error during API check for blueprint {blueprint_id} with params {api_params}: {api_e}")
                continue # Skip this card

    # 12. Update timestamp for the checked user
    try:
        timestamp_entity = {
            'PartitionKey': user_id_to_check,
            'RowKey': 'Timestamp', # Fixed RowKey
            'CardtraderLastChecked': now_utc.isoformat()
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
