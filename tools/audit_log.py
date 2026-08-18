"""Every write the agent executes -- UC or Lakebase -- gets one row here,
regardless of which backend it targeted. Uses the same Statement Execution
API path as tools/uc_connector.py."""

import json

from databricks.sdk.service.sql import StatementParameterListItem

import config
from tools.uc_connector import run_statement  # reuse the same warehouse connection helper


def log_write(user: str, target: str, table_name: str, preview: str, result: str, success: bool) -> None:
    statement = f"""
        INSERT INTO IDENTIFIER(:audit_table)
        (event_time, app_user, target, table_name, statement_preview, result, success)
        VALUES (current_timestamp(), :user, :target, :table_name, :preview, :result, :success)
    """
    parameters = [
        StatementParameterListItem(name="audit_table", value=config.AUDIT_LOG_TABLE, type="STRING"),
        StatementParameterListItem(name="user", value=user, type="STRING"),
        StatementParameterListItem(name="target", value=target, type="STRING"),
        StatementParameterListItem(name="table_name", value=table_name, type="STRING"),
        StatementParameterListItem(name="preview", value=preview, type="STRING"),
        StatementParameterListItem(name="result", value=result, type="STRING"),
        StatementParameterListItem(name="success", value=str(success), type="BOOLEAN"),
    ]
    try:
        run_statement(statement, parameters)
    except Exception as e:
        # Never let an audit-log failure block the user-facing response --
        # but this should be surfaced/alerted on in a real deployment.
        print(f"[audit_log] failed to write audit row: {e}")
