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
        response.headers['Access-Control-Allow-Methods'] = 'DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, x-ms-client-principal-id'
        return response

    if req.method == "OPTIONS":
        response = func.HttpResponse(status_code=200)
        return add_cors_headers(response)

    logging.info('DeleteFromSeeking function triggered')

    user_id = req.headers.get('x-ms-client-principal-id')
    if not user_id:
        response = func.HttpResponse(
            "No user ID provided",
            status_code=400
        )
        return add_cors_headers(response)

    try:
        # Get and validate request body
        req_body = req.get_json()
        logging.info(f"Request body: {req_body}")
        
        if not req_body or 'partitionKey' not in req_body or 'rowKey' not in req_body:
            response = func.HttpResponse(
                "Missing required fields: partitionKey and rowKey",
                status_code=400
            )
            return add_cors_headers(response)

        # Connect to table storage
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_client = table_service.get_table_client(table_name=user_id)

        # Delete the entity
        try:
            logging.info(f"Attempting to delete entity with PartitionKey: {req_body['partitionKey']} and RowKey: {req_body['rowKey']}")
            table_client.delete_entity(
                partition_key=req_body['partitionKey'],
                row_key=req_body['rowKey']
            )
            
            response = func.HttpResponse(
                json.dumps({"message": "Card deleted successfully"}),
                mimetype="application/json",
                status_code=200
            )
            return add_cors_headers(response)
            
        except ResourceNotFoundError as rnf:
            logging.error(f"Entity not found: {str(rnf)}")
            response = func.HttpResponse(
                "Entity not found",
                status_code=404
            )
            return add_cors_headers(response)

    except ValueError as ve:
        logging.error(f"Invalid request body: {str(ve)}")
        response = func.HttpResponse(
            "Invalid request body format",
            status_code=400
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error details: {type(e).__name__}: {str(e)}")
        response = func.HttpResponse(
            f"Internal server error: {type(e).__name__}: {str(e)}",
            status_code=500
        )
        return add_cors_headers(response)