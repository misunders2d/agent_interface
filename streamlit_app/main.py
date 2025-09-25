import sys
import os
import streamlit as st

# Add the project root to the Python path to allow importing shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from streamlit_app.login import require_login
from api_modules import list_sessions, create_session
from streamlit_app.query_streamlit import query_agent

# === Config ===
require_login()


def run_session_creation(user_id):
    new_session = create_session(user_id)
    st.toast(f"New session created: {new_session}")


USER_ID = (
    st.user.email
    if "email" in st.user and isinstance(st.user.email, str)
    else "undefined"
)
user_picture = (
    st.user.picture
    if st.user.picture and isinstance(st.user.picture, str)
    else "media/user_avatar.jpg"
)
sessions = list_sessions(USER_ID)
session_ids = [x["id"] for x in sessions]
with st.sidebar:
    create_chat_col, delete_chat_col = st.columns([1, 1])
    new_chat = create_chat_col.button(
        "Start new chat",
        on_click=run_session_creation,
        kwargs={"user_id": USER_ID},
        type="tertiary",
    )
    delete_chat = delete_chat_col.button("Delete chat", type="tertiary")
    show_tool_calls = st.checkbox("Show tool calls", value=False)
    show_thoughts = st.checkbox("Show thoughts", value=False)
    sessions_list = st.selectbox("Sessions", session_ids)


# --- Streamlit UI ---
st.set_page_config(layout="wide")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    if msg["role"] == "thought":
        icon = "🧠"
        if msg.get("type") == "tool_call":
            icon = "🔧"
        elif msg.get("type") == "tool_response":
            icon = "📥"

        with st.expander(msg["label"], icon=icon):
            if msg.get("type") == "tool_response":
                st.json(msg["content"])
            else:
                st.info(msg["content"])
    else:
        with st.chat_message(
            msg["role"],
            avatar=(user_picture if msg["role"] == "user" else "media/haken.jpg"),
        ):
            st.markdown(msg["content"])


if prompt := st.chat_input("Type your message"):
    # Display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=user_picture):
        st.markdown(prompt)

    # Query agent
    with st.chat_message("assistant", avatar="media/haken.jpg"):
        try:
            st.write_stream(
                query_agent(
                    USER_ID,
                    prompt,
                    show_thoughts=show_thoughts,
                    show_tool_calls=show_tool_calls,
                )
            )
        except Exception as e:
            st.write(f"Sorry, an error occurred, please try later:\n{e}")
