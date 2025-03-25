import logging
import azure.functions as func
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError

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

    logging.info('Function triggered with headers: %s', dict(req.headers))

    user_id = req.headers.get('x-ms-client-principal-id')
    if not user_id:
        response = func.HttpResponse(
            "No user ID provided",
            status_code=400
        )
        return add_cors_headers(response)

    try:
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        logging.info("Creating TableServiceClient with connection string")
        
        table_service = TableServiceClient.from_connection_string(conn_string)
        table_name = f"{user_id}"
        logging.info(f"Working with table: {table_name}")

        try:
            table_client = table_service.get_table_client(table_name)
            logging.info("Checking if table exists...")
            next(table_client.query_entities("", results_per_page=1), None)
            response = func.HttpResponse(
                '{"message": "Table already exists"}',
                mimetype="application/json",
                status_code=200
            )
            return add_cors_headers(response)
        except ResourceNotFoundError:
            logging.info(f"Creating new table: {table_name}")
            table_service.create_table(table_name)
            response = func.HttpResponse(
                '{"message": "Table created successfully"}',
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