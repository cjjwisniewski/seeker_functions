import logging
import azure.functions as func
import json
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError

def main(req: func.HttpRequest) -> func.HttpResponse:
    def add_cors_headers(response):
        allowed_origins = ['http://localhost:5173', 'https://seeker.cityoftraitors.com']
        origin = req.headers.get('Origin', '')
        if origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, x-ms-client-principal-id'
        return response

    if req.method == "OPTIONS":
        response = func.HttpResponse(status_code=200)
        return add_cors_headers(response)

    logging.info('GetSeekingList function triggered')

    user_id = req.headers.get('x-ms-client-principal-id')
    if not user_id:
        response = func.HttpResponse(
            "No user ID provided",
            status_code=400
        )
        return add_cors_headers(response)

    try:
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name=user_id)

        # Get all entities from the table
        entities = list(table_client.list_entities())
        
        # Convert entities to a list of dictionaries
        cards = [{
            'id': entity['id'],
            'name': entity['name'],
            'set_code': entity['set_code'],
            'collector_number': entity['collector_number'],
            'language': entity['language'],
            'finish': entity['finish'],
            'image_uri': entity['image_uri'],
            'stock': {
                'cardtrader': 'unknown',
                'tcgplayer': 'unknown',
                'cardmarket': 'unknown'
            }
        } for entity in entities]

        # Add debug logging
        logging.info('Retrieved cards from table:')
        for card in cards:
            logging.info(f"Card: {card['name']}, collector_number: {card.get('collector_number', 'NOT_FOUND')}")

        response = func.HttpResponse(
            json.dumps({"cards": cards}),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except ResourceNotFoundError:
        logging.info(f"Table not found for user: {user_id}")
        response = func.HttpResponse(
            json.dumps({"cards": []}),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error details: {type(e).__name__}: {str(e)}")
        response = func.HttpResponse(
            f"Internal server error: {type(e).__name__}: {str(e)}",
            status_code=500
        )
        return add_cors_headers(response)