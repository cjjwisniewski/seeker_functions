import logging
import azure.functions as func
import requests
import os
from azure.data.tables import TableServiceClient, TableEntity
from datetime import datetime

def main(timer: func.TimerRequest) -> None:
    logging.info('GetCardtraderInventory function triggered')

    try:
        # Get API credentials from environment variables
        api_key = os.environ["CARDTRADER_API_KEY"]
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        
        # Add debug logging
        logging.info(f"Connection string present: {bool(conn_string)}")
        logging.info(f"Connection string length: {len(conn_string)}")
        
        # Connect to table storage
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name="inventorycardtrader")

        # Ensure table exists
        try:
            table_service.create_table(table_name="inventorycardtrader")
            logging.info("Created inventory table")
        except Exception:
            logging.info("Table exists")

        # CardTrader API endpoint
        url = "https://api.cardtrader.com/api/v2/inventory"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }

        # Get inventory data (paginated)
        page = 1
        total_cards = 0

        while True:
            logging.info(f"Fetching page {page}")
            response = requests.get(
                url,
                headers=headers,
                params={
                    "page": page,
                    "per_page": 100,  # Maximum allowed by API
                    "game": "mtg",    # Updated from 'magic' to 'mtg'
                    "status": "selling",
                    "include": "card"  # Include full card details
                }
            )

            if response.status_code != 200:
                logging.error(f"API error {response.status_code}: {response.text}")
                raise Exception(f"API request failed: {response.text}")

            data = response.json()
            cards = data.get("data", [])
            
            if not cards:
                logging.info("No more cards found")
                break

            # Process each card
            for card in cards:
                try:
                    card_data = card.get("card", {})
                    entity = TableEntity(
                        PartitionKey=card_data.get("set", "unknown"),
                        RowKey=f"{card_data.get('collector_number', '0')}_{card.get('language', 'unknown')}_{card.get('finish', 'normal')}",
                        id=card.get("id"),
                        name=card_data.get("name"),
                        set_code=card_data.get("set"),
                        collector_number=card_data.get("collector_number"),
                        language=card.get("language"),
                        finish=card.get("finish", "normal"),
                        price=card.get("price"),
                        quantity=card.get("quantity"),
                        condition=card.get("condition"),
                        last_updated=datetime.utcnow().isoformat()
                    )

                    table_client.upsert_entity(entity=entity)
                    total_cards += 1
                    
                except Exception as card_error:
                    logging.error(f"Error processing card {card.get('id')}: {str(card_error)}")
                    continue

            logging.info(f"Processed {len(cards)} cards from page {page}")
            
            if not data.get("next_page_url"):
                logging.info("No more pages available")
                break
                
            page += 1

        logging.info(f'Updated inventory with {total_cards} total cards')

    except Exception as e:
        logging.error(f"Inventory update failed: {str(e)}")
        raise