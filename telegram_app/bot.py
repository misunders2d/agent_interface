import os
import json
import logging
import sys
# --- Initialization ---
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# import asyncio
from typing import Any, Dict, List
import config

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    # Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import engine_modules
# Suppress absl warnings
from absl import logging as absl_logging

absl_logging.set_verbosity(absl_logging.ERROR)




logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

agent_app = engine_modules.get_remote_agent()
session_service = engine_modules.get_session_service()
artifact_service = engine_modules.get_artifact_service()
memory_service = engine_modules.get_memory_service()


show_tools = False
sessions_dict = {}

# --- Constants ---
TELEGRAM_TOKEN = config.TELEGRAM_BOT_TOKEN


# --- Helper Functions ---
async def download_files_from_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> List[Dict[str, Any]]:
    """Download files (documents and photos) from a Telegram update."""
    files_info: List[Dict[str, Any]] = []
    message = update.effective_message
    if not message:
        return files_info

    async def download_file(file_id: str) -> bytes:
        file = await context.bot.get_file(file_id)
        content = await file.download_as_bytearray()
        return bytes(content)

    # Documents
    if message.document:
        file_content = await download_file(message.document.file_id)
        files_info.append(
            {
                "name": message.document.file_name or "document",
                "mime_type": message.document.mime_type or "application/octet-stream",
                "content": file_content,
                "size": message.document.file_size,
            }
        )
    # Photos
    if message.photo:
        largest_photo = message.photo[-1]
        file_content = await download_file(largest_photo.file_id)
        files_info.append(
            {
                "name": f"photo_{largest_photo.file_id}.jpg",
                "mime_type": "image/jpeg",
                "content": file_content,
                "size": largest_photo.file_size,
            }
        )
    return files_info


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


async def get_event_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Dict[str, Any]:
    """Extracts and structures event information from a Telegram update."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    # Basic info
    user_id = str(user.id) if user else "unknown_user"
    display_name = user.full_name if user else "Unknown User"
    chat_id = str(chat.id) if chat else "unknown_chat"

    # Session user ID should be based on the chat, to mimic Slack's channel-based sessions
    session_user_id = f"Telegram: {chat_id}"
    # Personal user ID for memory access
    personal_user_id = f"Telegram: {user_id} ({display_name})"

    event_info: Dict[str, Any] = {
        "user_id": user_id,
        "display_name": display_name,
        "chat_id": chat_id,
        "chat_type": chat.type if chat else "N/A",
        "message_text": message.text if message and message.text else "",
        "session_user_id": session_user_id,
        "personal_user_id": personal_user_id,
    }

    # Handle files
    files_info = await download_files_from_update(update, context)

    # Enrich the message
    enriched_message = (
        f"Message from {display_name} ({user_id}): {event_info['message_text']}"
    )
    if files_info:
        event_info["files_attached"] = files_info
        enriched_message += (
            f" Attached files: {', '.join([f['name'] for f in files_info])}."
        )

    event_info["enriched_message"] = enriched_message

    # logger.info(f"[EVENT INFO]:\n\n{event_info}\n\n\n") # For debugging
    return event_info


# --- Core Agent Logic ---
async def query_agent_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Queries the agent and replies to the user.
    """
    if not update.effective_message or not update.effective_chat:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    event_info = await get_event_info(update, context)

    final_answer = ""
    last_text = ""
    thoughts = []
    session_id = None
    try:
        session_id = await get_session_id(event_info["session_user_id"])

        # Temporarily change user_id for personal memories access
        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            state_delta={"user_id": f'Telegram: {event_info["user_id"]}'},
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
            if not response:
                continue

            response_author = response.get("author")

            try:

                if response_author == "answer_validator_agent":
                    continue

                parts = response.get("content", {}).get("parts", [])
                for part in parts:

                    await context.bot.send_chat_action(
                        chat_id=update.effective_chat.id, action=ChatAction.TYPING
                    )

                    if part.get("text") and not part.get("thought"):
                        final_answer += part.get("text")

                    elif part.get("text") and part.get("thought"):
                        thought = (
                            f"🧠 *Thought* ({response_author}): {part.get('text')}"
                        )
                        thoughts.append(thought)
                        if show_tools:
                            await update.effective_message.reply_text(thought)
                        last_text = thought

                    elif part.get("function_call"):
                        fc = part.get("function_call")
                        thought = f"🔧 *Tool Call* ({response_author}): `{fc.get('name')}` with args: `{fc.get('args')}`"
                        thoughts.append(thought)
                        if show_tools:
                            await update.effective_message.reply_text(thought)
                        last_text = thought

                    elif part.get("function_response"):
                        fr = part.get("function_response")
                        thought = f"📥 *Tool Response* for `{fr.get('name')}`: `{fr.get('response')}`"
                        thoughts.append(thought)
                        if show_tools:
                            await update.effective_message.reply_text(thought)
                        last_text = thought

            except json.JSONDecodeError as e:
                await update.effective_message.reply_text(
                    f"ERROR: Ran into JSON decoding issue: {str(e)}"
                )
            except Exception as e:
                await update.effective_message.reply_text(
                    f"ERROR: Ran into an unexpected issue: {str(e)}"
                )

        # Change user_id back
        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            state_delta={
                "user_id": event_info["session_user_id"]
            },  # Revert to chat-based ID
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
            await query_agent_and_reply(update, context)  # Recurse
            return  # Important to exit after recursion
        else:
            final_answer = f"Sorry, an error occurred: {e}"
            logger.error(final_answer)

    if not final_answer and not last_text:
        logger.info("Agent provided no final answer.")
        return

    try:
        post_message = final_answer or last_text
        # Telegram max message length is 4096
        chunks = [post_message[i : i + 4096] for i in range(0, len(post_message), 4096)]
        for chunk in chunks:
            await update.effective_message.reply_text(chunk)

    except Exception as e:
        logger.error(f"Error posting final answer: {e}")
        await update.effective_message.reply_text(
            f"Sorry, an error occurred while sending the reply: {e}"
        )


