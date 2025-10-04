import os
import requests
import json
import asyncio
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- Initialization ---
import sys

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import engine_modules

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = App(token=config.SLACK_BOT_TOKEN, signing_secret=config.SLACK_SIGNING_SECRET)
agent_app = engine_modules.get_remote_agent()
session_service = engine_modules.get_session_service()
artifact_service = engine_modules.get_artifact_service()
memory_service = engine_modules.get_memory_service()

show_tools = False
sessions_dict = {}


def get_event_info(body) -> dict:
    event_info = {}
    event = body["event"]
    event_info["event"] = event
    event_info["user_id"] = event["user"]
    event_info["message_text"] = event["text"]
    event_info["channel_id"] = event["channel"]
    # Only reply in a thread if the original message was in a thread
    event_info["thread_ts"] = event.get("thread_ts")
    # Fetch channel display name
    try:
        channel_info = app.client.conversations_info(channel=event_info["channel_id"])
        event_info["channel_display_name"] = channel_info.get("channel", {}).get(
            "name", event_info["channel_id"]
        )
    except Exception as e:
        logger.error(f"Error fetching channel display name: {e}")
        event_info["channel_display_name"] = event_info["channel_id"]
    event_info["session_user_id"] = f"Slack: {event_info['channel_display_name']}"
    try:
        # Fetch user email
        user_info = app.client.users_info(user=event_info["user_id"])
        profile = user_info.get("user", {}).get("profile", {})
        event_info["user_email"] = profile.get("email", "unknown.email@example.com")
        event_info["display_name"] = profile.get("display_name", "Unknown User")

    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
        event_info["user_email"] = "unknown.email@example.com"
        event_info["display_name"] = "Unknown User"

    files_info = []

    if "files" in event_info["event"]:
        # logger.info(f"Downloading {len(event_info['event']['files'])} attached files...\n\n\n")
        for file_obj in event_info["event"]["files"]:
            # The URL is private and requires an Authorization header to access
            url = file_obj["url_private_download"]
            file_type = file_obj["filetype"]
            file_name = file_obj["name"]
            file_size = file_obj["size"]

            # logger.info(f"Downloading file: {file_name} ({file_type}) from {url}\n\n\n")

            headers = {"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"}
            try:

                response = requests.get(url, headers=headers)
                response.raise_for_status()  # Raises an exception for bad status codes

                response_content = response.content
                files_info.append(
                    {
                        "name": file_name,
                        "mime_type": file_type,
                        "content": response_content,  # This is the raw file data in bytes
                        "size": file_size,
                    }
                )

            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to download file {file_name}: {e}")
                app.client.chat_postMessage(
                    channel=event_info["channel_id"],
                    thread_ts=event_info["reply_ts"],
                    text=f"Sorry, I couldn't download the file: {file_name}",
                )
                return event_info

    # Enrich the message with user info
    event_info["enriched_message"] = (
        f"Message from {event_info['display_name']} {event_info['user_email']} ({event_info['user_id']}): {event_info['message_text']}"
    )
    if files_info:
        event_info["files_attached"] = files_info
        event_info[
            "enriched_message"
        ] += f"Attached files: {', '.join([f['name'] for f in files_info])}."
    # logger.info(f"[EVENT INFO]:\n\n{event_info}\n\n\n")  # TODO remove after debugging
    return event_info


async def get_session_id(user_id):
    if user_id in sessions_dict:
        logger.info(f"Using cached session ID for user {user_id}")
        return sessions_dict[user_id]
    else:
        logger.info(f"Creating / fetching new session ID for user {user_id}")
        session_id = await engine_modules.get_or_create_session(
            session_service=session_service, user_id=user_id
        )
        sessions_dict[user_id] = session_id
        return session_id


