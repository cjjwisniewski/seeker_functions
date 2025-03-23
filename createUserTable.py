import logging
import azure.functions as func
from azure.data.tables import TableServiceClient, TableClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
import os

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    # Get the Discord user ID from the auth header
    discord_user_id = req.headers.get('x-ms-client-principal-id')
    if not discord_user_id:
        return func.HttpResponse(
            "Unauthorized - No user ID found",
            status_code=401
        )

    try:
        # Initialize the connection to Azure Table Storage
        connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(connection_string)
        table_name = f"user{discord_user_id}Cards"

        # Check if table exists
        try:
            table_client = table_service.get_table_client(table_name)
            # Try to query the table to verify it exists
            next(table_client.query_entities("", results_per_page=1), None)
            return func.HttpResponse(
                '{"message": "Table already exists"}',
                mimetype="application/json",
                status_code=200
            )
        except ResourceNotFoundError:
            # Table doesn't exist, create it
            table_service.create_table(table_name)
            return func.HttpResponse(
                '{"message": "Table created successfully"}',
                mimetype="application/json",
                status_code=200
            )

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(
            f"Internal server error: {str(e)}",
            status_code=500
        )