import azure.functions as func
import json
import pytest
from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableServiceClient, TableEntity
from unittest.mock import MagicMock, patch
import sys
import os

# Add the parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from addToSeeking import main

@pytest.fixture(autouse=True)
def mock_env_vars():
    with patch.dict(os.environ, {'AZURE_STORAGE_CONNECTION_STRING': 'dummy_connection_string'}):
        yield

def get_mock_request(user_id="user123", include_body=True):
    mock_req = MagicMock()
    mock_req.method = "POST"
    mock_req.headers = {"x-ms-client-principal-id": user_id}
    
    if include_body:
        mock_req.get_json.return_value = {
            "id": "test-id",
            "name": "Test Card",
            "set_code": "test",
            "collector_number": "123",
            "language": "en",
            "oracle_id": "test-oracle",
            "image_uri": "test-uri",
            "timestamp": "2024-03-24T12:00:00Z",
            "finish": "nonfoil"
        }
    return mock_req

def test_add_to_seeking_success():
    mock_req = get_mock_request()
    mock_req.method = "POST"
    
    with patch('azure.data.tables.TableServiceClient', autospec=True) as MockTableServiceClient:
        # Create mock instances
        mock_table_client = MagicMock()
        mock_service_client = MagicMock()
        
        # Set up the chain of mocks properly
        MockTableServiceClient.from_connection_string.return_value = mock_service_client
        mock_service_client.get_table_client.return_value = mock_table_client
        
        # Ensure the connection string is properly mocked
        with patch.dict('os.environ', {'AZURE_STORAGE_CONNECTION_STRING': 'DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net'}):
            response = main(mock_req)
        
        # Verify the response
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["message"] == "Card added to seeking list successfully"
        assert body["id"] == "test-id"
        assert "Access-Control-Allow-Origin" in response.headers

def test_add_to_seeking_duplicate():
    mock_req = get_mock_request()
    mock_req.method = "POST"
    
    with patch('azure.data.tables.TableServiceClient', autospec=True) as MockTableServiceClient:
        # Create mock instances
        mock_table_client = MagicMock()
        mock_service_client = MagicMock()
        
        # Set up the chain of mocks properly
        MockTableServiceClient.from_connection_string.return_value = mock_service_client
        mock_service_client.get_table_client.return_value = mock_table_client
        mock_table_client.create_entity.side_effect = ResourceExistsError()
        
        # Ensure the connection string is properly mocked
        with patch.dict('os.environ', {'AZURE_STORAGE_CONNECTION_STRING': 'DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net'}):
            response = main(mock_req)
        
        # Verify the response
        assert response.status_code == 409
        body = json.loads(response.get_body())
        assert body["message"] == "Card already exists in seeking list"
        assert body["error"] == "ALREADY_EXISTS"
        assert "Access-Control-Allow-Origin" in response.headers

def test_add_to_seeking_missing_user_id():
    mock_req = get_mock_request(user_id=None)
    response = main(mock_req)
    
    assert response.status_code == 400
    assert response.get_body() == b"No user ID provided"
    assert "Access-Control-Allow-Origin" in response.headers

def test_add_to_seeking_missing_required_field():
    mock_req = MagicMock()
    mock_req.method = "POST"
    mock_req.headers = {"x-ms-client-principal-id": "user123"}
    mock_req.get_json.return_value = {
        "id": "test-id"  # Missing other required fields
    }
    
    response = main(mock_req)
    
    assert response.status_code == 400
    assert b"Missing required field" in response.get_body()
    assert "Access-Control-Allow-Origin" in response.headers

def test_add_to_seeking_options_request():
    mock_req = MagicMock()
    mock_req.method = "OPTIONS"
    
    response = main(mock_req)
    
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" in response.headers
    assert "Access-Control-Allow-Methods" in response.headers