async def process_message_for_context(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """
    Silently sends a message to the agent engine for context-building.
    """
    if not update.effective_message:
        return

    event_info = await get_event_info(update, context)
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

        logger.info(f"Processed message for context in chat {event_info['chat_id']}")
    except Exception as e:
        logger.error(f"Error processing message for context: {e}")


# --- Command Handlers ---
async def delete_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    session_user_id = f"Telegram: {chat_id}"

    if session_user_id not in sessions_dict and update.effective_message:
        await update.effective_message.reply_text(
            "No active session found for this chat."
        )
        return

    session_id = sessions_dict[session_user_id]

    if update.effective_message:
        try:
            await engine_modules.delete_session(
                session_service=session_service,
                user_id=session_user_id,
                session_id=session_id,
            )
            sessions_dict.pop(session_user_id, None)
            await update.effective_message.reply_text(
                "🗑️ Deleted session for this chat."
            )
        except Exception as e:
            error_msg = f"Error deleting session: {e}"
            logger.error(error_msg)
            await update.effective_message.reply_text(f"❌ {error_msg}")


async def save_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    session_user_id = f"Telegram: {chat_id}"

    if update.effective_message:
        if session_user_id not in sessions_dict:
            await update.effective_message.reply_text(
                "No active session found for this chat."
            )
            return

        session_id = await get_session_id(session_user_id)

        try:
            session = await engine_modules.get_session(
                session_service=session_service,
                user_id=session_user_id,
                session_id=session_id,
            )
            if session:
                await memory_service.add_session_to_memory(session)
                await update.effective_message.reply_text(
                    "💾 Saved session for this chat. It's now safe to delete the session using `/delete_session` command"
                )
            else:
                await update.effective_message.reply_text(
                    "Could not find session to save."
                )
        except Exception as e:
            error_msg = f"Error saving session: {e}"
            logger.error(error_msg)
            await update.effective_message.reply_text(f"❌ {error_msg}")


# --- Message Handler ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming messages and routes them."""
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    # Ignore messages from bots
    if message.from_user and message.from_user.is_bot:
        return

    bot = await context.bot.get_me()

    # Route based on chat type
    if chat.type == "private":
        logger.info("Received private message, processing for reply...")
        await query_agent_and_reply(update, context)

    elif chat.type in ["group", "supergroup", "channel"]:
        # In groups, reply only on mention. In channels, always process for context.
        if (
            message.text
            and f"@{bot.username}" in message.text
            and chat.type != "channel"
        ):
            logger.info("Received group mention, processing for reply...")
            await query_agent_and_reply(update, context)
        else:
            logger.info(f"Received {chat.type} message, processing for context...")
            await process_message_for_context(update, context)


# --- Main Application Setup ---
def main():
    """Sets up and runs the Telegram bot."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("delete_session", delete_session_command))
    app.add_handler(CommandHandler("save_session", save_session_command))

    # Message handler for all text and file messages
    app.add_handler(
        MessageHandler(
            (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST)
            & (filters.TEXT | filters.ATTACHMENT)
            & ~filters.COMMAND,
            message_handler,
        )
    )

    logger.info("🤖 Telegram bot is running (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
