"""
Lakebase (Databricks Postgres) read/write, run in-process in the app --
deliberately NOT wrapped in a UC Python function like the original notebook
did.

Two reasons for that change:
  1. Lakebase auth is meant to go through
     WorkspaceClient.postgres.generate_database_credential(...), a
     short-lived OAuth token vended for the *caller's* identity. That's a
     natural fit for a long-lived app process (mint once, cache, refresh
     hourly) but awkward inside a UC Python function's sandboxed,
     per-invocation execution model.
  2. Databricks documents "app connects directly to Lakebase via the SDK"
     as the first-class pattern for exactly this scenario.

Same safety shape as tools/uc_connector.py: an allowlist of tables, a
structured (not free-text) filter, identifiers composed safely via
psycopg2.sql rather than string formatting, and reads/writes split into
propose -> confirm.

--- Provisioned vs. Autoscaling: why this file looks the way it does ---
Lakebase Postgres ships as two different capacity modes, and Databricks
gave them two *different, incompatible* SDK surfaces rather than one API
with a mode flag:

  - "Provisioned" (legacy): a flat instance concept.
    WorkspaceClient.database.get_database_instance(name=...) ->
    .read_write_dns, and
    WorkspaceClient.database.generate_database_credential(instance_names=[...]).
  - "Autoscaling" (current default, what this app targets): a hierarchical
    Project -> Branch -> Endpoint model under WorkspaceClient.postgres.*
    (the PostgresAPI). There's no single "instance name -> hostname" call;
    you resolve a project's default branch, that branch's read-write
    endpoint, and only that endpoint has a connectable host.

An instance created before the Autoscaling rollout and then upgraded shows
up in the Postgres UI's "Autoscaling" tab with a "(upgraded)" suffix on its
display name -- but that suffix is a UI label, not the actual project ID,
and the underlying instance is a real Autoscaling project reachable only
through WorkspaceClient.postgres.*, not WorkspaceClient.database.*.

If you're on legacy Provisioned, the fix is to swap _resolve_endpoint() /
_get_credential() below back to the WorkspaceClient.database.* calls
described above -- everything downstream (the psycopg2 connection, the
query/write functions) is unaffected either way.
"""

import json
import logging
import time

import psycopg2
from psycopg2 import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import EndpointType

import config

logger = logging.getLogger(__name__)

_w = WorkspaceClient()

_cred_cache = {"token": None, "expires_at": 0}
# Resolving project -> default branch -> read-write endpoint takes a few
# API calls and that topology essentially never changes during a running
# app process, so cache it separately from the (shorter-lived) credential.
_endpoint_cache = {"name": None, "host": None}


class LakebaseToolError(Exception):
    pass


def _resolve_endpoint() -> tuple[str, str]:
    """Returns (endpoint_full_name, host) for the project's default branch's
    read-write endpoint, e.g. endpoint_full_name =
    'projects/<id>/branches/<id>/endpoints/<id>'.

    Cached for the life of the process -- call _endpoint_cache.clear()-style
    reset (or just restart the app) if the project's branch/endpoint
    topology changes underneath a running deployment.
    """
    if _endpoint_cache["name"] and _endpoint_cache["host"]:
        return _endpoint_cache["name"], _endpoint_cache["host"]

    project_name = f"projects/{config.LAKEBASE_PROJECT_ID}"
    try:
        _w.postgres.get_project(name=project_name)
    except Exception as e:
        raise LakebaseToolError(
            f"Could not find Lakebase project '{config.LAKEBASE_PROJECT_ID}'. "
            f"Check LAKEBASE_PROJECT_ID matches the project ID shown in the "
            f"Lakebase Postgres UI's 'Autoscaling' tab. Underlying error: {e}"
        )

    branches = list(_w.postgres.list_branches(parent=project_name))
    if not branches:
        raise LakebaseToolError(f"Project '{project_name}' has no branches.")
    default_branch = next((b for b in branches if b.status and b.status.default), None)
    if default_branch is None:
        default_branch = branches[0]
        logger.warning(
            f"No branch in {project_name} is marked default; falling back to "
            f"the first branch returned ({default_branch.name})."
        )

    endpoints = list(_w.postgres.list_endpoints(parent=default_branch.name))
    rw_endpoint = next(
        (
            e for e in endpoints
            if e.spec and e.spec.endpoint_type == EndpointType.ENDPOINT_TYPE_READ_WRITE
        ),
        None,
    )
    if rw_endpoint is None:
        raise LakebaseToolError(
            f"Branch '{default_branch.name}' has no read-write endpoint. "
            f"Found endpoint types: {[e.spec.endpoint_type for e in endpoints if e.spec]}"
        )

    # list_endpoints results carry status already, but re-fetch to be sure
    # we have current connection info (host can change across suspend/resume).
    endpoint = _w.postgres.get_endpoint(name=rw_endpoint.name)
    host = endpoint.status.hosts.host if endpoint.status and endpoint.status.hosts else None
    if not host:
        raise LakebaseToolError(
            f"Endpoint '{rw_endpoint.name}' has no host yet -- it may still be "
            f"starting up (state: {endpoint.status.current_state if endpoint.status else 'unknown'})."
        )

    _endpoint_cache["name"] = endpoint.name
    _endpoint_cache["host"] = host
    return endpoint.name, host


