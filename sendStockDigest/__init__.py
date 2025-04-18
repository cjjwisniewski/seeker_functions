import logging
import azure.functions as func
import os
import datetime
import time
import requests
import pytz
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Constants
USER_TABLE_PREFIX = "user"
EXCLUDED_TABLES = {"userCheckTimestamps"} # Set of tables to ignore
MARKETPLACE_STOCK_FIELDS = [
    "cardmarket_stock",
    "cardtrader_stock",
    "ebay_stock",
    "tcgplayer_stock"
]

# Environment Variables
CONN_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
DISCORD_WEBHOOK_URL = os.environ.get("STOCK_DIGEST_DISCORD_WEBHOOK_URL") # Use a specific webhook URL for digests

def get_user_id_from_table_name(table_name):
    """Extracts user ID assuming table name format 'user<ID>'."""
    if table_name.startswith(USER_TABLE_PREFIX):
        return table_name[len(USER_TABLE_PREFIX):]
    return None

def get_marketplace_name(field_name):
    """Converts stock field name to a user-friendly marketplace name."""
    if field_name == "cardmarket_stock":
        return "Cardmarket"
    if field_name == "cardtrader_stock":
        return "Cardtrader"
    if field_name == "ebay_stock":
        return "eBay"
    if field_name == "tcgplayer_stock":
        return "TCGplayer"
    return field_name # Fallback

def main(timer: func.TimerRequest) -> None:
    start_time = time.time()
    now_utc = datetime.datetime.now(pytz.utc)
    logging.info(f'Python timer trigger function sendStockDigest ran at {now_utc.isoformat()}')

    if timer.past_due:
        logging.warning('The sendStockDigest timer is past due!')

    if not CONN_STRING:
        logging.error("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
        return
    if not DISCORD_WEBHOOK_URL:
        logging.error("STOCK_DIGEST_DISCORD_WEBHOOK_URL environment variable not set.")
        return

    try:
        table_service_client = TableServiceClient.from_connection_string(CONN_STRING)
    except Exception as e:
        logging.error(f"Failed to connect to Table Service: {e}")
        return

    user_tables_processed = 0
    digests_sent = 0

    try:
        all_tables = table_service_client.list_tables()
        user_table_names = [
            table.name for table in all_tables
            if table.name.startswith(USER_TABLE_PREFIX) and table.name not in EXCLUDED_TABLES
        ]
        logging.info(f"Found {len(user_table_names)} user tables to check for stock digests.")

    except Exception as e:
        logging.error(f"Failed to list tables: {e}")
        return

    for user_table_name in user_table_names:
        user_id = get_user_id_from_table_name(user_table_name)
        if not user_id:
            logging.warning(f"Could not extract user ID from table name '{user_table_name}'. Skipping.")
            continue

        logging.info(f"Processing table for user {user_id} ({user_table_name})...")
        user_tables_processed += 1
        in_stock_cards = []

        try:
            user_table_client = table_service_client.get_table_client(user_table_name)
            entities = user_table_client.list_entities()

            for card in entities:
                marketplaces_in_stock = []
                for field in MARKETPLACE_STOCK_FIELDS:
                    # Check if field exists and is explicitly True
                    if card.get(field) is True:
                        marketplaces_in_stock.append(get_marketplace_name(field))

                if marketplaces_in_stock:
                    in_stock_cards.append({
                        "name": card.get("name", "N/A"),
                        "set_code": card.get("PartitionKey", "N/A"),
                        "collector_number": card.get("collector_number", "N/A"),
                        "language": card.get("language", "N/A"),
                        "finish": card.get("finish", "N/A"),
                        "marketplaces": ", ".join(marketplaces_in_stock) # Comma-separated list
                    })

        except ResourceNotFoundError:
            logging.warning(f"User table {user_table_name} not found during processing. Skipping.")
            continue
        except Exception as e:
            logging.error(f"Failed to list or process entities for user table {user_table_name}: {e}")
            continue # Skip this user on error

        if not in_stock_cards:
            logging.info(f"No cards currently marked in stock for user {user_id}. No digest sent.")
            continue

        # Construct Discord Embed message
        # Embeds have limits: 25 fields, ~6000 chars total.
        user_ping = f"<@{user_id}>" # Keep ping outside the embed
        embed_fields = []
        max_fields = 25

        for i, card in enumerate(in_stock_cards):
            if len(embed_fields) >= max_fields -1: # Leave space for a potential truncation message field
                 embed_fields.append({
                     "name": "...",
                     "value": f"Message truncated. {len(in_stock_cards) - i} more items not shown.",
                     "inline": False
                 })
                 logging.warning(f"Stock digest embed for user {user_id} truncated due to field limit.")
                 break

            # Field Name: Card Name (Set #Num)
            field_name = f"{card['name']} ({card['set_code'].upper()} #{card['collector_number']})"
            # Field Value: [Lang/Finish] - Marketplaces: [List]
            field_value = (
                f"[{card['language']}/{card['finish']}]\n"
                f"Marketplaces: {card['marketplaces']}"
            )
            embed_fields.append({
                "name": field_name[:256], # Field name limit
                "value": field_value[:1024], # Field value limit
                "inline": False # Display each card as a separate block
            })


        # Assemble the embed object
        embed = {
            "title": "Seeker Stock Alert!",
            "description": f"Found {len(in_stock_cards)} item(s) in stock for you:",
            "color": 0x00ff00, # Green color, feel free to change (decimal format)
            "fields": embed_fields,
            "timestamp": now_utc.isoformat() # Add a timestamp
        }

        # Send to Discord Webhook
        try:
            # Payload includes the user ping in 'content' and the embed structure in 'embeds'
            payload = {
                "content": user_ping,
                "embeds": [embed] # Webhooks expect a list of embeds
            }
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            logging.info(f"Successfully sent stock digest embed to Discord for user {user_id}.")
            digests_sent += 1
            # Optional: Add a small delay if sending many webhooks rapidly
            # time.sleep(1)
        except requests.exceptions.RequestException as req_e:
            logging.error(f"Failed to send Discord webhook for user {user_id}: {req_e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred sending Discord webhook for user {user_id}: {e}")


    end_time = time.time()
    duration = end_time - start_time
    logging.info(f"sendStockDigest function finished. "
                 f"User tables processed: {user_tables_processed}, Digests sent: {digests_sent}. Duration: {duration:.2f} seconds.")
