import os
import threading
import json
import logging
import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- Initialization ---
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import api_modules

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(
    token=config.SLACK_BOT_TOKEN,
    signing_secret=config.SLACK_SIGNING_SECRET
)

show_tools = False

# --- Agent Query Logic ---
def query_agent_and_reply(body, say):
    """
    Queries the agent in a background thread and posts the response back to Slack.
    """
    event = body["event"]
    user_id = event["user"]
    message_text = event["text"]
    channel_id = event["channel"]
    # Only reply in a thread if the original message was in a thread
    thread_ts = event.get("thread_ts")

    try:
        # Fetch user email
        user_info = app.client.users_info(user=user_id)
        user_email = user_info.get("user", {}).get("profile", {}).get("email", "unknown.email@example.com")
    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
        user_email = "unknown.email@example.com"

    # Enrich the message with user info
    enriched_message = f"Message from {user_email} ({user_id}): {message_text}"

    try:
        initial_reply = say(text="🧠 Thinking...", thread_ts=thread_ts)
        reply_ts = initial_reply["ts"]
    except Exception as e:
        logger.error(f"Error posting initial reply: {e}")
        return

    final_answer = ""
    thoughts = []
    try:
        session_id = api_modules.get_or_create_session(user_id)
        url = f"{config.ENDPOINT}:streamQuery?alt=sse"
        headers = {
            "Authorization": f"Bearer {api_modules.get_identity_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        request_body = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": user_id,
                "session_id": session_id,
                "message": enriched_message,
            },
        }

        with requests.post(url, headers=headers, data=json.dumps(request_body), stream=True) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_lines():
                if not chunk:
                    continue
                try:
                    event_data = json.loads(chunk.decode("utf-8"))
                    event_author = event_data.get("author", "")

                    # Handle validator agent output
                    if event_author == "answer_validator_agent":
                        validator_text = event_data.get("content", {}).get("parts", [{}])[0].get("text", "{}")
                        thoughts.append(f"🕵️ *Validator Agent*: `{validator_text}`")
                        continue

                    # Handle other agents' output
                    parts = event_data.get("content", {}).get("parts", [])
                    for part in parts:
                        if part.get("text") and not part.get("thought"):
                            final_answer += part.get("text")
                        elif part.get("thought"):
                            thoughts.append(f"🧠 *Thought* ({event_author}): {part.get('text')}")
                        elif part.get("function_call") and show_tools:
                            fc = part.get("function_call")
                            thoughts.append(f"🔧 *Tool Call* ({event_author}): `{fc.get('name')}` with args: `{fc.get('args')}`")
                        elif part.get("function_response") and show_tools:
                            fr = part.get("function_response")
                            thoughts.append(f"📥 *Tool Response* for `{fr.get('name')}`: `{fr.get('response')}`")
                except json.JSONDecodeError:
                    continue

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
        app.client.chat_update(channel=channel_id, ts=reply_ts, text=final_answer)
        if thoughts:
            thought_text = "\n".join(thoughts)
            app.client.chat_postMessage(channel=channel_id, thread_ts=reply_ts, text=f"*My thought process:*\n{thought_text}")
    except Exception as e:
        logger.error(f"Error updating message or posting thoughts: {e}")

def process_message_for_context(body):
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
        user_email = user_info.get("user", {}).get("profile", {}).get("email", "unknown.email@example.com")
    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
        user_email = "unknown.email@example.com"

    # Enrich the message with user info
    enriched_message = f"Message from {user_email} ({user_id}): {message_text}"

    try:
        session_id = api_modules.get_or_create_session(session_id_for_channel)
        url = f"{config.ENDPOINT}:query"
        headers = {
            "Authorization": f"Bearer {api_modules.get_identity_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        request_body = {
            "class_method": "async_query",
            "input": {"user_id": user_id, "session_id": session_id, "message": enriched_message},
        }

        resp = requests.post(url, headers=headers, data=json.dumps(request_body))
        resp.raise_for_status()
        logger.info(f"Processed message for context in channel {session_id_for_channel}")
    except Exception as e:
        logger.error(f"Error processing message for context: {e}")

# --- Slack Event Handlers ---
@app.event("app_mention")
def handle_app_mention(body, say, ack):
    ack()
    thread = threading.Thread(target=query_agent_and_reply, args=(body, say))
    thread.start()

@app.event("message")
def handle_message_events(body, say, logger):
    event = body["event"]
    # Ignore messages from bots and any message with a subtype (e.g., edits, file shares)
    if "bot_id" in event or "subtype" in event:
        return

    channel_type = event.get("channel_type")
    if channel_type == "im":
        logger.info("Received DM, processing for reply...")
        thread = threading.Thread(target=query_agent_and_reply, args=(body, say))
        thread.start()
        return

    if channel_type == "channel":
        logger.info("Received channel message, processing for context...")
        thread = threading.Thread(target=process_message_for_context, args=(body,))
        thread.start()
        return

# --- App Start ---
if __name__ == "__main__":
    logger.info("🤖 Slack bot is running in Socket Mode...")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()