async def query_agent_and_reply(body, say):
    """
    Queries the agent in a background thread and posts the response back to Slack.
    """
    event_info = get_event_info(body)
    try:
        initial_reply = say(text="🧠 Thinking...", thread_ts=event_info["thread_ts"])
        reply_ts = initial_reply["ts"]
    except Exception as e:
        logger.error(f"Error posting initial reply: {e}")
        return

    final_answer = ""
    last_text = ""
    thoughts = []
    session_id = None
    try:
        session_id = await get_session_id(event_info["session_user_id"])

        # change user_id temporarily to the message's user email for personal memories access
        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            state_delta={"user_id": event_info["user_email"]},
        )

        prepared_message = engine_modules.prepare_message_dict(
            text=event_info["enriched_message"],
            file_list=event_info.get("files_attached", []),
        )

        async for response in agent_app.async_stream_query(  # type: ignore
            user_id=event_info["session_user_id"],
            session_id=session_id,
            message=prepared_message,
        ):
            response_author = response.get("author")
            # logger.info("[EVENT]" + "-" * 40)
            # logger.info(response)
            # logger.info("\n\n\n")

            if not response:
                continue
            try:
                # Handle validator agent output
                if response_author == "answer_validator_agent":
                    # validator_text = (
                    #     response.get("content", {})
                    #     .get("parts", [{}])[0]
                    #     .get("text", "{}")
                    # )
                    # thoughts.append(f"🕵️ *Validator Agent*: `{validator_text}`")
                    continue

                # Handle other agents' output
                parts = response.get("content", {}).get("parts", [])
                for part in parts:
                    # logger.info(f"[PART{i}]" + '-' * 40)
                    # logger.info(f"{part} \n\n\n")
                    if part.get("text") and not part.get("thought"):
                        # logger.info(f"logging part without thought: {part.get('text')}")
                        final_answer += part.get("text")
                    if part.get("text") and part.get("thought"):
                        # logger.info(f"logging part WITH thought: {part.get('text')}")
                        thought = (
                            f"🧠 *Thought* ({response_author}): {part.get('text')}"
                        )
                        thoughts.append(thought)
                        app.client.chat_postMessage(
                            channel=event_info["channel_id"],
                            thread_ts=reply_ts,
                            text=thought,
                        )
                        last_text = thought

                    elif part.get("function_call"):
                        fc = part.get("function_call")
                        thought = f"🔧 *Tool Call* ({response_author}): `{fc.get('name')}` with args: `{fc.get('args')}`"
                        thoughts.append(thought)
                        if show_tools:
                            app.client.chat_postMessage(
                                channel=event_info["channel_id"],
                                thread_ts=reply_ts,
                                text=thought,
                            )
                        last_text = thought

                    elif part.get("function_response"):
                        fr = part.get("function_response")
                        thought = f"📥 *Tool Response* for `{fr.get('name')}`: `{fr.get('response')}`"
                        thoughts.append(thought)
                        if show_tools:
                            app.client.chat_postMessage(
                                channel=event_info["channel_id"],
                                thread_ts=reply_ts,
                                text=thought,
                            )
                        last_text = thought

                # if artifact_delta:
                #     filename = artifact_delta.keys()[0]
                #     artifact = await engine_modules.load_artifact(
                #         artifact_service=artifact_service,
                #         session_id=session_id,
                #         user_id=event_info["session_user_id"],
                #         filename=filename,
                #     )

            except json.JSONDecodeError as e:
                app.client.chat_postMessage(
                    channel=event_info["channel_id"],
                    thread_ts=reply_ts,
                    text=f"ERROR: Ran into JSON decoding issue: {str(e)}",
                )
            except Exception as e:
                app.client.chat_postMessage(
                    channel=event_info["channel_id"],
                    thread_ts=reply_ts,
                    text=f"ERROR: Ran into an unexpected issue: {str(e)}",
                )

        # change user_id back to channel id to prevent personal memories access
        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            state_delta={"user_id": event_info["user_id"]},
        )

    except Exception as e:
        if str(e).startswith("404 NOT_FOUND") and "sessionId" in str(e) and session_id:
            sessions_dict.pop(event_info["session_user_id"], None)
            await engine_modules.delete_session(
                session_service=session_service,
                user_id=event_info["session_user_id"],
                session_id=session_id,
            )
            logger.info("Session not found, creating a new one and retrying...")
            await query_agent_and_reply(body, say)
        else:
            final_answer = f"Sorry, an error occurred: {e}"
            logger.error(final_answer)

    # If there's no final answer, delete the 'Thinking...' message and stop.
    if not final_answer and not last_text:
        logger.info("Agent provided no final answer. Deleting 'Thinking...' message.")
        try:
            app.client.chat_delete(channel=event_info["channel_id"], ts=reply_ts)
        except Exception as e:
            logger.error(f"Error deleting 'Thinking...' message: {e}")
        return

    # Otherwise, update the message with the final answer and post thoughts.
    try:
        post_message = final_answer or last_text
        chunks = [post_message[i : i + 3900] for i in range(0, len(post_message), 3900)]
        first_chunk = chunks[0]
        # Send extra chunks as new messages in the same thread
        if len(chunks) > 1:
            first_chunk += "\n\n*(Message too long, continued in thread...)*"
            app.client.chat_postMessage(
                channel=event_info["channel_id"],
                thread_ts=reply_ts,
                text="Remaining message: ",
            )
            for chunk in chunks[1:]:
                app.client.chat_postMessage(
                    channel=event_info["channel_id"], thread_ts=reply_ts, text=chunk
                )
        # Send the first chunk as an update to the initial reply
        app.client.chat_update(
            channel=event_info["channel_id"], ts=reply_ts, text=first_chunk
        )
    except Exception as e:
        logger.error(f"Error updating message or posting thoughts: {e}")


