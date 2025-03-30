import logging
import azure.functions as func
import requests
import os
from azure.data.tables import TableServiceClient, TableEntity
from datetime import datetime, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def create_session():
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def get_next_set(sets_client):
    """Get the next set to process based on blueprints_last_updated timestamp"""
    sets = list(sets_client.list_entities())
    
    # First priority: Sets without a blueprints_last_updated timestamp
    new_sets = [s for s in sets if 'blueprints_last_updated' not in s]
    if new_sets:
        return new_sets[0]
    
    # Second priority: Set with oldest timestamp
    return min(sets, key=lambda x: x.get('blueprints_last_updated', '9999-12-31'))

def get_unique_row_key(blueprint):
    """Create unique row key from blueprint data"""
    collector_number = str(blueprint.get('fixed_properties', {}).get('collector_number', ''))
    blueprint_id = str(blueprint.get('id', ''))
    # Combine collector number and blueprint ID to ensure uniqueness
    return f"{collector_number}_{blueprint_id}"

def process_blueprint(blueprint, set_code):
    """Process a single blueprint and return entity"""
    # Extract editable properties safely
    editable_props = blueprint.get('editable_properties', [])
    languages = next((prop.get('possible_values', []) 
                     for prop in editable_props 
                     if prop.get('name') == 'mtg_language'), [])
    conditions = next((prop.get('possible_values', []) 
                      for prop in editable_props 
                      if prop.get('name') == 'condition'), [])
    foil = next((prop.get('possible_values', [False]) 
                 for prop in editable_props 
                 if prop.get('name') == 'mtg_foil'), [False])

    entity = TableEntity(
        PartitionKey=set_code,
        RowKey=str(blueprint.get('id')),
        id=blueprint.get('id'),
        name=blueprint.get('name'),
        collector_number=blueprint.get('fixed_properties', {}).get('collector_number'),
        rarity=blueprint.get('fixed_properties', {}).get('mtg_rarity'),
        scryfall_id=blueprint.get('scryfall_id'),
        image_url=blueprint.get('image_url'),
        tcg_player_id=blueprint.get('tcg_player_id'),
        card_market_ids=str(blueprint.get('card_market_ids', [])),
        possible_languages=str(languages),
        possible_conditions=str(conditions),
        foil_available=str(any(foil)),
        last_updated=datetime.utcnow().isoformat()
    )
    
    return entity

def main(timer: func.TimerRequest) -> None:
    logging.info('GetCardtraderBlueprints timer trigger function ran')

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

        # Get single set to process
        set_entity = get_next_set(sets_client)
        expansion_id = set_entity['id']
        set_code = set_entity['code']
        
        logging.info(f"Processing blueprints for set {set_code} (ID: {expansion_id})")
        
        session = create_session()
        batch_size = 100
        batch_operations = []
        total_blueprints = 0

        # Get blueprints for this set
        response = session.get(
            "https://api.cardtrader.com/api/v2/blueprints/export",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json"
            },
            params={"expansion_id": expansion_id}
        )
        
        if response.status_code != 200:
            logging.error(f"API error for {set_code}: {response.status_code}")
            return

        blueprints = response.json()
        logging.info(f"Found {len(blueprints)} blueprints for set {set_code}")

        # Process each blueprint
        for blueprint in blueprints:
            try:
                entity = process_blueprint(blueprint, set_code)
                batch_operations.append(('upsert', entity))
                total_blueprints += 1

                if len(batch_operations) >= batch_size:
                    blueprints_client.submit_transaction(batch_operations)
                    logging.info(f"Committed batch of {len(batch_operations)} blueprints")
                    batch_operations = []

            except Exception as blueprint_error:
                logging.error(f"Failed blueprint {blueprint.get('id')} in {set_code}: {str(blueprint_error)}")
                continue

        # Commit any remaining blueprints
        if batch_operations:
            blueprints_client.submit_transaction(batch_operations)

        # Update set's blueprints_last_updated timestamp
        set_entity['blueprints_last_updated'] = datetime.now(timezone.utc).isoformat()
        sets_client.update_entity(set_entity)

        logging.info(f'Success: Processed set {set_code} with {total_blueprints} blueprints')

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise