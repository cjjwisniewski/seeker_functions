import pytest
import os
import json
from unittest.mock import patch, MagicMock, call
import azure.functions as func
from datetime import datetime, timedelta
import requests

# Add parent directory to path to import function
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from getSystemStatus import main as getSystemStatus_main, get_function_list, check_function

# --- Constants ---
MOCK_BASE_URL = "https://seeker-functions.azurewebsites.net/api"
FUNCTION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # Project root

# --- Fixtures ---

@pytest.fixture
def mock_os_listdir(monkeypatch):
    """Mock os.listdir to control discovered functions."""
    # Simulate a typical project structure
    mock_files = [
        'addToSeeking',         # Function directory
        'callback',             # Function directory (should be excluded from checks)
        'checkCardtraderStock', # Function directory
        'getSeekingList',       # Function directory
        'getSystemStatus',      # Function directory (should be excluded)
        'tests',                # Test directory (should be excluded)
        '.git',                 # Hidden directory (should be excluded)
        '__pycache__',          # Cache directory (should be excluded)
        'host.json',            # File (should be excluded)
        'requirements.txt',     # File (should be excluded)
    ]
    monkeypatch.setattr("getSystemStatus.os.listdir", lambda path: mock_files)
    # Also mock os.path.isdir
    def mock_isdir(path):
        dir_name = os.path.basename(path)
        return dir_name in ['addToSeeking', 'callback', 'checkCardtraderStock', 'getSeekingList', 'getSystemStatus', 'tests', '.git', '__pycache__']
    monkeypatch.setattr("getSystemStatus.os.path.isdir", mock_isdir)


@pytest.fixture
def mock_requests_get(monkeypatch):
    """Mock requests.get for checking function status."""
    with patch('getSystemStatus.requests.get') as mock_get:
        yield mock_get

@pytest.fixture
def mock_datetime_utcnow(monkeypatch):
    """Mock datetime.utcnow."""
    fixed_time = datetime(2025, 4, 6, 12, 0, 0)
    mock_dt = MagicMock(spec=datetime)
    mock_dt.utcnow.return_value = fixed_time
    # Allow timedelta calculations
    mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
    monkeypatch.setattr("getSystemStatus.datetime", mock_dt)
    return fixed_time

def create_mock_request(method="GET", origin="http://localhost:5173"):
    """Helper to create a mock HttpRequest."""
    headers = {'Origin': origin}
    return func.HttpRequest(
        method=method,
        url='/api/getsystemstatus',
        headers=headers,
        params={},
        body=None
    )

# --- Test Cases ---

def test_get_function_list(mock_os_listdir):
    """Test that get_function_list correctly identifies and filters functions."""
    expected_functions = ['addtoseeking', 'checkcardtraderstock', 'getseekinglist'] # Lowercase, excludes callback, getsystemstatus, tests, etc.
    actual_functions = get_function_list()
    assert sorted(actual_functions) == sorted(expected_functions)

def test_check_function_success(mock_requests_get, mock_datetime_utcnow):
    """Test check_function for a successful response."""
    func_name = "myfunction"
    mock_response = MagicMock(status_code=200)
    mock_requests_get.return_value = mock_response

    # Simulate time passing during request
    start_time = mock_datetime_utcnow.utcnow() # Call the mocked function
    end_time = start_time + timedelta(milliseconds=150)
    # Make the mock return start_time first, then end_time
    mock_datetime_utcnow.utcnow.side_effect = [start_time, end_time]

    result = check_function(func_name)

    mock_requests_get.assert_called_once_with(
        f"{MOCK_BASE_URL}/{func_name}",
        headers={'x-ms-client-principal-id': 'healthcheck'},
        timeout=5
    )
    assert result['name'] == func_name
    assert result['status'] == "running"
    assert result['status_code'] == 200
    assert result['elapsed'] == pytest.approx(150)
    assert result['response_time'] == "150ms" # Check formatted string

def test_check_function_error_5xx(mock_requests_get, mock_datetime_utcnow):
    """Test check_function for a 5xx error response."""
    func_name = "errorfunction"
    mock_response = MagicMock(status_code=503)
    mock_requests_get.return_value = mock_response

    start_time = mock_datetime_utcnow.utcnow()
    end_time = start_time + timedelta(milliseconds=50)
    mock_datetime_utcnow.utcnow.side_effect = [start_time, end_time]

    result = check_function(func_name)

    assert result['name'] == func_name
    assert result['status'] == "error"
    assert result['status_code'] == 503
    assert result['elapsed'] == pytest.approx(50)
    assert result['response_time'] == "50ms"

def test_check_function_request_exception(mock_requests_get, mock_datetime_utcnow):
    """Test check_function when requests.get raises an exception (e.g., timeout)."""
    func_name = "timeoutfunction"
    error_message = "Connection timed out"
    mock_requests_get.side_effect = requests.exceptions.Timeout(error_message)

    start_time = mock_datetime_utcnow.utcnow()
    # End time doesn't matter as exception happens
    mock_datetime_utcnow.utcnow.return_value = start_time # Only start time is called

    result = check_function(func_name)

    assert result['name'] == func_name
    assert result['status'] == "error"
    assert result['status_code'] == 500 # Default status code for exception
    assert result['elapsed'] == 0
    assert result['response_time'] == "0ms"
    assert result['error'] == error_message

