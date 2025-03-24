# seeker_createUserTable

A Python Azure Function that creates and manages user tables for the Seeker application.

## Prerequisites
- Azure CLI
- Azure Functions Core Tools v4
- Python 3.9+
- pip

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables:
```bash
# Set Azure Storage connection string
az functionapp config appsettings set \
    --name seeker-createusertable \
    --resource-group seeker-rg \
    --settings AZURE_STORAGE_CONNECTION_STRING="your_connection_string"
```

## Project Structure
```
seeker_createUserTable/
├── createUserTable/
│   ├── __init__.py      # Main function logic
│   └── function.json    # Function binding configuration
├── host.json           # Function app configuration
└── requirements.txt    # Python dependencies
```

## Development
To test locally:
```bash
func start
```

## Testing
Run the test script:
```bash
python test_function.py
```

## Deployment
Deploy to Azure:
```bash
func azure functionapp publish seeker-createUserTable
```

## Monitoring
View function logs:
```bash
az functionapp logs tail \
    --name seeker-createusertable \
    --resource-group seeker-rg
```

## Function URL
Once deployed, the function is available at:
```
https://seeker-createusertable.azurewebsites.net/api/createusertable
```