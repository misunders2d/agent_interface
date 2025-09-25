from vertexai import agent_engines
from google.adk.events import Event#, EventActions
from google.adk.sessions import VertexAiSessionService
from google.genai import types

import json
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
    return credentials


def get_remote_agent(resource_name=config.AGENT_ENGINE_ID):
    remote_app = agent_engines.get(resource_name)
    remote_app.credentials = get_identity_token()
    return remote_app


def get_session_service():
    project = config.GOOGLE_CLOUD_PROJECT
    location = config.GOOGLE_CLOUD_LOCATION
    session_service = VertexAiSessionService(project=project, location=location)
    return session_service


# --- Session Management ---
async def list_sessions(
    session_service: VertexAiSessionService, user_id: str
) -> list | None:
    remote_sessions = await session_service.list_sessions(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id
    )
    if remote_sessions.sessions:
        return remote_sessions.sessions
    else:
        return None


async def create_session(
    session_service: VertexAiSessionService, user_id: str, session_id: str | None = None
) -> str:
    remote_session = await session_service.create_session(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id, session_id=session_id
    )
    return remote_session.id


async def get_session(
    session_service: VertexAiSessionService, user_id: str, session_id: str
):
    remote_session = await session_service.get_session(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id, session_id=session_id
    )
    return remote_session


async def get_or_create_session(
    session_service: VertexAiSessionService, user_id: str
) -> str:
    sessions = await list_sessions(session_service, user_id)
    if sessions:
        return sessions[-1].id
    return await create_session(session_service, user_id)


async def delete_session(
    session_service: VertexAiSessionService, user_id: str, session_id: str
):
    await session_service.delete_session(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id, session_id=session_id
    )


async def add_messages(
    session_service: VertexAiSessionService, session_id, user_id, author, message
):
    session = await get_session(
        session_service=session_service, user_id=user_id, session_id=session_id
    )
    if session:
        part = types.Part(text=message)
        event = Event(
            content=types.Content(parts=[part]),
            author=author,
            invocation_id="invocation_id",
        )
        await session_service.append_event(session=session, event=event)


if __name__ == "__main__":
    import asyncio

    session_service = get_session_service()
    session = asyncio.run(
        get_session(
            session_service,
            user_id="Slack: D07LHACUY6R",
            session_id="8082777706862739456",
        )
    )
    asyncio.run(
        add_messages(
            session_service,
            session_id="8082777706862739456",
            user_id="Slack: D07LHACUY6R",
            author="Sergey Demchenko",
            message="heyhowareyouagain?",
        )
    )
    print(session)