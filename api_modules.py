import json
import requests
from google.oauth2 import service_account
from google.auth.transport import requests as google_requests
import config


# --- Auth ---
def get_identity_token():
    """Get identity token from the GCP service account string."""
    if not config.GCP_SERVICE_ACCOUNT_STRING:
        raise ValueError("GCP_SERVICE_ACCOUNT environment variable not set.")
    service_info = json.loads(config.GCP_SERVICE_ACCOUNT_STRING)
    credentials = service_account.Credentials.from_service_account_info(
        service_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    auth_req = google_requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


# --- Session Management ---
def list_sessions(user_id: str):
    url = f"{config.ENDPOINT}:query"
    headers = {
        "Authorization": f"Bearer {get_identity_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "class_method": "list_sessions",
        "input": {"user_id": user_id},
    }
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    return resp.json().get("output", {}).get("sessions", [])


def create_session(user_id: str) -> str:
    url = f"{config.ENDPOINT}:query"
    headers = {
        "Authorization": f"Bearer {get_identity_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "class_method": "create_session",
        "input": {"user_id": user_id},
    }
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    data = resp.json()
    return data.get("output", {}).get("id") or data.get("sessionId")


def get_or_create_session(user_id: str) -> str:
    sessions = list_sessions(user_id)
    if sessions:
        return sessions[-1].get("id")
    return create_session(user_id)


def get_session(user_id: str, session_id: str) -> dict:
    url = f"{config.ENDPOINT}:query"
    headers = {
        "Authorization": f"Bearer {get_identity_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "class_method": "get_session",
        "input": {"user_id": user_id, "session_id": session_id},
    }
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    data = resp.json()
    return data


def delete_session(user_id: str, session_id: str) -> dict:
    url = f"{config.ENDPOINT}:query"
    headers = {
        "Authorization": f"Bearer {get_identity_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "class_method": "delete_session",
        "input": {"user_id": user_id, "session_id": session_id},
    }
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    data = resp.json()
    return data

def list_messages(session):
    pass