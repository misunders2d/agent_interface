import os
import logging
import asyncio
import json
from typing import Dict, Any, List

from telegram import Update, ChatAction, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -- repo imports --
import sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import engine_modules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = config.TELEGRAM_BOT_TOKEN
agent_app = engine_modules.get_remote_agent()
session_service = engine_modules.get_session_service()
artifact_service = engine_modules.get_artifact_service()
memory_service = engine_modules.get_memory_service()

show_tools = False
sessions_dict: Dict[str, str] = {}


async def get_session_id(user_id: str) -> str:
    if user_id in sessions_dict:
        logger.info(f"Using cached session ID for user {user_id}")
        return sessions_dict[user_id]
    else:
        logger.info(f"Creating / fetching new session ID for user {user_id}")
        session_id = await engine_modules.get_or_create_session(session_service=session_service, user_id=user_id)
        sessions_dict[user_id] = session_id
        return session_id


async def download_files_from_update(update: Update) -> List[Dict[str, Any]]:
    files_info: List[Dict[str, Any]] = []
    message = update.effective_message
    # Documents and photos
    if message.document:
        file = await message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        files_info.append({
            "name": message.document.file_name or "document",
            "mime_type": message.document.mime_type or "application/octet-stream",
            "content": bytes(file_bytes),
            "size": message.document.file_size or len(file_bytes),
        })
    if message.photo:
        # photos is a list of PhotoSize, take the largest
        largest = message.photo[-1]
        file = await largest.get_file()
        file_bytes = await file.download_as_bytearray()
        files_info.append({
            "name": f"photo_{largest.file_id}",
            "mime_type": "image/jpeg",
            "content": bytes(file_bytes),
            "size": len(file_bytes),
        })
    return files_info


def prepare_enriched_message(display_name: str, user_email: str, user_id: str, text: str, files: List[Dict[str, Any]]) -> str:
    enriched = f"Message from {display_name} {user_email} ({user_id}): {text}"
    if files:
        enriched += " Attached files: " + ", ".join([f.get("name", "file") for f in files]) + "."
    return enriched


async def query_agent_and_reply_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    # Prepare event_info similar to Slack implementation
    event_info: Dict[str, Any] = {}
    event_info["user_id"] = user.id if user else "unknown_user"
    event_info["user_email"] = getattr(user, "email", "unknown.email@example.com")
    event_info["display_name"] = user.full_name if user else "Unknown User"
    event_info["chat_id"] = chat.id
    event_info["message_text"] = message.text or ""
    event_info["session_user_id"] = f"Telegram: {chat.id}"

    # Download files if any
    files = await download_files_from_update(update)
    event_info["files_attached"] = files
    event_info["enriched_message"] = prepare_enriched_message(
        event_info["display_name"], event_info["user_email"], event_info["user_id"], event_info["message_text"], files
    )

    try:
        # Send typing action
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    except Exception as e:
        logger.error(f"Failed to send chat action: {e}")

    try:
        thinking_msg = await message.reply_text("🧠 Thinking...")
    except Exception as e:
        logger.error(f"Error posting initial reply: {e}")
        return

    final_answer = ""
    last_text = ""
    thoughts: List[str] = []
    session_id = None

    try:
        session_id = await get_session_id(event_info["session_user_id"]) 

        # Temporarily set user_id for personal memories access (match Slack behavior)
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

        async for response in agent_app.async_stream_query(
            user_id=event_info["session_user_id"],
            session_id=session_id,
            message=prepared_message,
        ):
            if not response:
                continue
            try:
                response_author = response.get("author")
                if response_author == "answer_validator_agent":
                    continue

                parts = response.get("content", {}).get("parts", [])
                for part in parts:
                    if part.get("text") and not part.get("thought"):
                        final_answer += part.get("text")
                    if part.get("text") and part.get("thought"):
                        thought = f"🧠 *Thought* ({response_author}): {part.get('text')}"
                        thoughts.append(thought)
                        await thinking_msg.reply_text(thought)
                        last_text = thought

                    elif part.get("function_call"):
                        fc = part.get("function_call")
                        thought = f"🔧 *Tool Call* ({response_author}): `{fc.get('name')}` with args: `{fc.get('args')}`"
                        thoughts.append(thought)
                        if show_tools:
                            await thinking_msg.reply_text(thought)
                        last_text = thought

                    elif part.get("function_response"):
                        fr = part.get("function_response")
                        thought = f"📥 *Tool Response* for `{fr.get('name')}`: `{fr.get('response')}`"
                        thoughts.append(thought)
                        if show_tools:
                            await thinking_msg.reply_text(thought)
                        last_text = thought

            except json.JSONDecodeError as e:
                await thinking_msg.reply_text(f"ERROR: Ran into JSON decoding issue: {str(e)}")
            except Exception as e:
                await thinking_msg.reply_text(f"ERROR: Ran into an unexpected issue: {str(e)}")

        # change user_id back to chat id to prevent personal memories access
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
            await query_agent_and_reply_telegram(update, context)
            return
        else:
            final_answer = f"Sorry, an error occurred: {e}"
            logger.error(final_answer)

    # If there's no final answer, delete the 'Thinking...' message and stop.
    if not final_answer and not last_text:
        logger.info("Agent provided no final answer. Deleting 'Thinking...' message.")
        try:
            await thinking_msg.delete()
        except Exception as e:
            logger.error(f"Error deleting 'Thinking...' message: {e}")
        return

    # Otherwise, update the message with the final answer and post thoughts.
    try:
        post_message = final_answer or last_text
        # Telegram max message length is ~4096 characters
        chunks = [post_message[i : i + 3900] for i in range(0, len(post_message), 3900)]
        await thinking_msg.reply_text("⬇️⬇️⬇️Final message:")
        for chunk in chunks:
            await thinking_msg.reply_text(chunk)
        await thinking_msg.edit_text("✅ Check my reply in thread. Ping me there if you need to continue conversation...")
    except Exception as e:
        logger.error(f"Error updating message or posting thoughts: {e}")


