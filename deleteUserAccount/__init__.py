import logging
import azure.functions as func
import json
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError

def main(req: func.HttpRequest) -> func.HttpResponse:
    def add_cors_headers(response):
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:5173'
        response.headers['Access-Control-Allow-Methods'] = 'DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, x-ms-client-principal-id'
        return response

    if req.method == "OPTIONS":
        response = func.HttpResponse(status_code=200)
        return add_cors_headers(response)

    logging.info('DeleteUserAccount function triggered')

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
        
        # Delete the user's table
        table_client = table_service.get_table_client(table_name=user_id)
        table_service.delete_table(table_name=user_id)
        
        logging.info(f"Successfully deleted table for user: {user_id}")
        
        response = func.HttpResponse(
            json.dumps({"message": "Account deleted successfully"}),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except ResourceNotFoundError:
        logging.info(f"Table not found for user: {user_id}")
        response = func.HttpResponse(
            json.dumps({"message": "Account already deleted or not found"}),
            status_code=404
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error details: {type(e).__name__}: {str(e)}")
        response = func.HttpResponse(
            f"Internal server error: {type(e).__name__}: {str(e)}",
            status_code=500
        )
        return add_cors_headers(response)