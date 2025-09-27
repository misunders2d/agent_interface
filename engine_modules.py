from vertexai import agent_engines
from google.adk.events import Event, EventActions
from google.adk.sessions import VertexAiSessionService, Session
from google.genai import types

import datetime
import json
import uuid
from google.oauth2 import service_account
import google.auth
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


credentials = get_identity_token()
google.auth.default = lambda *args, **kwargs: (credentials, credentials.project_id)
# vertexai.init(credentials=get_identity_token())


def get_remote_agent(resource_name=config.AGENT_ENGINE_ID):
    remote_app = agent_engines.get(resource_name)
    remote_app.credentials = get_identity_token()
    return remote_app


def get_session_service() -> VertexAiSessionService:
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
) -> Session | None:
    remote_session = await session_service.get_session(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id, session_id=session_id
    )
    if remote_session:
        return remote_session


async def get_or_create_session(
    session_service: VertexAiSessionService, user_id: str, session_id: str | None = None
) -> str:
    sessions = await list_sessions(session_service, user_id)
    if sessions:
        return sessions[-1].id
    return await create_session(session_service, user_id, session_id)


async def delete_session(
    session_service: VertexAiSessionService, user_id: str, session_id: str
):
    await session_service.delete_session(
        app_name=config.AGENT_ENGINE_ID, user_id=user_id, session_id=session_id
    )


async def change_state(
    session_service: VertexAiSessionService, session_id, user_id, state_delta
):
    session = await get_session(
        session_service=session_service, user_id=user_id, session_id=session_id
    )
    if session:
        actions = EventActions(state_delta=state_delta)
        event = Event(
            actions=actions,
            author="system",
            invocation_id=f"invocation_{uuid.uuid4()}",
            id=f"id_{uuid.uuid4()}",
        )
        await session_service.append_event(session=session, event=event)


async def add_messages(
    session_service: VertexAiSessionService, session_id, user_id, author, message
):
    session = await get_session(
        session_service=session_service, user_id=user_id, session_id=session_id
    )
    if session:
        part = types.Part(text=message)
        event = Event(
            content=types.Content(parts=[part], role="user"),
            author=author,
            invocation_id=f"invocation_{uuid.uuid4()}",
            id=f"id_{uuid.uuid4()}",
        )
        await session_service.append_event(session=session, event=event)


async def list_messages(session_service: VertexAiSessionService, session_id, user_id):
    session = await get_session(
        session_service=session_service, session_id=session_id, user_id=user_id
    )
    if session:
        for event in session.events:
            ts = event.timestamp
            date = datetime.datetime.fromtimestamp(ts)

            if event.content and event.content.parts:
                text = [x.text for x in event.content.parts]
                if text:
                    print(f"{date.isoformat()}: {text}", end="\n\n")


async def run_query(user_id, session_id):
    async for event in agent_app.async_stream_query(  # type: ignore
        user_id=user_id,
        session_id=session_id,
        message="""List all messages here including this one. Don't use memory agents, don't list agent messages. Don't overthink this or pass to any agents, I'm simply testing whether you can see the context and history.""",
    ):
        print(event)


if __name__ == "__main__":
    import asyncio

    import pickle
    agent_app = get_remote_agent()
    user_id = "Slack: D07LHACUY6R"
    session_service = get_session_service()
    session_id = "5911989909912027136"
    # session_id = asyncio.run(
    #     get_or_create_session(
    #         session_service,
    #         user_id=user_id,
    #     )
    # )
    # session_id = asyncio.run(create_session(session_service, user_id, session_id = None))
    session = asyncio.run(
        get_session(
            session_service=session_service, user_id=user_id, session_id=session_id
        )
    )
    print(session)
    # with open('session.pkl', 'wb') as file:
    #     pickle.dump(session, file)
    # print(session)

    # asyncio.run(
    #     add_messages(
    #         session_service,
    #         session_id,
    #         user_id,
    #         author="sergey demchenko",
    #         message="First test message from outside of the flow",
    #         # timestamp=datetime.datetime.now().timestamp()
    #     )
    # )
    # print("Message added", end="\n\n\n")
    # asyncio.run(
    #     add_messages(
    #         session_service,
    #         session_id,
    #         user_id,
    #         author="Vladimir Barabulia",
    #         message="and another test message from outside of the flow",
    #         # timestamp=datetime.datetime.now().timestamp()
    #     )
    # )
    # print("Message added", end="\n\n\n")
    # asyncio.run(run_query(user_id, session_id))
