"""
Agent core: builds the tool list (real UCFunctionToolkit tools for
list_tables/describe_table, plus locally-defined tools for query/write on
both UC and Lakebase), runs the tool-calling loop against a Databricks
Foundation Model endpoint, and enforces propose-before-execute for writes.

Kept separate from app.py so the agent logic is testable without Streamlit.
"""

import json
import logging

import openai
from databricks.sdk import WorkspaceClient
from unitycatalog.ai.core.databricks import DatabricksFunctionClient
from unitycatalog.ai.core.utils.function_processing_utils import get_tool_name
from unitycatalog.ai.openai.toolkit import UCFunctionToolkit

import config
from tools import uc_connector, lakebase_connector, audit_log
from tools.pending_actions import PendingActionStore
from tools.uc_connector import UCToolError
from tools.lakebase_connector import LakebaseToolError

logger = logging.getLogger(__name__)

_w = WorkspaceClient()
_uc_client = DatabricksFunctionClient()

# --- LLM client -------------------------------------------------------
# Deliberately NOT minting a fresh PAT per run (w.tokens.create(...)) like
# the original notebook did -- that litters the workspace with tokens and
# needs token-creation permission the app's service principal shouldn't
# necessarily have. Some databricks-sdk versions expose a ready-made OpenAI
# client wrapper (serving_endpoints.get_open_ai_client()); as of sdk 0.94
# that method doesn't exist yet, so the fallback below builds one from the
# app's own ambient auth. It deliberately uses config.authenticate() rather
# than config.token: token is only populated for static-PAT auth, while a
# Databricks App's service principal typically authenticates via OAuth,
# where authenticate() is the SDK's abstraction for "give me a fresh bearer
# token right now" regardless of which auth method is actually in play.
try:
    llm_client = _w.serving_endpoints.get_open_ai_client()
except AttributeError:
    def _bearer_token() -> str:
        headers = _w.config.authenticate()
        return headers.get("Authorization", "").split(" ")[-1]

    llm_client = openai.OpenAI(
        base_url=f"{_w.config.host}/serving-endpoints",
        api_key=_bearer_token(),
    )
    # Demo simplification: this token is fetched once at import time. OAuth
    # tokens expire (~1hr); a long-running deployment should refresh this
    # periodically (or re-check for get_open_ai_client() on a newer sdk
    # version, which handles refresh for you) rather than caching it for
    # the process lifetime.

# --- UC-native tools (real UCFunctionToolkit, not hand-rolled schema conversion) ---
_UC_NATIVE_FUNCS = [f"{config.UC_FULL_SCHEMA}.list_tables", f"{config.UC_FULL_SCHEMA}.describe_table"]
_uc_toolkit = UCFunctionToolkit(function_names=_UC_NATIVE_FUNCS, client=_uc_client)
# Map the LLM-facing tool name back to the fully-qualified UC function name
# ourselves (same transform the toolkit uses internally) rather than assuming
# the tool name equals the unqualified function name -- the toolkit may
# sanitize/prefix names differently than we'd guess.
_uc_native_name_to_full = {get_tool_name(fn): fn for fn in _UC_NATIVE_FUNCS}

# --- Locally-defined tools (real dynamic execution -- see tools/uc_connector.py docstring) ---
_local_tools = [
    {
        "type": "function",
        "function": {
            "name": "query_uc_table",
            "description": "Read rows from an allowlisted Unity Catalog table, with an optional single filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "enum": config.ALLOWED_UC_TABLES},
                    "filter_column": {"type": "string", "description": "Column to filter on. Omit for no filter."},
                    "filter_operator": {"type": "string", "enum": sorted(config.ALLOWED_FILTER_OPERATORS)},
                    "filter_value": {"type": "string"},
                    "row_limit": {"type": "integer", "description": f"Max rows, default {config.DEFAULT_ROW_LIMIT}, cap {config.MAX_ROW_LIMIT}."},
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_uc_write",
            "description": (
                "Propose an update to a Unity Catalog table. Does NOT execute anything -- "
                "returns a preview and a confirmation_id. You must show the preview to the "
                "user and get their explicit confirmation before calling confirm_write."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "enum": config.ALLOWED_UC_TABLES},
                    "column_name": {"type": "string"},
                    "new_value": {"type": "string"},
                    "filter_column": {"type": "string"},
                    "filter_operator": {"type": "string", "enum": sorted(config.ALLOWED_FILTER_OPERATORS)},
                    "filter_value": {"type": "string"},
                },
                "required": ["table_name", "column_name", "new_value", "filter_column", "filter_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_write",
            "description": (
                "Execute a previously proposed write (from propose_uc_write or "
                "propose_lakebase_write) by its confirmation_id. Only call this after "
                "the user has explicitly confirmed in their most recent message -- "
                "never infer confirmation, always wait for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"confirmation_id": {"type": "string"}},
                "required": ["confirmation_id"],
            },
        },
    },
]

