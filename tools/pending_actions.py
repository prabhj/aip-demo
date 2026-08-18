"""
Server-side store for the propose -> confirm write pattern.

This is the piece that actually makes "always confirm before writing" a
real guarantee instead of a prompt instruction the model can skip: when the
agent calls confirm_write(confirmation_id), the statement/parameters that
get executed are whatever was stored here at propose time -- never
anything the LLM supplies fresh in the confirm call. So even if a model is
prompt-injected into calling confirm_write with a fabricated id, there's
nothing pending under that id and nothing executes.

Demo simplification: this is an in-memory dict, scoped to the Streamlit
session. That's fine for a single-app-instance demo. For a production
deployment with multiple app replicas or a need to survive restarts,
back this with a real staging table (e.g. a UC Delta table keyed by
confirmation_id with a TTL) instead of a process-local dict.
"""

import time
import uuid

_TTL_SECONDS = 300  # a proposed write must be confirmed within 5 minutes


class PendingActionStore:
    def __init__(self):
        self._store = {}

    def put(self, pending: dict) -> str:
        confirmation_id = str(uuid.uuid4())[:8]
        self._store[confirmation_id] = {**pending, "_created_at": time.time()}
        return confirmation_id

    def pop(self, confirmation_id: str) -> dict:
        entry = self._store.get(confirmation_id)
        if entry is None:
            raise KeyError(f"No pending action found for confirmation id '{confirmation_id}'. "
                            f"It may have already been executed, expired, or never existed -- propose the write again.")
        if time.time() - entry["_created_at"] > _TTL_SECONDS:
            del self._store[confirmation_id]
            raise KeyError(f"Confirmation id '{confirmation_id}' expired after {_TTL_SECONDS}s. Propose the write again.")
        del self._store[confirmation_id]
        return entry
