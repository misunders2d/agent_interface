import json
import requests
import time
import streamlit as st
from api_modules import get_or_create_session, ENDPOINT, get_identity_token


# --- Query ---
def query_agent(
    user_id: str, message_text: str, show_tool_calls=True, show_thoughts=True
):
    session_id = get_or_create_session(user_id)

    url = f"{ENDPOINT}:streamQuery?alt=sse"
    headers = {
        "Authorization": f"Bearer {get_identity_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "class_method": "async_stream_query",
        "input": {
            "user_id": user_id,
            "session_id": session_id,
            "message": message_text,
        },
    }

    resp = requests.post(url, headers=headers, data=json.dumps(body), stream=True)
    resp.raise_for_status()

    for chunk in resp.iter_lines():
        if not chunk:
            continue
        try:
            event = json.loads(chunk.decode("utf-8"))
        except Exception:
            continue
        event_author = event.get("author", "")

        if event_author == "answer_validator_agent":
            validator_answer = json.loads(
                event.get("content", {}).get("parts", [{}])[0].get("text", "{}")
            )
            st.toast(validator_answer)
            # if not validator_answer.get("answer_needed"):
            #     st.toast("No answer required")
        else:
            parts = event.get("content", {}).get("parts", [])
            for part in parts:

                # Case 1: It's regular text for the final reply
                if part.get("text", "") and not part.get("thought"):
                    message_data = {
                        "role": "assistant",
                        "type": "response",
                        "label": "Response",
                        "content": f'{event_author}: {part.get("text")}',
                    }
                    st.session_state.messages.append(message_data)
                    for char in message_data["content"]:
                        yield char + ""
                        time.sleep(0.01)

                # Case 2: It's a "thought" from the planner
                elif part.get("thought") and show_thoughts:
                    thought_data = {
                        "role": "thought",
                        "type": "thought",
                        "label": "Thought",
                        "content": f"""{event_author}'s thought: {part.get("text")}""",
                    }
                    st.session_state.messages.append(thought_data)
                    with st.expander(thought_data["label"], icon="🧠"):
                        st.info(thought_data["content"])

                # Case 3: It's a tool call
                elif part.get("function_call") and show_tool_calls:
                    fc = part.get("function_call")
                    thought_data = {
                        "role": "thought",
                        "type": "tool_call",
                        "label": f"Tool Call: `{fc.get('name')}`",
                        "content": f"Author: {event_author}\nArguments: `{fc.get('args')}`",
                    }
                    st.session_state.messages.append(thought_data)
                    with st.expander(thought_data["label"], icon="🔧"):
                        st.json(thought_data["content"])

                # Case 4: It's a tool response
                elif part.get("function_response") and show_tool_calls:
                    fr = part.get("function_response")
                    thought_data = {
                        "role": "thought",
                        "type": "tool_response",
                        "label": f"Tool Response: `{fr.get('name')}`",
                        "content": f'Author: {event_author}\n Tool response:{fr.get("response")}',
                    }
                    st.session_state.messages.append(thought_data)
                    with st.expander(thought_data["label"], icon="📥"):
                        st.json(thought_data["content"])
