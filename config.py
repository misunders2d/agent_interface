import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Slack ---
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

# --- Google Cloud ---
GCP_SERVICE_ACCOUNT_STRING = os.getenv("GCP_SERVICE_ACCOUNT")

# --- Agent Engine ---
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID")
APP_NAME = os.getenv("APP_NAME")

ENDPOINT = (
    f"https://us-central1-aiplatform.googleapis.com/v1/{AGENT_ENGINE_ID}"
    if AGENT_ENGINE_ID
    else None
)
