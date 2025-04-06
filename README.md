# Seeker - Azure Functions Backend

## Overview

Seeker is a backend application built using Azure Functions. It provides APIs for managing user card seeking lists, interacting with the Cardtrader marketplace, and handling user authentication via Discord. The primary goal appears to be helping users track desired trading cards and potentially check their availability or price on Cardtrader.

## Features

*   **Discord Authentication:** Handles user login, logout, and session management using Discord OAuth2.
*   **User Profile Management:** Creates user-specific storage and allows account deletion (with admin controls).
*   **Card Seeking List:** Allows users to add, view, and remove cards from their personal seeking list.
*   **Cardtrader Integration:**
    *   Fetches Cardtrader set information.
    *   Fetches Cardtrader blueprint (card version) information.
    *   Periodically checks Cardtrader stock/availability for cards in user seeking lists (timer-triggered).
*   **System Status:** Provides an endpoint to check the status of various functions.
*   **Admin Functionality:** Includes endpoints for administrative tasks like viewing user lists and deleting accounts.

## Technology Stack

*   **Cloud Platform:** Microsoft Azure (Azure Functions, Azure Table Storage)
*   **Language:** Python
*   **Framework:** Azure Functions Python SDK
*   **Authentication:** Discord OAuth2
*   **External APIs:**
    *   Discord API
    *   Cardtrader API
*   **Testing:** pytest

## Setup

1.  **Prerequisites:**
    *   Python 3.x
    *   Azure Functions Core Tools
    *   Azure CLI (optional, for deployment/management)
    *   Access to an Azure subscription with Storage Account configured.
2.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Environment Variables:** Configure necessary environment variables in `local.settings.json` for local development or Application Settings in Azure Functions App. Key variables include:
    *   `AZURE_STORAGE_CONNECTION_STRING`: Connection string for Azure Table Storage.
    *   `DISCORD_CLIENT_ID`: Discord application client ID.
    *   `DISCORD_CLIENT_SECRET`: Discord application client secret.
    *   `DISCORD_REDIRECT_URI`: Callback URL registered with Discord (e.g., `http://localhost:7071/api/callback` or deployed function URL).
    *   `DISCORD_REQUIRED_GUILD_ID`: (Optional) Discord server ID for membership check.
    *   `DISCORD_REQUIRED_ROLE_ID`: (Optional) Discord role ID for membership check.
    *   `CARDTRADER_API_KEY`: API key for Cardtrader.
    *   `FRONTEND_URL`: URL of the frontend application for redirects.
    *   `CREATE_USER_TABLE_FUNCTION_URL`: URL of the `createUserTable` function.
    *   `ADMIN_USER_IDS`: Comma-separated list of Discord user IDs with admin privileges.

## API Endpoints (Azure Functions)

*   `login`: Initiates the Discord OAuth2 login flow.
*   `callback`: Handles the OAuth2 callback from Discord, exchanges code for token, fetches user info, and potentially creates user table.
*   `logout`: Revokes the Discord token.
*   `userinfo`: Retrieves user information (Discord ID, guilds, etc.) based on the provided token.
*   `createUserTable`: Creates a dedicated Azure Table Storage table for a new user.
*   `addToSeeking`: Adds a card entry to the user's seeking list table.
*   `getSeekingList`: Retrieves the user's seeking list (admin can view others).
*   `deleteFromSeeking`: Removes a card entry from the user's seeking list table.
*   `getUserTables`: Lists user tables (likely for admin purposes).
*   `deleteUserAccount`: Deletes a user's account and associated table (admin or self-service).
*   `getCardtraderSets`: Timer-triggered function to fetch and store Cardtrader set data.
*   `getCardtraderBlueprints`: Timer-triggered function to fetch and store Cardtrader blueprint data for known sets.
*   `checkCardtraderStock`: Timer-triggered function to check Cardtrader stock for items in user seeking lists.
*   `getSystemStatus`: Checks the operational status of various functions.

## Testing

Tests are written using `pytest`. Run tests from the root directory:

```bash
pytest
```
