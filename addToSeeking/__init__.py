import logging
import azure.functions as func
import json
from azure.data.tables import TableServiceClient, TableEntity
from azure.core.exceptions import ResourceExistsError
import os

def main(req: func.HttpRequest) -> func.HttpResponse:
    def add_cors_headers(response):
        allowed_origins = ['http://localhost:5173', 'https://seeker.cityoftraitors.com']
        origin = req.headers.get('Origin', '')
        if origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, x-ms-client-principal-id'
        return response

    # Handle OPTIONS request for CORS preflight
    if req.method == "OPTIONS":
        response = func.HttpResponse(status_code=200)
        return add_cors_headers(response)

    logging.info('AddToSeeking function triggered')

    # Get user ID from header
    user_id = req.headers.get('x-ms-client-principal-id')
    if not user_id:
        response = func.HttpResponse(
            "No user ID provided",
            status_code=400
        )
        return add_cors_headers(response)

    try:
        # Get request body
        req_body = req.get_json()
        
        # Validate required fields
        required_fields = ['id', 'name', 'set_code', 'collector_number', 'language', 'oracle_id', 'image_uri', 'timestamp', 'finish']
        for field in required_fields:
            if field not in req_body:
                response = func.HttpResponse(
                    f"Missing required field: {field}",
                    status_code=400
                )
                return add_cors_headers(response)

        # Connect to table storage
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name=user_id)

        # Create entity using finish from req_body
        entity = TableEntity(
            PartitionKey=req_body['set_code'],
            RowKey=f"{req_body['collector_number']}_{req_body['language']}_{req_body['finish']}",
            id=req_body['id'],
            name=req_body['name'],
            set_code=req_body['set_code'],
            collector_number=req_body['collector_number'],
            language=req_body['language'],
            oracle_id=req_body['oracle_id'],
            image_uri=req_body['image_uri'],
            timestamp=req_body['timestamp'],
            finish=req_body['finish'],
            cardtrader_stock=False,
            tcgplayer_stock=False,
            cardmarket_stock=False,
            ebay_stock=False,
        )

        table_client.create_entity(entity=entity)
        response = func.HttpResponse(
            json.dumps({
                "message": "Card added to seeking list successfully",
                "id": req_body['id']
            }),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except ResourceExistsError:
        logging.info(f"Card already exists in seeking list for user: {user_id}")
        response = func.HttpResponse(
            json.dumps({
                "message": "Card already exists in seeking list",
                "error": "ALREADY_EXISTS"
            }),
            mimetype="application/json",
            status_code=409
        )
        return add_cors_headers(response)

    except ValueError as ve:
        logging.error(f"Invalid request body: {str(ve)}")
        response = func.HttpResponse(
            "Invalid request body",
            status_code=400
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error details: {type(e).__name__}: {str(e)}")
        response = func.HttpResponse(
            json.dumps({
                "message": "Internal server error",
                "error": f"{type(e).__name__}: {str(e)}"
            }),
            mimetype="application/json",
            status_code=500
        )
        return add_cors_headers(response)