"""
Lakebase (Databricks Postgres) read/write, run in-process in the app --
deliberately NOT wrapped in a UC Python function like the original notebook
did.

Two reasons for that change:
  1. Lakebase auth is meant to go through
     WorkspaceClient.database.generate_database_credential(...), a
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
"""

import json
import logging
import time
import uuid

import psycopg2
from psycopg2 import sql
from databricks.sdk import WorkspaceClient

import config

logger = logging.getLogger(__name__)

_w = WorkspaceClient()

_cred_cache = {"token": None, "expires_at": 0}


class LakebaseToolError(Exception):
    pass


def _get_credential() -> str:
    """OAuth tokens for Lakebase are valid ~1hr; cache and refresh with a
    safety margin rather than minting a new one on every call."""
    now = time.time()
    if _cred_cache["token"] and now < _cred_cache["expires_at"] - 120:
        return _cred_cache["token"]

    cred = _w.database.generate_database_credential(
        request_id=str(uuid.uuid4()), instance_names=[config.LAKEBASE_INSTANCE_NAME]
    )
    _cred_cache["token"] = cred.token
    _cred_cache["expires_at"] = now + 3600
    return cred.token


def _pg_user() -> str:
    if config.LAKEBASE_PG_USER:
        return config.LAKEBASE_PG_USER
    me = _w.current_user.me()
    return me.user_name or me.emails[0].value


def get_connection():
    if not config.LAKEBASE_ENABLED:
        raise LakebaseToolError("Lakebase is not configured (LAKEBASE_INSTANCE_NAME is unset).")
    instance = _w.database.get_database_instance(name=config.LAKEBASE_INSTANCE_NAME)
    token = _get_credential()
    return psycopg2.connect(
        host=instance.read_write_dns,
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
