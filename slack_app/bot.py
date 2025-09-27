import os
import json
import asyncio
import logging
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- Initialization ---
import sys

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

show_tools = False


async def query_agent_and_reply(body, say):
    """
    Queries the agent in a background thread and posts the response back to Slack.
    """
    event = body["event"]
    user_id = event["user"]
    message_text = event["text"]
    channel_id = event["channel"]
    # Only reply in a thread if the original message was in a thread
    thread_ts = event.get("thread_ts")
    # Fetch channel display name
    try:
        channel_info = app.client.conversations_info(channel=channel_id)
        channel_display_name = channel_info.get("channel", {}).get("name", channel_id)
    except Exception as e:
        logger.error(f"Error fetching channel display name: {e}")
        channel_display_name = channel_id

    try:
        # Fetch user email
        user_info = app.client.users_info(user=user_id)
        profile = user_info.get("user", {}).get("profile", {})
        user_email = profile.get("email", "unknown.email@example.com")
        display_name = profile.get("display_name", "")
        # real_name = profile.get("real_name", "")

    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
        user_email = "unknown.email@example.com"
        display_name = "Unknown User"

    # Enrich the message with user info
    enriched_message = (
        f"Message from {display_name} {user_email} ({user_id}): {message_text}"
    )

    try:
        initial_reply = say(text="🧠 Thinking...", thread_ts=thread_ts)
        reply_ts = initial_reply["ts"]
    except Exception as e:
        logger.error(f"Error posting initial reply: {e}")
        return

    final_answer = ""
    thoughts = []
    try:
        session_id = await engine_modules.get_or_create_session(
            session_service=session_service, user_id=f"Slack: {channel_display_name}"
        )
        async for response in agent_app.async_stream_query(  # type: ignore
            user_id=f"Slack: {channel_display_name}",
            session_id=session_id,
            message=enriched_message,
        ):
            response_author = response.get("author")
            # logger.info("[EVENT]" + "-" * 40)
            # logger.info(response)

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
                            channel=channel_id,
                            thread_ts=reply_ts,
                            text=thought,
                        )

                    elif part.get("function_call"):
                        fc = part.get("function_call")
                        thought = f"🔧 *Tool Call* ({response_author}): `{fc.get('name')}` with args: `{fc.get('args')}`"
                        thoughts.append(thought)
                        if show_tools:
                            app.client.chat_postMessage(
                                channel=channel_id,
                                thread_ts=reply_ts,
                                text=thought,
                            )
                    elif part.get("function_response"):
                        fr = part.get("function_response")
                        thought = f"📥 *Tool Response* for `{fr.get('name')}`: `{fr.get('response')}`"
                        thoughts.append(thought)
                        if show_tools:
                            app.client.chat_postMessage(
                                channel=channel_id,
                                thread_ts=reply_ts,
                                text=thought,
                            )
            except json.JSONDecodeError as e:
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=reply_ts,
                    text=f"ERROR: Ran into JSON decoding issue: {str(e)}",
                )
            except Exception as e:
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=reply_ts,
                    text=f"ERROR: Ran into an unexpected issue: {str(e)}",
                )

    except Exception as e:
        final_answer = f"Sorry, an error occurred: {e}"
        logger.error(final_answer)

    # If there's no final answer, delete the 'Thinking...' message and stop.
    if not final_answer:
        logger.info("Agent provided no final answer. Deleting 'Thinking...' message.")
        try:
            app.client.chat_delete(channel=channel_id, ts=reply_ts)
        except Exception as e:
            logger.error(f"Error deleting 'Thinking...' message: {e}")
        return

    # Otherwise, update the message with the final answer and post thoughts.
    try:
        # if thoughts:
        # thought_text = "\n".join(thoughts)
        # for thought in thoughts:
        #     app.client.chat_postMessage(
        #         channel=channel_id,
        #         thread_ts=reply_ts,
        #         text=thought,
        #     )
        chunks = [final_answer[i : i + 3900] for i in range(0, len(final_answer), 3900)]
        # Send the first chunk as an update to the initial reply
        app.client.chat_update(channel=channel_id, ts=reply_ts, text=chunks[0])
        # Send any remaining chunks as new messages in the same thread
        if len(chunks) > 1:
            for chunk in chunks[1:]:
                app.client.chat_postMessage(
                    channel=channel_id, thread_ts=reply_ts, text=chunk
                )
    except Exception as e:
        logger.error(f"Error updating message or posting thoughts: {e}")


async def process_message_for_context(body):
    """
    Silently sends a message to the agent engine for context-building.
    """
    event = body["event"]
    session_id_for_channel = event["channel"]
    user_id = event["user"]
    message_text = event["text"]

    try:
        # Fetch user email
        user_info = app.client.users_info(user=user_id)
        profile = user_info.get("user", {}).get("profile", {})
        user_email = profile.get("email", "unknown.email@example.com")
        display_name = profile.get("display_name", "")
        # real_name = profile.get("real_name", "")

    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
        user_email = "unknown.email@example.com"
        display_name = "Unknown User"

    # Enrich the message with user info
    enriched_message = (
        f"Message from {display_name} {user_email} ({user_id}): {message_text}"
    )

    try:
        session_id = await engine_modules.get_or_create_session(
            session_service=session_service, user_id=f"Slack: {session_id_for_channel}"
        )

        await engine_modules.add_messages(
            session_service=session_service,
            session_id=session_id,
            user_id=f"Slack: {session_id_for_channel}",
            author=display_name,
            message=enriched_message,
        )

        logger.info(
            f"Processed message for context in channel {session_id_for_channel}"
        )
    except Exception as e:
        logger.error(f"Error processing message for context: {e}")


# --- Slack Event Handlers ---
@app.event("app_mention")
def handle_app_mention(body, say, ack):
    ack()
    asyncio.run(query_agent_and_reply(body, say))


@app.event("message")
def handle_message_events(body, say, logger):
    event = body["event"]
    # Ignore messages from bots and any message with a subtype (e.g., edits, file shares)
    if "bot_id" in event or "subtype" in event:
        return

    channel_type = event.get("channel_type")
    if channel_type == "im":
        logger.info("Received DM, processing for reply...")
        asyncio.run(query_agent_and_reply(body, say))
        return

    if channel_type in ["channel", "group"]:
        logger.info("Received channel message, processing for context...")
        asyncio.run(process_message_for_context(body))
        return


# --- App Start ---
if __name__ == "__main__":
    logger.info("🤖 Slack bot is running in Socket Mode...")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()
