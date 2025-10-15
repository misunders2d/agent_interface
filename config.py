import os
from dotenv import load_dotenv

# Load environment variables from .env file
project_root = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(project_root, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)

# --- Slack ---
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")

# --- Telegram ---
# TELEGRAM_BOT_TOKEN is required to run the Telegram bot.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Use webhook if you want to run behind HTTPS endpoint. Default: polling.
TELEGRAM_USE_WEBHOOK = os.getenv("TELEGRAM_USE_WEBHOOK", "false").lower() in [
    "1",
    "true",
    "yes",
]
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")
TELEGRAM_WEBHOOK_PORT = int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8443"))

# --- Google Cloud ---
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "")
GCP_SERVICE_ACCOUNT_STRING = os.getenv("GCP_SERVICE_ACCOUNT", "")
GOOGLE_CLOUD_BUCKET = os.getenv("GOOGLE_CLOUD_BUCKET", "")

# --- Agent Engine ---
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID", "")
APP_NAME = os.getenv("APP_NAME", "")

ENDPOINT = (
    f"https://{GOOGLE_CLOUD_LOCATION}-aiplatform.googleapis.com/v1/{AGENT_ENGINE_ID}"
    if AGENT_ENGINE_ID
    else None
)
