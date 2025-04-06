import logging
import azure.functions as func
import json
import os
from azure.data.tables import TableServiceClient

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

    try:
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        
        user_tables = []
        
        # List all tables and filter for those starting with 'user', excluding specific tables
        for table in table_service.list_tables():
            if table.name.startswith('user') and table.name != 'userCheckTimestamps':
                table_client = table_service.get_table_client(table.name)
                # Count items in table
                count = sum(1 for _ in table_client.list_entities())
                user_tables.append({
                    'userId': table.name,
                    'itemCount': count
                })

        response = func.HttpResponse(
            json.dumps(user_tables),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error getting user tables: {str(e)}")
        response = func.HttpResponse(
            json.dumps({
                "message": "Internal server error",
                "error": str(e)
            }),
            mimetype="application/json",
            status_code=500
        )
        return add_cors_headers(response)
