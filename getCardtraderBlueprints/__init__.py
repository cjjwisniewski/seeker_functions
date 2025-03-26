import logging
import azure.functions as func
import requests
import os
import time
from azure.data.tables import TableServiceClient, TableEntity
from datetime import datetime

def main(timer: func.TimerRequest) -> None:
    logging.info('GetCardtraderBlueprints function triggered')

    try:
        # Get API credentials and connection string
        api_key = os.environ["CARDTRADER_API_KEY"]
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        
        # Connect to table storage
        table_service = TableServiceClient.from_connection_string(conn_string)
        sets_client = table_service.get_table_client(table_name="setscardtrader")
        blueprints_client = table_service.get_table_client(table_name="blueprintscardtrader")

        # Ensure blueprints table exists
        try:
            table_service.create_table(table_name="blueprintscardtrader")
            logging.info("Created blueprints table")
        except Exception:
            logging.info("Blueprints table exists")

        # Get all MTG sets from our sets table
        sets = list(sets_client.list_entities())
        total_blueprints = 0
        
        # Process each set
        for set_entity in sets:
            expansion_id = set_entity['id']
            set_code = set_entity['code']
            logging.info(f"Processing blueprints for set {set_code} (ID: {expansion_id})")

            # Rate limiting - 1 request per second
            time.sleep(1)
            
            # Get blueprints for this set
            url = f"https://api.cardtrader.com/api/v2/blueprints/export"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json"
            }
            params = {
                "expansion_id": expansion_id
            }
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                logging.error(f"API error for set {set_code}: {response.status_code}: {response.text}")
                continue

            blueprints = response.json()
            logging.info(f"Found {len(blueprints)} blueprints for set {set_code}")

            # Process each blueprint
            for blueprint in blueprints:
                try:
                    # Create entity
                    entity = TableEntity(
                        PartitionKey=set_code,
                        RowKey=str(blueprint.get('fixed_properties', {}).get('collector_number', '')),
                        id=blueprint.get('id'),
                        name=blueprint.get('name'),
                        rarity=blueprint.get('fixed_properties', {}).get('mtg_rarity'),
                        scryfall_id=blueprint.get('scryfall_id'),
                        image_url=blueprint.get('image_url'),
                        tcg_player_id=blueprint.get('tcg_player_id'),
                        card_market_ids=str(blueprint.get('card_market_ids', [])),
                        possible_languages=str([prop.get('possible_values', []) 
                                             for prop in blueprint.get('editable_properties', [])
                                             if prop.get('name') == 'mtg_language'][0]),
                        possible_conditions=str([prop.get('possible_values', []) 
                                              for prop in blueprint.get('editable_properties', [])
                                              if prop.get('name') == 'condition'][0]),
                        foil_available=str(any([prop.get('possible_values', [False]) 
                                             for prop in blueprint.get('editable_properties', [])
                                             if prop.get('name') == 'mtg_foil'])),
                        last_updated=datetime.utcnow().isoformat()
                    )

                    blueprints_client.upsert_entity(entity=entity)
                    total_blueprints += 1

                except Exception as blueprint_error:
                    logging.error(f"Error processing blueprint {blueprint.get('id')} from set {set_code}: {str(blueprint_error)}")
                    continue

            logging.info(f"Completed processing set {set_code}")

        logging.info(f'Updated blueprints table with {total_blueprints} total blueprints')

    except Exception as e:
        logging.error(f"Blueprint update failed: {str(e)}")
        raise