"""
Streamlit chat UI -- the Databricks App entrypoint (see app.yaml).

Deliberately thin: all the actual logic (tool schemas, the agent loop,
propose/confirm safety) lives in agent.py / tools/, so it's testable
without spinning up Streamlit.
"""

import streamlit as st

import config
from agent import run_agent
from tools.pending_actions import PendingActionStore

st.set_page_config(page_title="Genie-Like UC & Lakebase Agent", page_icon="🧞", layout="centered")


def get_current_user() -> str:
    """Databricks Apps inject the signed-in viewer's identity via request
    headers on the proxy in front of the app. Header name has varied across
    Databricks App platform versions -- check st.context.headers in your
    workspace and adjust if this doesn't resolve. Falls back to a generic
    label so the demo still runs standalone (e.g. via `streamlit run` locally)."""
    try:
        headers = st.context.headers
        for key in ("X-Forwarded-Email", "X-Forwarded-User", "X-Forwarded-Preferred-Username"):
            if headers.get(key):
                return headers.get(key)
    except Exception:
        pass
    return "demo-user"


if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_store" not in st.session_state:
    st.session_state.pending_store = PendingActionStore()

st.title("🧞 UC & Lakebase Agent")
st.caption(
    f"UC: `{config.UC_FULL_SCHEMA}` ({', '.join(t.split('.')[-1] for t in config.ALLOWED_UC_TABLES)}) · "
    + (f"Lakebase: `{config.LAKEBASE_DATABASE}` ({', '.join(config.ALLOWED_LAKEBASE_TABLES)})"
       if config.LAKEBASE_ENABLED else "Lakebase: not configured")
)

with st.sidebar:
    st.subheader("About this demo")
    st.markdown(
        "Ask it to **look up** data, or **update** a row. Writes always go "
        "through a propose → confirm step before anything executes -- "
        "the agent will show you the exact statement and wait for you to "
        "say \"yes\" before running it."
    )
    st.markdown("**Try:**")
    st.code("Show me the 5 most recent orders", language=None)
    st.code("Mark order 106 as cancelled", language=None)
    if config.LAKEBASE_ENABLED:
        st.code("What support tickets are still open?", language=None)
    if st.button("Reset conversation"):
        st.session_state.messages = []
        st.session_state.pending_store = PendingActionStore()
        st.rerun()

for msg in st.session_state.messages:
    role = msg.get("role")
    if role == "system":
        continue
    if role == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    elif role == "assistant":
        if msg.get("content"):
            with st.chat_message("assistant"):
                st.markdown(msg["content"])
        if msg.get("tool_calls"):
            with st.chat_message("assistant", avatar="🔧"):
                for tc in msg["tool_calls"]:
                    st.caption(f"called `{tc['function']['name']}`  \n`{tc['function']['arguments']}`")
    elif role == "tool":
        with st.expander("🔧 tool result", expanded=False):
            st.code(msg["content"], language="json")

if user_input := st.chat_input("Ask about your data, or ask to update something..."):
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.spinner("Thinking..."):
        st.session_state.messages = run_agent(
            st.session_state.messages, st.session_state.pending_store, current_user=get_current_user()
        )
    st.rerun()
