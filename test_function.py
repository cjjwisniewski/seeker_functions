import requests

def test_function():
    function_url = "https://seeker-createusertable.azurewebsites.net/api/createusertable"
    headers = {
        "Content-Type": "application/json",
        "x-ms-client-principal-id": "123456789012345678"
    }
    
    response = requests.post(function_url, headers=headers)
    print(f"Status: {response.status_code}")
    print(f"Raw Response: {response.text}")
    
    try:
        print(f"JSON Response: {response.json()}")
    except json.decoder.JSONDecodeError:
        print("Could not parse response as JSON")

if __name__ == "__main__":
    test_function()