if config.LAKEBASE_ENABLED:
    _local_tools.append({
        "type": "function",
        "function": {
            "name": "read_lakebase_table",
            "description": "Read rows from an allowlisted Lakebase (Postgres) table, with an optional single filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "enum": config.ALLOWED_LAKEBASE_TABLES},
                    "filter_column": {"type": "string"},
                    "filter_operator": {"type": "string", "enum": sorted(config.ALLOWED_FILTER_OPERATORS)},
                    "filter_value": {"type": "string"},
                    "row_limit": {"type": "integer"},
                },
                "required": ["table_name"],
            },
        },
    })
    _local_tools.append({
        "type": "function",
        "function": {
            "name": "propose_lakebase_write",
            "description": (
                "Propose an update to a Lakebase table. Does NOT execute anything -- "
                "returns a preview and a confirmation_id, to be executed via confirm_write "
                "only after the user explicitly confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "enum": config.ALLOWED_LAKEBASE_TABLES},
                    "column_name": {"type": "string"},
                    "new_value": {"type": "string"},
                    "filter_column": {"type": "string"},
                    "filter_operator": {"type": "string", "enum": sorted(config.ALLOWED_FILTER_OPERATORS)},
                    "filter_value": {"type": "string"},
                },
                "required": ["table_name", "column_name", "new_value", "filter_column", "filter_value"],
            },
        },
    })

ALL_TOOLS = list(_uc_toolkit.tools) + _local_tools

SYSTEM_PROMPT = f"""You are a data assistant for a Databricks workspace with two backends:
- Unity Catalog tables (analytical data): {', '.join(config.ALLOWED_UC_TABLES)}
- Lakebase Postgres tables (operational data): {', '.join(config.ALLOWED_LAKEBASE_TABLES) if config.LAKEBASE_ENABLED else '(not configured for this deployment)'}

Rules:
1. If you're unsure what tables or columns exist, call list_tables / describe_table first rather than guessing.
2. Reads (query_uc_table, read_lakebase_table) can be called directly.
3. Writes are two-step and MUST stay two-step: call propose_uc_write or propose_lakebase_write first,
   show the user the exact preview text you get back, and ask them to confirm. Only call confirm_write
   after the user's most recent message clearly confirms (e.g. "yes", "confirm", "do it"). If they haven't
   confirmed yet, stop and wait -- do not call confirm_write speculatively.
4. Every write is audit-logged automatically; you don't need to mention that unless asked.
5. Be concise. Summarize query results in a short table or list rather than dumping raw JSON.
"""


def _dispatch_local(name: str, args: dict, pending_store: PendingActionStore, current_user: str) -> str:
    try:
        if name == "query_uc_table":
            return uc_connector.query_uc_table(**args)
        if name == "propose_uc_write":
            pending = uc_connector.build_pending_uc_write(**args)
            confirmation_id = pending_store.put(pending)
            return json.dumps({"confirmation_id": confirmation_id, "preview": pending["preview"],
                                "note": "Not executed yet. Ask the user to confirm, then call confirm_write."})
        if name == "read_lakebase_table":
            return lakebase_connector.query_lakebase_table(**args)
        if name == "propose_lakebase_write":
            pending = lakebase_connector.build_pending_lakebase_write(**args)
            confirmation_id = pending_store.put(pending)
            return json.dumps({"confirmation_id": confirmation_id, "preview": pending["preview"],
                                "note": "Not executed yet. Ask the user to confirm, then call confirm_write."})
        if name == "confirm_write":
            pending = pending_store.pop(args["confirmation_id"])
            if pending["target"] == "uc":
                result = uc_connector.execute_pending_write(pending)
            else:
                result = lakebase_connector.execute_pending_write(pending)
            audit_log.log_write(current_user, pending["target"], pending["table_name"],
                                 pending["preview"], result, success=True)
            return result
        return f"Unknown tool: {name}"
    except (UCToolError, LakebaseToolError, KeyError) as e:
        # KeyError.__str__ wraps its message in repr() quotes -- unwrap so
        # the error text shown to the user/model isn't double-quoted.
        message = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        if name == "confirm_write":
            audit_log.log_write(current_user, "unknown", "unknown", "", message, success=False)
        return f"Error: {message}"


def run_agent(messages: list, pending_store: PendingActionStore, current_user: str = "unknown") -> list:
    """Runs one user turn to completion (including any tool calls) and
    returns the updated messages list. Caller (app.py) owns persisting
    `messages` and `pending_store` across turns -- both need to survive
    for the propose/confirm flow to work across a multi-turn conversation."""
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    for _ in range(config.MAX_AGENT_ITERATIONS):
        response = llm_client.chat.completions.create(
            model=config.MODEL_ENDPOINT,
            messages=messages,
            tools=ALL_TOOLS,
            tool_choice="auto",
        )
        assistant_message = response.choices[0].message
        assistant_entry = {"role": "assistant", "content": assistant_message.content}
        if assistant_message.tool_calls:
            assistant_entry["tool_calls"] = [tc.model_dump() for tc in assistant_message.tool_calls]
        messages.append(assistant_entry)

        if not assistant_message.tool_calls:
            return messages

        for tool_call in assistant_message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                args = {}

            if name in _uc_native_name_to_full:
                try:
                    result = _uc_client.execute_function(function_name=_uc_native_name_to_full[name], parameters=args)
                    tool_result = result.error if result.error else (result.value or "[]")
                except Exception as e:
                    tool_result = f"Error: {e}"
            else:
                tool_result = _dispatch_local(name, args, pending_store, current_user)

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

    messages.append({"role": "assistant", "content": "I've hit my step limit for this turn -- could you rephrase or break that into smaller steps?"})
    return messages