async def process_message_for_context(body):
    """
    Silently sends a message to the agent engine for context-building.
    """
    event_info = get_event_info(body)
    try:
        session_id = await get_session_id(event_info["session_user_id"])

        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            author=event_info["display_name"],
            message=event_info["enriched_message"],
            file_list=event_info.get("files_attached", []),
        )

        logger.info(
            f"Processed message for context in channel {event_info['channel_id']}"
        )
    except Exception as e:
        logger.error(f"Error processing message for context: {e}")


# --- Slack Event Handlers ---
@app.command("/delete_session")
def handle_delete_session(ack, body, say):
    ack()
    channel_id = body["channel_id"]
    session_user_id = f"Slack: {channel_id}"
    session_id = asyncio.run(get_session_id(session_user_id))

    try:
        asyncio.run(
            engine_modules.delete_session(
                session_service=session_service,
                user_id=session_user_id,
                session_id=session_id,
            )
        )
        sessions_dict.pop(session_user_id, None)
        say("🗑️ Deleted session for this channel.")
    except Exception as e:
        error_msg = f"Error deleting session: {e}"
        logger.error(error_msg)
        say(f"❌ {error_msg}")


@app.command("/save_session")
def handle_save_session(ack, body, say):
    ack()
    channel_id = body["channel_id"]
    session_user_id = f"Slack: {channel_id}"
    session_id = asyncio.run(get_session_id(session_user_id))

    session = asyncio.run(
        engine_modules.get_session(
            session_service=session_service,
            user_id=session_user_id,
            session_id=session_id,
        )
    )

    if session:
        try:
            asyncio.run(memory_service.add_session_to_memory(session))
            say("💾 Saved session for this channel.")
        except Exception as e:
            error_msg = f"Error saving session: {e}"
            logger.error(error_msg)
            say(f"❌ {error_msg}")


@app.event("app_mention")
def handle_app_mention(body, say, ack):
    ack()
    asyncio.run(query_agent_and_reply(body, say))


@app.event("message")
def handle_message_events(body, say, logger):
    event = body["event"]
    subtype = event.get("subtype")

    # Ignore messages from bots, and subtypes other than file shares
    if "bot_id" in event or (subtype and subtype != "file_share"):
        return

    channel_type = event.get("channel_type")
    if channel_type == "im":
        logger.info("Received DM, processing for reply...")
        asyncio.run(query_agent_and_reply(body, say))
        # asyncio.run(process_message_for_context(body))
        return

    if channel_type in ["channel", "group"]:
        logger.info("Received channel message, processing for context...")
        asyncio.run(process_message_for_context(body))
        return


@app.event("reaction_added")
def handle_reaction_added_events(body, logger):
    logger.info(f"REACTION NEEDED: {body}")


# --- App Start ---
if __name__ == "__main__":
    logger.info("🤖 Slack bot is running in Socket Mode...")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()
