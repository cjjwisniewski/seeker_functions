import logging
import azure.functions as func
import json
import os
from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# --- Configuration ---
# Load admin IDs from environment variable (comma-separated)
ADMIN_USER_IDS_STR = os.environ.get("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = {admin_id.strip() for admin_id in ADMIN_USER_IDS_STR.split(',') if admin_id.strip()}
logging.info(f"Loaded {len(ADMIN_USER_IDS)} admin user IDs.")

# Connection string for Azure Table Storage
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
if not AZURE_STORAGE_CONNECTION_STRING:
    logging.error("FATAL: AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
    # In a real scenario, you might want the function to fail hard here.
    # For now, we'll let it proceed and fail later if needed.


def is_admin(user_id):
    """Checks if the given user_id is in the configured admin list."""
    return user_id in ADMIN_USER_IDS

def add_cors_headers(response, origin):
    """Adds CORS headers to the response."""
    # Define allowed origins (consider making this an environment variable too)
    allowed_origins = ['http://localhost:5173', 'https://seeker.cityoftraitors.com'] # Replace with your actual frontend URLs
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    else:
        # Optionally log origins that are not allowed but attempting access
        logging.debug(f"Origin '{origin}' not in allowed list.")
        # Do not add Allow-Origin header if origin is not allowed

    # Allow methods used by frontend + OPTIONS
    response.headers['Access-Control-Allow-Methods'] = 'DELETE, OPTIONS'
    # Allow headers sent by frontend + standard headers
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization' # Authorization is key now
    return response

# --- Main Function Logic ---
def main(req: func.HttpRequest) -> func.HttpResponse:
    origin = req.headers.get('Origin', '')

    # Handle CORS Preflight request for DELETE
    if req.method == "OPTIONS":
        logging.info("Handling OPTIONS preflight request for DELETE.")
        # Preflight should return 204 No Content for successful checks
        response = func.HttpResponse(status_code=204)
        # Crucially add headers to the preflight response too
        return add_cors_headers(response, origin)

    # Expect DELETE method for actual operation
    if req.method != "DELETE":
        logging.warning(f"Received unexpected method: {req.method}")
        response = func.HttpResponse("Method Not Allowed", status_code=405)
        return add_cors_headers(response, origin)

    logging.info(f"DeleteUserAccount function triggered by {req.method} request.")

    # --- Authentication ---
    # Get the authenticated user's ID (injected by APIM/App Service Auth from Bearer token)
    # Ensure your API Gateway/Auth Service is configured to populate this header.
    authenticated_user_id = req.headers.get('x-ms-client-principal-id')
    if not authenticated_user_id:
        logging.warning("Request received without 'x-ms-client-principal-id' header. Authentication missing or misconfigured.")
        response = func.HttpResponse("Unauthorized: Missing user principal ID.", status_code=401)
        return add_cors_headers(response, origin)

    logging.info(f"Authenticated user ID (from header): {authenticated_user_id}")

    # --- Determine Target User ID ---
    target_user_id_from_body = None
    req_body = None
    try:
        # Attempt to parse JSON body, but don't require it
        # Check if body exists and has content before trying to parse
        body_bytes = req.get_body()
        if body_bytes:
            req_body = json.loads(body_bytes)
            target_user_id_from_body = req_body.get('targetUserIdToDelete')
            if not isinstance(target_user_id_from_body, str) or not target_user_id_from_body.strip():
                 # Handle cases where the key exists but value is empty, null, or not a string
                 target_user_id_from_body = None
                 logging.debug("Request body had 'targetUserIdToDelete' but it was empty or not a string.")
            else:
                 target_user_id_from_body = target_user_id_from_body.strip() # Clean whitespace
                 logging.info(f"Found 'targetUserIdToDelete' in request body: {target_user_id_from_body}")
        else:
            logging.info("Request body is empty.")

    except json.JSONDecodeError:
        # Body existed but wasn't valid JSON. If an admin intended to delete someone, this is an error.
        # If a user was deleting self, this is maybe acceptable, but cleaner if body is empty or omitted.
        # We'll treat it as a Bad Request if JSON was expected but invalid.
        # Check if Content-Type header suggests JSON was intended.
        content_type = req.headers.get('Content-Type', '').lower()
        if 'application/json' in content_type:
             logging.warning("Could not parse request body as JSON, although Content-Type suggested JSON.")
             response = func.HttpResponse("Bad Request: Invalid JSON format.", status_code=400)
             return add_cors_headers(response, origin)
        else:
             # Content-Type wasn't JSON, or body was present but not JSON. Proceed as if no target was specified.
             logging.info("Request body was not valid JSON (or Content-Type wasn't JSON). Proceeding as self-delete.")
             target_user_id_from_body = None # Ensure it's None

    except Exception as e: # Catch other potential errors reading body
        logging.error(f"Error reading request body: {type(e).__name__}: {str(e)}", exc_info=True)
        response = func.HttpResponse("Internal server error reading request.", status_code=500)
        return add_cors_headers(response, origin)

    # --- Authorization & Target ID Finalization ---
    actual_target_id = None
    if target_user_id_from_body:
        # Scenario: Admin attempting to delete a specific user
        logging.info(f"Admin action detected: Caller '{authenticated_user_id}' attempts to delete target '{target_user_id_from_body}'.")
        if not is_admin(authenticated_user_id):
            logging.warning(f"Forbidden: Non-admin user '{authenticated_user_id}' attempted admin action (delete target '{target_user_id_from_body}').")
            response = func.HttpResponse("Forbidden: You do not have permission to delete other user accounts.", status_code=403)
            return add_cors_headers(response, origin)
        # Admin is authorized, set the actual target
        actual_target_id = target_user_id_from_body
        logging.info(f"Authorization successful: Admin '{authenticated_user_id}' proceeding to delete target '{actual_target_id}'.")
    else:
        # Scenario: User attempting to delete their own account
        logging.info(f"Self-delete action detected: User '{authenticated_user_id}' attempts to delete own account.")
        actual_target_id = authenticated_user_id
        # No specific admin check needed here, they are deleting themselves.
        logging.info(f"Authorization successful: User '{actual_target_id}' proceeding with self-deletion.")


    # --- Deletion Logic ---
    if not actual_target_id:
         # This should theoretically not happen if authentication worked, but good practice to check
         logging.error("Logic error: actual_target_id was not determined.")
         response = func.HttpResponse("Internal Server Error: Could not determine target for deletion.", status_code=500)
         return add_cors_headers(response, origin)

    if not AZURE_STORAGE_CONNECTION_STRING:
        logging.error("Cannot proceed with deletion: AZURE_STORAGE_CONNECTION_STRING is not configured.")
        response = func.HttpResponse("Internal Server Error: Storage configuration missing.", status_code=500)
        return add_cors_headers(response, origin)

    try:
        table_service_client = TableServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

        logging.info(f"Attempting to delete table: '{actual_target_id}'")
        table_service_client.delete_table(table_name=actual_target_id)
        logging.info(f"Successfully deleted table '{actual_target_id}'.")

        # --- TODO: Add deletion logic for any other user data associated with actual_target_id ---
        # --------------------------------------------------------------------------------------

        response = func.HttpResponse(
            json.dumps({"message": f"User account data for '{actual_target_id}' deleted successfully."}),
            mimetype="application/json",
            status_code=200 # Use 200 OK for successful deletion
            # Or use 204 No Content if you prefer not to send a body on success:
            # status_code=204, body=None, mimetype=None
        )
        return add_cors_headers(response, origin)

    except ResourceNotFoundError:
        # Table didn't exist - considered success for DELETE (idempotency)
        logging.warning(f"Table '{actual_target_id}' not found during deletion attempt (already deleted or never existed).")
        response = func.HttpResponse(
            json.dumps({"message": f"User account data for '{actual_target_id}' not found (already deleted or never existed)."}),
            mimetype="application/json",
            status_code=200 # Return 200 OK as the desired state (no data) is achieved
        )
        return add_cors_headers(response, origin)

    except HttpResponseError as hre:
        logging.error(f"Azure Storage Error deleting table '{actual_target_id}': Status={hre.status_code}, Code={hre.error_code}, Message={hre.message}", exc_info=True) # Log stack trace
        response = func.HttpResponse(
            json.dumps({"error": "Storage error during deletion.", "details": hre.message}),
            mimetype="application/json",
            status_code=hre.status_code if hre.status_code else 500
        )
        return add_cors_headers(response, origin)

    except Exception as e:
        logging.error(f"Unexpected error deleting account '{actual_target_id}': {type(e).__name__}: {str(e)}", exc_info=True)
        response = func.HttpResponse(
             json.dumps({"error": "Internal server error during deletion.", "details": f"{type(e).__name__}"}),
             mimetype="application/json",
             status_code=500
        )
        return add_cors_headers(response, origin)