def test_main_all_healthy(mock_os_listdir, mock_requests_get, mock_datetime_utcnow):
    """Test main function when all discovered functions are healthy."""
    # Arrange
    req = create_mock_request()
    # Mock responses for the expected functions
    mock_resp_200 = MagicMock(status_code=200)
    mock_requests_get.return_value = mock_resp_200 # All return 200

    # Simulate consistent response time for simplicity
    start_time = mock_datetime_utcnow.utcnow()
    response_times = [100, 120, 80] # ms for addtoseeking, checkcardtraderstock, getseekinglist
    # Create a list of return values for utcnow: start, end, start, end, ...
    call_times = [start_time] # Initial call in main
    for rt in response_times:
        call_times.extend([start_time, start_time + timedelta(milliseconds=rt)]) # Simulate start/end for each check_function call
    mock_datetime_utcnow.utcnow.side_effect = call_times

    # Act
    response = getSystemStatus_main(req)

    # Assert
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    body = json.loads(response.get_body(as_text=True))

    assert body['state'] == "running"
    assert body['availability'] == "normal"
    assert 'last_checked' in body
    assert len(body['functions']) == 3 # addtoseeking, checkcardtraderstock, getseekinglist
    assert all(f['status'] == 'running' for f in body['functions'])
    # Note: Order depends on listdir mock, sort for reliable check
    body['functions'].sort(key=lambda x: x['name'])
    assert body['functions'][0]['name'] == 'addtoseeking'
    assert body['functions'][1]['name'] == 'checkcardtraderstock'
    assert body['functions'][2]['name'] == 'getseekinglist'


    metrics = {m['name']: m['value'] for m in body['metrics']}
    assert metrics['Total Functions Checked'] == 3
    assert metrics['Healthy Functions'] == 3
    assert metrics['Average Response Time'] == "100ms" # (100+120+80)/3
    assert metrics['Health Percentage'] == "100.0%"

    assert 'Access-Control-Allow-Origin' in response.headers

def test_main_one_error(mock_os_listdir, mock_requests_get, mock_datetime_utcnow):
    """Test main function when one function returns an error."""
    # Arrange
    req = create_mock_request()
    mock_resp_200 = MagicMock(status_code=200)
    mock_resp_500 = MagicMock(status_code=500)

    # checkCardtraderStock will fail
    def get_side_effect(url, *args, **kwargs):
        if 'checkcardtraderstock' in url.lower():
            return mock_resp_500
        else:
            return mock_resp_200
    mock_requests_get.side_effect = get_side_effect

    # Simulate response times
    start_time = mock_datetime_utcnow.utcnow()
    response_times = [100, 50, 80] # ms for addtoseeking, checkcardtraderstock (error), getseekinglist
    call_times = [start_time] # Initial call in main
    for rt in response_times:
        call_times.extend([start_time, start_time + timedelta(milliseconds=rt)])
    mock_datetime_utcnow.utcnow.side_effect = call_times

    # Act
    response = getSystemStatus_main(req)

    # Assert
    assert response.status_code == 200 # Main function still returns 200
    body = json.loads(response.get_body(as_text=True))

    assert body['state'] == "degraded"
    assert body['availability'] == "limited"
    assert len(body['functions']) == 3
    # Sort for reliable check
    body['functions'].sort(key=lambda x: x['name'])
    assert body['functions'][0]['status'] == 'running' # addtoseeking
    assert body['functions'][1]['status'] == 'error'   # checkcardtraderstock
    assert body['functions'][2]['status'] == 'running' # getseekinglist

    metrics = {m['name']: m['value'] for m in body['metrics']}
    assert metrics['Total Functions Checked'] == 3
    assert metrics['Healthy Functions'] == 2
    assert metrics['Average Response Time'] == "76ms" # (100+50+80)/3
    assert metrics['Health Percentage'] == "66.7%" # 2/3

    assert 'Access-Control-Allow-Origin' in response.headers

def test_main_options_request(mock_os_listdir):
    """Test handling of OPTIONS preflight request."""
    # Arrange
    req = create_mock_request(method="OPTIONS")

    # Act
    response = getSystemStatus_main(req)

    # Assert
    assert response.status_code == 200 # Should be 200 for OPTIONS
    assert response.get_body() == b''
    assert 'Access-Control-Allow-Origin' in response.headers
    assert 'Access-Control-Allow-Methods' in response.headers
    assert 'Access-Control-Allow-Headers' in response.headers

def test_main_no_functions_found(mock_os_listdir, monkeypatch, mock_requests_get):
    """Test main function when listdir returns only excluded items."""
    # Arrange
    req = create_mock_request()
    # Simulate only finding excluded items
    monkeypatch.setattr("getSystemStatus.os.listdir", lambda path: ['tests', '.git', 'callback', 'getSystemStatus'])
    def mock_isdir(path):
        return True # Treat all as dirs for simplicity here
    monkeypatch.setattr("getSystemStatus.os.path.isdir", mock_isdir)

    # Act
    response = getSystemStatus_main(req)

    # Assert
    assert response.status_code == 200
    body = json.loads(response.get_body(as_text=True))

    assert body['state'] == "running" # No functions to check -> running
    assert body['availability'] == "normal"
    assert len(body['functions']) == 0 # No functions checked or reported

    metrics = {m['name']: m['value'] for m in body['metrics']}
    assert metrics['Total Functions Checked'] == 0
    assert metrics['Healthy Functions'] == 0
    assert metrics['Average Response Time'] == "0ms"
    assert metrics['Health Percentage'] == "0.0%" # Avoid division by zero

    mock_requests_get.assert_not_called() # No functions were checked
