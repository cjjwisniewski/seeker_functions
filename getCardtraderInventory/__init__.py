import logging
import azure.functions as func
import requests
import os
import time
from azure.data.tables import TableServiceClient, TableEntity
from datetime import datetime

def main(timer: func.TimerRequest) -> None:
    logging.info('GetCardtraderInventory function triggered')

    try:
        # Get API credentials and connection string
        api_key = os.environ["CARDTRADER_API_KEY"]
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        
        # Connect to table storage
        table_service = TableServiceClient.from_connection_string(conn_string)
        inventory_client = table_service.get_table_client(table_name="inventorycardtrader")
        sets_client = table_service.get_table_client(table_name="setscardtrader")

        # Ensure inventory table exists
        try:
            table_service.create_table(table_name="inventorycardtrader")
            logging.info("Created inventory table")
        except Exception:
            logging.info("Inventory table exists")

        # Get all MTG sets from our sets table
        sets = list(sets_client.list_entities())
        total_cards = 0
        
        # CardTrader API endpoint
        url = "https://api.cardtrader.com/api/v2/marketplace/products"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }

        # Languages to fetch (2-letter country codes)
        languages = ['en', 'de', 'fr', 'it', 'es', 'pt', 'ja', 'ko', 'ru', 'zhs', 'zht']
        
        # Process each set
        for set_entity in sets:
            expansion_id = set_entity['id']
            set_code = set_entity['code']
            logging.info(f"Processing set {set_code} (ID: {expansion_id})")
            
            # Track cards seen in this run
            current_cards = set()
            
            # Process each language
            for lang in languages:
                page = 1
                while True:
                    # Rate limiting - 1 request per second (as per API docs)
                    time.sleep(1)
                    
                    logging.info(f"Fetching page {page} for set {set_code} in {lang}")
                    response = requests.get(
                        url,
                        headers=headers,
                        params={
                            "expansion_id": expansion_id,
                            "page": page,
                            "per_page": 100,
                            "language": lang
                        }
                    )

                    if response.status_code != 200:
                        logging.error(f"API error for set {set_code} {lang}: {response.status_code}: {response.text}")
                        break

                    data = response.json()
                    # Add debug logging for API response
                    logging.info(f"Response for {set_code} {lang}: Found {len(data.get('data', []))} products")
                    
                    cards = data.get("data", [])
                    if not cards:
                        logging.info(f"No more cards found for set {set_code} in {lang}")
                        break

                    # Process each card
                    for card in cards:
                        try:
                            # Create row key from product properties
                            row_key = f"{card.get('blueprint_id', '0')}_{lang}_{card.get('properties_hash', {}).get('finish', 'normal')}"
                            
                            # Add to seen cards set
                            current_cards.add(row_key)

                            entity = TableEntity(
                                PartitionKey=set_code,
                                RowKey=row_key,
                                id=card.get("id"),
                                name=card.get("name_en"),
                                set_code=set_code,
                                blueprint_id=card.get("blueprint_id"),
                                language=lang,
                                finish=card.get("properties_hash", {}).get("finish", "normal"),
                                price_cents=card.get("price", {}).get("cents"),
                                price_currency=card.get("price", {}).get("currency"),
                                quantity=card.get("quantity"),
                                condition=card.get("properties_hash", {}).get("condition"),
                                seller_id=card.get("user", {}).get("id"),
                                seller_name=card.get("user", {}).get("username"),
                                last_updated=datetime.utcnow().isoformat()
                            )

                            try:
                                inventory_client.upsert_entity(entity=entity)
                                total_cards += 1
                                logging.info(f"Updated/inserted card: {entity['name']} ({entity['RowKey']}) in set {set_code}")
                            except Exception as table_error:
                                logging.error(f"Failed to update table for card {entity['name']}: {str(table_error)}")
                                continue
                        
                        except Exception as card_error:
                            logging.error(f"Error processing card {card.get('id')} from set {set_code}: {str(card_error)}")
                            continue

                    logging.info(f"Processed {len(cards)} cards from page {page} for set {set_code} in {lang}")
                    
                    if not data.get("next_page_url"):
                        logging.info(f"No more pages for set {set_code} in {lang}")
                        break
                        
                    page += 1

            # After processing all pages, remove cards no longer available
            query_filter = f"PartitionKey eq '{set_code}'"
            existing_cards = inventory_client.query_entities(query_filter)
            
            for existing_card in existing_cards:
                if existing_card['RowKey'] not in current_cards:
                    inventory_client.delete_entity(
                        partition_key=set_code,
                        row_key=existing_card['RowKey']
                    )
                    logging.info(f"Removed unavailable card: {existing_card['name']} ({existing_card['RowKey']}) from set {set_code}")

            logging.info(f"Completed processing set {set_code}")

        logging.info(f'Updated inventory with {total_cards} total cards across all sets')

    except Exception as e:
        logging.error(f"Inventory update failed: {str(e)}")
        raise