import logging
import azure.functions as func
import json
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError # Import HttpResponseError

# --- Configuration ---
# Load admin IDs from environment variable (comma-separated)
# Example: ADMIN_USER_IDS="user12345,user67890"
ADMIN_USER_IDS_STR = os.environ.get("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = {admin_id.strip() for admin_id in ADMIN_USER_IDS_STR.split(',') if admin_id.strip()}
logging.info(f"Loaded {len(ADMIN_USER_IDS)} admin user IDs.")

def is_admin(user_id):
    """Checks if the given user_id is in the configured admin list."""
    return user_id in ADMIN_USER_IDS

def add_cors_headers(response, origin):
    """Adds CORS headers to the response."""
    # Allowed origins for CORS
    allowed_origins = ['http://localhost:5173', 'https://seeker.cityoftraitors.com']
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        # Allow credentials (cookies, authorization headers)
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    # Allow methods used by frontend + OPTIONS
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    # Allow headers sent by frontend (including Authorization) + standard headers
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, x-ms-client-principal-id' # Added Authorization
    return response

def main(req: func.HttpRequest) -> func.HttpResponse:
    origin = req.headers.get('Origin', '') # Get origin for CORS responses

    # Handle CORS Preflight request
    if req.method == "OPTIONS":
        logging.info("Handling OPTIONS preflight request.")
        response = func.HttpResponse(status_code=204) # Use 204 No Content for preflight success
        return add_cors_headers(response, origin)

    logging.info(f"GetSeekingList function triggered by {req.method} request.")

    # --- Authentication & User Identification ---
    authenticated_user_id = req.headers.get('x-ms-client-principal-id')
    if not authenticated_user_id:
        logging.warning("Request received without x-ms-client-principal-id header.")
        response = func.HttpResponse("Unauthorized: No user principal ID provided.", status_code=401)
        # Even errors need CORS headers for the browser to read the response
        return add_cors_headers(response, origin)

    logging.info(f"Authenticated user ID (from header): {authenticated_user_id}")

    # Check for target user ID (Admin scenario)
    target_user_id = req.params.get('targetUserId')
    effective_user_id = None
    is_admin_request = False

    if target_user_id:
        logging.info(f"Target user ID specified in query parameter: {target_user_id}")
        # --- Authorization Check ---
        if not is_admin(authenticated_user_id):
            logging.warning(f"Forbidden: User {authenticated_user_id} is not an admin but attempted to access target {target_user_id}.")
            response = func.HttpResponse("Forbidden: You do not have permission to view this user's data.", status_code=403)
            return add_cors_headers(response, origin)

        # Admin is authorized, set the effective ID to the target
        effective_user_id = target_user_id
        is_admin_request = True
        logging.info(f"Admin request authorized. Effective user ID set to target: {effective_user_id}")
    else:
        # Regular user scenario: fetch own data
        effective_user_id = authenticated_user_id
        logging.info(f"Regular user request. Effective user ID set to authenticated user: {effective_user_id}")

    if not effective_user_id:
         # This case should ideally not be reached if logic above is sound
         logging.error("Effective user ID could not be determined.")
         response = func.HttpResponse("Bad Request: Could not determine target user.", status_code=400)
         return add_cors_headers(response, origin)

    # --- Data Fetching ---
    try:
        conn_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        table_service = TableServiceClient.from_connection_string(conn_string)
        # Use the determined effective_user_id as the table name
        table_client = table_service.get_table_client(table_name=effective_user_id)

        logging.info(f"Querying table: {effective_user_id}")
        entities = list(table_client.list_entities())
        logging.info(f"Retrieved {len(entities)} entities from table {effective_user_id}.")

        # Convert entities to the expected format
        cards = [{
            'id': entity.get('RowKey', f"{entity.get('set_code', '')}_{entity.get('collector_number', '')}"), # Use RowKey or fallback if 'id' field missing
            'name': entity.get('name', 'N/A'),
            'set_code': entity.get('set_code', 'N/A'),
            'collector_number': entity.get('collector_number', 'N/A'),
            'language': entity.get('language', 'N/A'),
            'finish': entity.get('finish', 'N/A'),
            'image_uri': entity.get('image_uri', ''),
            # Handle potential non-boolean string values if needed before comparing
            'cardtrader_stock': str(entity.get('cardtrader_stock', 'unknown')).lower() == 'true',
            'tcgplayer_stock': str(entity.get('tcgplayer_stock', 'unknown')).lower() == 'true',
            'cardmarket_stock': str(entity.get('cardmarket_stock', 'unknown')).lower() == 'true',
            'ebay_stock': str(entity.get('ebay_stock', 'unknown')).lower() == 'true',
        } for entity in entities]

        # Optional: Log retrieved card names for debugging
        # if cards:
        #     logging.info(f"First few card names: {[card['name'] for card in cards[:3]]}")

        response = func.HttpResponse(
            json.dumps({"cards": cards}),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response, origin)

    except ResourceNotFoundError:
        # Table doesn't exist for the effective_user_id
        logging.warning(f"Table not found for user: {effective_user_id}. Returning empty list.")
        response = func.HttpResponse(
            json.dumps({"cards": []}), # Return empty list as per original behavior
            mimetype="application/json",
            status_code=200 # Keep 200 OK for consistency, even if table missing
        )
        return add_cors_headers(response, origin)

    except HttpResponseError as hre:
        # Catch specific errors from Azure SDK, like auth errors to storage etc.
        logging.error(f"Azure Storage Error accessing table {effective_user_id}: Status={hre.status_code}, Code={hre.error_code}, Message={hre.message}, Details={str(hre)}")
        response = func.HttpResponse(
            f"Internal server error accessing data: {hre.message}",
            status_code=hre.status_code if hre.status_code else 500
        )
        return add_cors_headers(response, origin)

    except Exception as e:
        # Catch any other unexpected errors
        logging.error(f"Unexpected error processing request for {effective_user_id}: {type(e).__name__}: {str(e)}", exc_info=True) # Log stack trace
        response = func.HttpResponse(
            f"Internal server error: {type(e).__name__}: {str(e)}",
            status_code=500
        )
        return add_cors_headers(response, origin)