"""
Unity Catalog table read/write, executed for real via the Databricks SQL
Statement Execution API against a SQL warehouse -- not via a UC SQL
function's single RETURN expression.

Why this isn't a UC SQL function like the original notebook tried:
UC SQL functions are limited to a single RETURN expression (no BEGIN/END,
no branching), which makes it awkward to safely build a query whose WHERE
clause is optional and whose table/column names vary per call. Doing the
dynamic-SQL assembly here in Python -- with real validation and IDENTIFIER()
for anything that has to be a table/column name -- is more auditable than
trying to force that logic into a single SQL expression.

Governance still lives in Unity Catalog: this app should run as a service
principal that only has SELECT/UPDATE grants on the tables listed in
config.ALLOWED_UC_TABLES. The allowlist check below is a second layer, not
the primary control -- see README "Governance".
"""

import json
import logging

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

import config

logger = logging.getLogger(__name__)

_w = WorkspaceClient()


class UCToolError(Exception):
    pass


def _check_table_allowed(table_name: str) -> None:
    if table_name not in config.ALLOWED_UC_TABLES:
        raise UCToolError(
            f"'{table_name}' is not in the allowed UC table list for this agent. "
            f"Allowed tables: {', '.join(config.ALLOWED_UC_TABLES)}"
        )


def _check_operator_allowed(operator: str) -> str:
    if operator not in config.ALLOWED_FILTER_OPERATORS:
        raise UCToolError(
            f"Operator '{operator}' is not allowed. Allowed operators: "
            f"{', '.join(sorted(config.ALLOWED_FILTER_OPERATORS))}"
        )
    return operator


def run_statement(statement: str, parameters=None):
    resp = _w.statement_execution.execute_statement(
        warehouse_id=config.SQL_WAREHOUSE_ID,
        statement=statement,
        parameters=parameters or [],
        wait_timeout="30s",
    )
    state = resp.status.state.value if resp.status and resp.status.state else None
    if state not in ("SUCCEEDED",):
        error = resp.status.error if resp.status else None
        raise UCToolError(f"Statement did not succeed (state={state}): {error}")
    return resp


def _rows_to_dicts(resp):
    columns = [c.name for c in resp.manifest.schema.columns] if resp.manifest and resp.manifest.schema else []
    rows = resp.result.data_array if resp.result and resp.result.data_array else []
    return columns, [dict(zip(columns, row)) for row in rows]


def query_uc_table(table_name: str, filter_column: str = None, filter_operator: str = "=",
                    filter_value: str = None, row_limit: int = None) -> str:
    """Read tool: real SELECT against an allowlisted UC table with an optional
    single structured filter (column/operator/value) instead of a free-text
    WHERE clause, so nothing but a table/column *name* is ever substituted
    as an identifier."""
    _check_table_allowed(table_name)
    row_limit = min(row_limit or config.DEFAULT_ROW_LIMIT, config.MAX_ROW_LIMIT)

    statement = f"SELECT * FROM IDENTIFIER(:table_name) "
    parameters = [StatementParameterListItem(name="table_name", value=table_name, type="STRING")]

    if filter_column:
        operator = _check_operator_allowed(filter_operator)
        statement += f"WHERE IDENTIFIER(:filter_column) {operator} :filter_value "
        parameters.append(StatementParameterListItem(name="filter_column", value=filter_column, type="STRING"))
        parameters.append(StatementParameterListItem(name="filter_value", value=str(filter_value), type="STRING"))

    statement += f"LIMIT {row_limit}"

    resp = run_statement(statement, parameters)
    columns, rows = _rows_to_dicts(resp)
    return json.dumps({"table": table_name, "row_count": len(rows), "columns": columns, "rows": rows}, default=str)


def describe_columns(table_name: str):
    """Used by propose_uc_write to validate a column really exists before
    building an UPDATE, and available to the agent as part of query_uc_table
    error recovery."""
    catalog, schema, table = table_name.split(".")
    statement = (
        "SELECT column_name, full_data_type FROM system.information_schema.columns "
        "WHERE table_catalog = :cat AND table_schema = :sch AND table_name = :tbl"
    )
    parameters = [
        StatementParameterListItem(name="cat", value=catalog, type="STRING"),
        StatementParameterListItem(name="sch", value=schema, type="STRING"),
        StatementParameterListItem(name="tbl", value=table, type="STRING"),
    ]
    resp = run_statement(statement, parameters)
    _, rows = _rows_to_dicts(resp)
    return {r["column_name"]: r["full_data_type"] for r in rows}


def build_pending_uc_write(table_name: str, column_name: str, new_value: str,
                            filter_column: str, filter_operator: str, filter_value: str) -> dict:
    """Validates a proposed UC write and returns a preview + the exact
    statement/parameters to run on confirm -- called by propose_uc_write in
    agent.py, not exposed to the LLM directly."""
    _check_table_allowed(table_name)
    operator = _check_operator_allowed(filter_operator)

    columns = describe_columns(table_name)
    if column_name not in columns:
        raise UCToolError(f"Column '{column_name}' does not exist on {table_name}. Known columns: {', '.join(columns)}")
    if filter_column not in columns:
        raise UCToolError(f"Filter column '{filter_column}' does not exist on {table_name}. Known columns: {', '.join(columns)}")

    statement = "UPDATE IDENTIFIER(:table_name) SET IDENTIFIER(:column_name) = :new_value " \
                f"WHERE IDENTIFIER(:filter_column) {operator} :filter_value"
    parameters = [
        StatementParameterListItem(name="table_name", value=table_name, type="STRING"),
        StatementParameterListItem(name="column_name", value=column_name, type="STRING"),
        StatementParameterListItem(name="new_value", value=str(new_value), type="STRING"),
        StatementParameterListItem(name="filter_column", value=filter_column, type="STRING"),
        StatementParameterListItem(name="filter_value", value=str(filter_value), type="STRING"),
    ]
    preview = (f"UPDATE {table_name} SET {column_name} = '{new_value}' "
               f"WHERE {filter_column} {operator} '{filter_value}'")
    return {"target": "uc", "table_name": table_name, "statement": statement,
            "parameters": parameters, "preview": preview}


def execute_pending_write(pending: dict) -> str:
    resp = run_statement(pending["statement"], pending["parameters"])
    affected = resp.result.row_count if resp.result and resp.result.row_count is not None else None
    return f"Executed against {pending['table_name']}. Rows affected: {affected if affected is not None else 'unknown'}."
