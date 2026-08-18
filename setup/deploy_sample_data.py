"""
One-time setup: creates the sample UC schema/tables/audit log and the two
UC helper functions (list_tables, describe_table), using the same
Statement Execution API path the app uses at runtime.

Run from a machine/notebook with Databricks auth configured (e.g.
`databricks auth login` done, or DATABRICKS_HOST/DATABRICKS_TOKEN set):

    python setup/deploy_sample_data.py

To point this at a client's real catalog/schema instead of the sample
data, edit UC_CATALOG/UC_SCHEMA in config.py (or set the env vars) --
this script does not need to change.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from tools.uc_connector import run_statement


def load_sql(relative_path: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "sql", relative_path)) as f:
        text = f.read()
    return text.replace("{{CATALOG}}", config.UC_CATALOG).replace("{{SCHEMA}}", config.UC_SCHEMA)


def _has_real_sql(chunk: str) -> bool:
    """A chunk may be entirely a comment block (e.g. the file header before
    the first statement) -- strip leading '--' lines and blank lines and
    see if anything is actually left to execute."""
    for line in chunk.splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return True
    return False


def run_script(sql_text: str, label: str) -> None:
    chunks = [c.strip() for c in sql_text.split(";")]
    statements = [c for c in chunks if _has_real_sql(c)]
    for i, stmt in enumerate(statements, 1):
        print(f"[{label}] statement {i}/{len(statements)}...")
        run_statement(stmt)
    print(f"[{label}] done ({len(statements)} statements).")


if __name__ == "__main__":
    if not config.SQL_WAREHOUSE_ID:
        raise SystemExit("Set DATABRICKS_SQL_WAREHOUSE_ID before running setup.")

    print(f"Deploying sample data to {config.UC_FULL_SCHEMA} using warehouse {config.SQL_WAREHOUSE_ID}")
    run_script(load_sql("01_schema_and_tables.sql"), "schema_and_tables")
    run_script(load_sql("02_uc_helper_functions.sql"), "uc_helper_functions")
    print("\nDone. Sample tables: "
          f"{config.UC_FULL_SCHEMA}.customers, {config.UC_FULL_SCHEMA}.orders. "
          f"Audit log: {config.AUDIT_LOG_TABLE}.")
    print("Remember to also GRANT SELECT/UPDATE on these tables to the "
          "service principal the app will run as -- see README 'Governance'.")