def _get_credential(endpoint_name: str) -> str:
    """OAuth tokens for Lakebase are valid ~1hr; cache and refresh with a
    safety margin rather than minting a new one on every call."""
    now = time.time()
    if _cred_cache["token"] and now < _cred_cache["expires_at"] - 120:
        return _cred_cache["token"]

    cred = _w.postgres.generate_database_credential(endpoint=endpoint_name)
    _cred_cache["token"] = cred.token
    # DatabaseCredential.expire_time is a real timestamp when present; fall
    # back to the documented ~1hr lifetime if the SDK doesn't populate it.
    if getattr(cred, "expire_time", None):
        _cred_cache["expires_at"] = cred.expire_time.timestamp()
    else:
        _cred_cache["expires_at"] = now + 3600
    return cred.token


def _pg_user() -> str:
    if config.LAKEBASE_PG_USER:
        return config.LAKEBASE_PG_USER
    me = _w.current_user.me()
    return me.user_name or me.emails[0].value


def get_connection():
    if not config.LAKEBASE_ENABLED:
        raise LakebaseToolError("Lakebase is not configured (LAKEBASE_PROJECT_ID is unset).")
    endpoint_name, host = _resolve_endpoint()
    token = _get_credential(endpoint_name)
    return psycopg2.connect(
        host=host,
        port=config.LAKEBASE_PORT,
        dbname=config.LAKEBASE_DATABASE,
        user=_pg_user(),
        password=token,
        sslmode="require",
    )


def _check_table_allowed(table_name: str) -> None:
    if table_name not in config.ALLOWED_LAKEBASE_TABLES:
        raise LakebaseToolError(
            f"'{table_name}' is not in the allowed Lakebase table list. "
            f"Allowed tables: {', '.join(config.ALLOWED_LAKEBASE_TABLES)}"
        )


def _check_operator_allowed(operator: str) -> str:
    if operator not in config.ALLOWED_FILTER_OPERATORS:
        raise LakebaseToolError(
            f"Operator '{operator}' is not allowed. Allowed operators: "
            f"{', '.join(sorted(config.ALLOWED_FILTER_OPERATORS))}"
        )
    return operator


def query_lakebase_table(table_name: str, filter_column: str = None, filter_operator: str = "=",
                          filter_value: str = None, row_limit: int = None) -> str:
    _check_table_allowed(table_name)
    row_limit = min(row_limit or config.DEFAULT_ROW_LIMIT, config.MAX_ROW_LIMIT)

    query = sql.SQL("SELECT * FROM {table}").format(table=sql.Identifier(table_name))
    params = []
    if filter_column:
        operator = _check_operator_allowed(filter_operator)
        query = sql.SQL("{base} WHERE {col} {op} %s").format(
            base=query, col=sql.Identifier(filter_column), op=sql.SQL(operator)
        )
        params.append(filter_value)
    query = sql.SQL("{base} LIMIT %s").format(base=query)
    params.append(row_limit)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(columns, [str(v) for v in row])) for row in cur.fetchall()]
        return json.dumps({"table": table_name, "row_count": len(rows), "columns": columns, "rows": rows})
    except Exception as e:
        raise LakebaseToolError(str(e))
    finally:
        conn.close()


def _describe_columns(table_name: str, conn) -> list:
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [table_name])
    return [r[0] for r in cur.fetchall()]


def build_pending_lakebase_write(table_name: str, column_name: str, new_value: str,
                                  filter_column: str, filter_operator: str, filter_value: str) -> dict:
    _check_table_allowed(table_name)
    operator = _check_operator_allowed(filter_operator)

    conn = get_connection()
    try:
        columns = _describe_columns(table_name, conn)
        if column_name not in columns:
            raise LakebaseToolError(f"Column '{column_name}' does not exist on {table_name}. Known columns: {', '.join(columns)}")
        if filter_column not in columns:
            raise LakebaseToolError(f"Filter column '{filter_column}' does not exist on {table_name}. Known columns: {', '.join(columns)}")
    finally:
        conn.close()

    query = sql.SQL("UPDATE {table} SET {col} = %s WHERE {fcol} {op} %s").format(
        table=sql.Identifier(table_name), col=sql.Identifier(column_name),
        fcol=sql.Identifier(filter_column), op=sql.SQL(operator),
    )
    preview = (f"UPDATE {table_name} SET {column_name} = '{new_value}' "
               f"WHERE {filter_column} {operator} '{filter_value}'")
    return {"target": "lakebase", "table_name": table_name, "query": query,
            "params": [new_value, filter_value], "preview": preview}


def execute_pending_write(pending: dict) -> str:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(pending["query"], pending["params"])
        affected = cur.rowcount
        conn.commit()
        return f"Executed against {pending['table_name']}. Rows affected: {affected}."
    except Exception as e:
        conn.rollback()
        raise LakebaseToolError(str(e))
    finally:
        conn.close()
