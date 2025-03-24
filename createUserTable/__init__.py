import logging
import azure.functions as func
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Function triggered with headers: %s', dict(req.headers))

    user_id = req.headers.get('x-ms-client-principal-id')
    if not user_id:
        return func.HttpResponse(
            "No user ID provided",
            status_code=400
        )

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
            return func.HttpResponse(
                '{"message": "Table already exists"}',
                mimetype="application/json",
                status_code=200
            )
        except ResourceNotFoundError:
            logging.info(f"Creating new table: {table_name}")
            table_service.create_table(table_name)
            return func.HttpResponse(
                '{"message": "Table created successfully"}',
                mimetype="application/json",
                status_code=200
            )

    except Exception as e:
        logging.error(f"Error details: {type(e).__name__}: {str(e)}")
        return func.HttpResponse(
            f"Internal server error: {type(e).__name__}: {str(e)}",
            status_code=500
        )