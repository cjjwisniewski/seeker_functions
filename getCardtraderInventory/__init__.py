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
        
        # Connect to table storage
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name="inventorycardtrader")

        # Ensure table exists
        try:
            table_service.create_table(table_name="inventorycardtrader")
        except Exception:
            pass  # Table already exists

        # CardTrader API endpoint
        url = "https://api.cardtrader.com/api/v1/inventory"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }

        # Get inventory data (paginated)
        page = 1
        while True:
            response = requests.get(
                url,
                headers=headers,
                params={
                    "page": page,
                    "per_page": 100,  # Maximum allowed by API
                    "game": "magic"
                }
            )

            if response.status_code != 200:
                raise Exception(f"API request failed: {response.text}")

            data = response.json()
            cards = data.get("data", [])

            if not cards:
                break  # No more cards to process

            # Process each card and store in table
            for card in cards:
                entity = TableEntity(
                    PartitionKey=card["set_code"],
                    RowKey=f"{card['collector_number']}_{card['language']}_{card['finish']}",
                    id=card["id"],
                    name=card["name"],
                    set_code=card["set_code"],
                    collector_number=card["collector_number"],
                    language=card["language"],
                    finish=card["finish"],
                    price=card["price"],
                    quantity=card["quantity"],
                    condition=card["condition"],
                    last_updated=datetime.utcnow().isoformat()
                )

                table_client.upsert_entity(entity=entity)

            logging.info(f"Processed page {page}")
            
            # Check if there are more pages
            if not data.get("next_page_url"):
                break
                
            page += 1

        logging.info('Successfully updated CardTrader inventory')

    except Exception as e:
        logging.error(f"Error updating CardTrader inventory: {str(e)}")
        raise