async def process_message_for_context_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    event_info = {}
    event_info["user_id"] = user.id if user else "unknown_user"
    event_info["display_name"] = user.full_name if user else "Unknown User"
    event_info["chat_id"] = chat.id
    event_info["message_text"] = message.text or ""
    event_info["session_user_id"] = f"Telegram: {chat.id}"

    files = await download_files_from_update(update)
    event_info["files_attached"] = files

    try:
        session_id = await get_session_id(event_info["session_user_id"]) 

        await engine_modules.update_session(
            session_service=session_service,
            session_id=session_id,
            user_id=event_info["session_user_id"],
            author=event_info["display_name"],
            message=prepare_enriched_message(event_info["display_name"], getattr(user, "email", "unknown.email@example.com"), event_info["user_id"], event_info["message_text"], files),
            file_list=event_info.get("files_attached", []),
        )

        logger.info(f"Processed message for context in chat {event_info['chat_id']}")
    except Exception as e:
        logger.error(f"Error processing message for context: {e}")


async def delete_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Deleting session...")
    chat_id = update.effective_chat.id
    session_user_id = f"Telegram: {chat_id}"
    session_id = await get_session_id(session_user_id)

    try:
        await engine_modules.delete_session(session_service=session_service, user_id=session_user_id, session_id=session_id)
        sessions_dict.pop(session_user_id, None)
        await update.message.reply_text("🗑️ Deleted session for this chat.")
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        await update.message.reply_text(f"❌ Error deleting session: {e}")


async def save_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Saving session...")
    chat_id = update.effective_chat.id
    session_user_id = f"Telegram: {chat_id}"
    session_id = await get_session_id(session_user_id)

    session = await engine_modules.get_session(session_service=session_service, user_id=session_user_id, session_id=session_id)

    if session:
        try:
            await memory_service.add_session_to_memory(session)
            await update.message.reply_text("💾 Saved session for this chat. It's now safe to delete the session using /delete_session command")
        except Exception as e:
            logger.error(f"Error saving session: {e}")
            await update.message.reply_text(f"❌ Error saving session: {e}")


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in environment. Exiting.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("delete_session", delete_session_command))
    app.add_handler(CommandHandler("save_session", save_session_command))

    # Message handler: ignore bot messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Entity("bot_command"), handle_messages))

    # Start polling by default
    logger.info("🤖 Telegram bot is running (polling)...")
    app.run_polling()


# Glue handler to route messages to reply or context
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await query_agent_and_reply_telegram(update, context)
    else:
        await process_message_for_context_telegram(update, context)


if __name__ == "__main__":
    main()
