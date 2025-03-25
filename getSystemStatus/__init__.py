import logging
import azure.functions as func
import json
import requests
from datetime import datetime

def main(req: func.HttpRequest) -> func.HttpResponse:
    def add_cors_headers(response):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    if req.method == "OPTIONS":
        response = func.HttpResponse(status_code=200)
        return add_cors_headers(response)

    try:
        # Check function app availability by making a simple request
        function_app_url = "https://seeker-functions.azurewebsites.net"
        response = requests.get(function_app_url)
        
        # Determine status based on response
        is_running = response.status_code == 200
        
        status = {
            "state": "running" if is_running else "stopped",
            "availability": "normal" if is_running else "limited",
            "last_modified": datetime.utcnow().isoformat(),
            "host_names": ["seeker-functions.azurewebsites.net"],
            "metrics": [
                {
                    "name": "Response Time",
                    "value": f"{response.elapsed.total_seconds() * 1000:.0f}ms"
                },
                {
                    "name": "Status Code",
                    "value": response.status_code
                }
            ]
        }

        response = func.HttpResponse(
            json.dumps(status),
            mimetype="application/json",
            status_code=200
        )
        return add_cors_headers(response)

    except Exception as e:
        logging.error(f"Error getting system status: {str(e)}")
        status = {
            "state": "unknown",
            "availability": "unknown",
            "last_modified": datetime.utcnow().isoformat(),
            "host_names": ["seeker-functions.azurewebsites.net"],
            "metrics": [],
            "error": str(e)
        }
        
        response = func.HttpResponse(
            json.dumps(status),
            mimetype="application/json",
            status_code=200  # Return 200 even on error to show status in UI
        )
        return add_cors_headers(response)