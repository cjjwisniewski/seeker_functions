import logging
import azure.functions as func
import requests
import os
from azure.data.tables import TableServiceClient, TableEntity
from datetime import datetime

def main(timer: func.TimerRequest) -> None:
    logging.info('GetCardtraderSets function triggered')

    try:
        # Get API credentials from environment variables
        api_key = os.environ["CARDTRADER_API_KEY"]
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        
        # Connect to table storage
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name="setscardtrader")

        # Ensure table exists
        try:
            table_service.create_table(table_name="setscardtrader")
            logging.info("Created sets table")
        except Exception:
            logging.info("Table exists")

        # CardTrader API endpoint
        url = "https://api.cardtrader.com/api/v2/expansions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }

        # Get sets data
        logging.info("Fetching MTG sets from CardTrader")
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            logging.error(f"API error {response.status_code}: {response.text}")
            raise Exception(f"API request failed: {response.text}")

        sets = response.json()
        total_sets = 0

        # Process each set
        for set_data in sets:
            try:
                # Filter for MTG sets only (game_id == 1)
                if set_data.get('game_id') != 1:
                    continue

                entity = TableEntity(
                    PartitionKey="mtg",
                    RowKey=set_data.get('code', '').lower(),
                    id=set_data.get('id'),
                    name=set_data.get('name'),
                    code=set_data.get('code'),
                    last_updated=datetime.utcnow().isoformat()
                )

                table_client.upsert_entity(entity=entity)
                total_sets += 1
                
            except Exception as set_error:
                logging.error(f"Error processing set {set_data.get('code')}: {str(set_error)}")
                continue

        logging.info(f'Updated sets table with {total_sets} MTG sets')

    except Exception as e:
        logging.error(f"Sets update failed: {str(e)}")
        raise