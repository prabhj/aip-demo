"""
Central configuration for the Genie-like UC & Lakebase agent.

Everything that's specific to a deployment (which catalog/schema, which
Lakebase instance, which tables the agent is allowed to touch) lives here
and is read from environment variables where possible, so this whole app
can be re-pointed at a client's real data by changing env vars / this file
only -- no changes needed in agent.py, app.py, or the tools/ modules.

In a Databricks App, set these as App environment variables (in app.yaml
or the Apps UI) rather than editing this file in place for each client.
"""

import os

# --- Unity Catalog target ---
# The catalog/schema the sample data + audit log live in. Point this at the
# client's own catalog/schema to reuse this app on their real tables.
UC_CATALOG = os.environ.get("UC_CATALOG", "main")
UC_SCHEMA = os.environ.get("UC_SCHEMA", "genie_demo")
UC_FULL_SCHEMA = f"{UC_CATALOG}.{UC_SCHEMA}"

# SQL warehouse the app uses to run governed reads/writes via the
# Statement Execution API. Required -- create a small serverless SQL
# warehouse for the app's service principal and put its ID here.
SQL_WAREHOUSE_ID = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")

# Tables the agent is allowed to read/write in UC, as "schema.table" or
# "catalog.schema.table". This is a defense-in-depth allowlist checked in
# app code -- the *primary* control should still be UC GRANTs on the
# service principal this app runs as (see README "Governance" section).
ALLOWED_UC_TABLES = [
    t.strip()
    for t in os.environ.get(
        "ALLOWED_UC_TABLES", f"{UC_FULL_SCHEMA}.customers,{UC_FULL_SCHEMA}.orders"
    ).split(",")
    if t.strip()
]

# Delta table (created by setup/deploy_sample_data.py) that every write
# this agent performs gets logged to, UC-side and Lakebase-side alike.
AUDIT_LOG_TABLE = f"{UC_FULL_SCHEMA}.agent_audit_log"

# --- Lakebase (Postgres) target ---
# Lakebase Postgres has two capacity modes with two different SDK/API
# surfaces (see tools/lakebase_connector.py docstring for the full
# explanation): legacy "Provisioned" (flat instance name -> WorkspaceClient.
# database.*) and current-gen "Autoscaling" (Project -> Branch -> Endpoint
# hierarchy -> WorkspaceClient.postgres.*). This app targets Autoscaling.
#
# LAKEBASE_PROJECT_ID is just the project's short ID -- the last segment of
# its resource name "projects/{project_id}", and the same string shown as
# the instance/project name in the Lakebase Postgres UI's "Autoscaling" tab.
# The app resolves that project's default branch and read-write endpoint
# itself at connect time, so you don't need to know branch/endpoint IDs.
# Leave blank to disable the Lakebase tools entirely (the agent just won't
# offer them).
LAKEBASE_PROJECT_ID = os.environ.get("LAKEBASE_PROJECT_ID", "")
LAKEBASE_DATABASE = os.environ.get("LAKEBASE_DATABASE", "databricks_postgres")
LAKEBASE_PORT = int(os.environ.get("LAKEBASE_PORT", "5432"))
# Postgres role to connect as. This role must already exist in the Lakebase
# project and be mapped to the Databricks identity the app runs as (see
# Databricks docs "Authenticate to a database instance" -- Postgres roles
# are provisioned separately from Databricks identities). If left blank,
# the app falls back to the current Databricks user/service-principal name,
# which will only work if a matching Postgres role was created for it.
LAKEBASE_PG_USER = os.environ.get("LAKEBASE_PG_USER", "")

ALLOWED_LAKEBASE_TABLES = [
    t.strip()
    for t in os.environ.get("ALLOWED_LAKEBASE_TABLES", "support_tickets").split(",")
    if t.strip()
]

LAKEBASE_ENABLED = bool(LAKEBASE_PROJECT_ID)

# --- Model serving endpoint ---
MODEL_ENDPOINT = os.environ.get("MODEL_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")

# --- MLflow tracing ---
# Every LLM call and tool call gets logged to this experiment (autolog in
# agent.py) so a run can be inspected after the fact -- what the model saw,
# which tools it called, in what order, how long each step took. "/Shared/"
# rather than a /Users/ path since the app runs as a service principal, not
# a real user. Set MLFLOW_TRACING_ENABLED=false to turn it off entirely.
MLFLOW_EXPERIMENT_PATH = os.environ.get("MLFLOW_EXPERIMENT_PATH", "/Shared/genie_uc_lakebase_agent")
MLFLOW_TRACING_ENABLED = os.environ.get("MLFLOW_TRACING_ENABLED", "true").lower() != "false"

# --- Safety limits ---
MAX_ROW_LIMIT = int(os.environ.get("MAX_ROW_LIMIT", "200"))
DEFAULT_ROW_LIMIT = int(os.environ.get("DEFAULT_ROW_LIMIT", "50"))
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "6"))

# Comparison operators the agent may use in structured filters. Deliberately
# NOT a free-text WHERE clause -- see README "Why structured filters" for why.
ALLOWED_FILTER_OPERATORS = {"=", "!=", "<", "<=", ">", ">="}
