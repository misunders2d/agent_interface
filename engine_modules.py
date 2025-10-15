from vertexai import agent_engines, init as vertexai_init
from google.adk.events import Event, EventActions
from google.adk.sessions import VertexAiSessionService, Session
from google.adk.memory import VertexAiMemoryBankService
from google.adk.artifacts import GcsArtifactService
from google.genai import types

import datetime
import json
import uuid
import base64
from google.oauth2 import service_account
import google.auth
from google.auth.transport import requests as google_requests
import config


# --- Auth ---
def get_credentials():
    """Get credentials object from the GCP service account string."""
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


# --- Initialization ---
# Get credentials from the service account
credentials = get_credentials()

google.auth.default = lambda *args, **kwargs: (credentials, credentials.project_id)

# Initialize the Vertex AI SDK globally
vertexai_init(
    credentials=credentials,
    project=config.GOOGLE_CLOUD_PROJECT,
    location=config.GOOGLE_CLOUD_LOCATION,
)

# --- Necessary variables ---
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MIME_TYPE_MAPPING = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/plain",
}
SUPPORTED_MIME_TYPES = set([x for x in MIME_TYPE_MAPPING.values()])


# --- Remote Agent and Services ---

def get_remote_agent(resource_name=config.AGENT_ENGINE_ID):
    """Gets the remote agent engine, relying on the global SDK initialization."""
    remote_app = agent_engines.get(resource_name)
    return remote_app


def get_session_service() -> VertexAiSessionService:
    """Initializes the session service with explicit credentials."""
    return VertexAiSessionService(
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )


def get_memory_service() -> VertexAiMemoryBankService:
    """Initializes the memory service with explicit credentials."""
    return VertexAiMemoryBankService(
        agent_engine_id=config.AGENT_ENGINE_ID.split('/')[-1],
        project=credentials.project_id,
    )


def get_artifact_service() -> GcsArtifactService:
    """Initializes the artifact service with explicit credentials."""
    return GcsArtifactService(
        bucket_name=config.GOOGLE_CLOUD_BUCKET, credentials=credentials
    )


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


async def update_session(
    session_service: VertexAiSessionService,
    session_id: str,
    user_id: str,
    author: str = "user",  # Changed default to 'user' as typically this is user input
    message: str | None = None,
    file_list: list | None = None,
    state_delta: dict = {},
):
    """
    file_list, if provided, must be a list of dicts with keys:
    {
        "name": file_name,
        "mime_type": file_type,
        "content": response_content,  # This is the raw file data in bytes
        "size": file_size,
    }
    """

    # Assuming get_session is a working helper function you have defined elsewhere
    session = await get_session(
        session_service=session_service, user_id=user_id, session_id=session_id
    )
    parts = []
    if message:
        parts.append(types.Part(text=message))

    if file_list:
        for file in file_list:
            file_content = file["content"]  # This is the raw file data in bytes
            file_name = file.get("name", "unknown_file")
            file_size = file.get("size", len(file_content))
            provided_file_type = file.get("mime_type")
            if provided_file_type and "/" in provided_file_type:
                provided_file_type = provided_file_type.split("/")[-1]

            if file_size > MAX_FILE_SIZE_BYTES:
                print(
                    f"Skipping file '{file_name}': Size ({file_size / (1024*1024):.2f} MB) exceeds 20 MB limit."
                )
                continue

            actual_mime_type = MIME_TYPE_MAPPING.get(provided_file_type, None)
            if actual_mime_type not in SUPPORTED_MIME_TYPES:
                print(
                    f"Skipping file '{file_name}': Unsupported MIME type '{provided_file_type}' (resolved to '{actual_mime_type}')."
                )
                continue
            parts.append(
                types.Part(
                    inline_data=types.Blob(
                        mime_type=actual_mime_type, data=file_content
                    )
                )
            )

    if session:
        actions = EventActions(state_delta=state_delta)
        event = Event(
            actions=actions,
            content=types.Content(parts=parts, role=author),
            author=author,
            invocation_id=f"invocation_{uuid.uuid4()}",
            id=f"id_{uuid.uuid4()}",
        )
        await session_service.append_event(session=session, event=event)


async def save_artifact(
    artifact_service: GcsArtifactService,
    session_id,
    user_id,
    filename,
    file_content,
    mime_type,
):
    part = types.Part(inline_data=types.Blob(mime_type=mime_type, data=file_content))
    result = await artifact_service.save_artifact(
        app_name=config.APP_NAME,
        user_id=user_id,
        session_id=session_id,
        filename=filename,
        artifact=part,
    )
    print(f"ARTIFACT SAVE RESULT: {result}\n\n\n")
    return result


async def load_artifact(
    artifact_service: GcsArtifactService, session_id, user_id, filename
):
    try:
        result = await artifact_service.load_artifact(
            app_name=config.APP_NAME,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        print(f"ARTIFACT LOAD RESULT: {result}\n\n\n")
    except Exception as e:
        print(f"Error loading artifact '{filename}': {e}")
        return None
    return result


def prepare_message_dict(text: str, file_list: list | None = None) -> dict:
    """
    file_list, if provided, must be a list of dicts with keys:
    {
        "name": file_name,
        "mime_type": file_type,
        "content": response_content,  # This is the raw file data in bytes
        "size": file_size,
    }
    """
    message = {"parts": [], "role": "user"}

    if text:
        message["parts"].append({"text": text})

    if file_list:
        for file in file_list:
            file_content = file["content"]  # This is the raw file data in bytes
            file_name = file.get("name", "unknown_file")
            file_size = file.get("size", len(file_content))
            provided_file_type = file.get("mime_type")
            if provided_file_type and "/" in provided_file_type:
                provided_file_type = provided_file_type.split("/")[-1]
            encoded_content = base64.b64encode(file_content).decode("utf-8")

            if file_size > MAX_FILE_SIZE_BYTES:
                print(
                    f"Skipping file '{file_name}': Size ({file_size / (1024*1024):.2f} MB) exceeds 20 MB limit."
                )
                continue

            actual_mime_type = MIME_TYPE_MAPPING.get(provided_file_type, None)
            if actual_mime_type not in SUPPORTED_MIME_TYPES:
                print(
                    f"Skipping file '{file_name}': Unsupported MIME type '{provided_file_type}' (resolved to '{actual_mime_type}')."
                )
                continue

            message["parts"].append(
                {
                    "inline_data": {
                        "data": encoded_content,
                        "mime_type": actual_mime_type,
                    }
                }
            )
    return message


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

    agent_app = get_remote_agent()
    session_service = get_session_service()
    session_id = asyncio.run(list_sessions(session_service=session_service, user_id="sergey@mellanni.com"))
    print(session_id)
