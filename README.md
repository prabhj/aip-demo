# UC & Lakebase Agent -- demo Databricks App

A Genie-like chat agent that reads and writes both Unity Catalog tables and
a Lakebase Postgres database, with a propose-then-confirm safety step on
every write, full audit logging, and MLflow tracing on every LLM/tool call.
Built as a Databricks App (Streamlit).

Ships with synthetic sample data (customers/orders in UC, support_tickets
in Lakebase) so it runs standalone for a demo -- see "Reuse for a client's
real data" below for pointing it at something real.

## What changed vs. the original prototype notebook

The original notebook this was built from had two tools that returned
canned success text without executing anything (`query_uc_table` just
returned unexecuted SQL as a string; `update_uc_table` returned a fake
"Update submitted successfully" message without running the UPDATE), plus
a Lakebase reader authenticating with a generic `DATABRICKS_TOKEN` env var
rather than Lakebase's actual OAuth credential-vending path. This version:

- Actually executes reads and writes (via the Databricks SQL Statement
  Execution API for UC, and `psycopg2` for Lakebase).
- Fixes Lakebase auth to use `WorkspaceClient.database.generate_database_credential(...)`.
- Never lets the LLM write raw SQL text or a raw WHERE clause -- writes and
  filters are structured (table/column/operator/value), validated against
  real schema, and identifiers are substituted safely (`IDENTIFIER()` on
  the UC side, `psycopg2.sql.Identifier` on the Lakebase side) instead of
  string concatenation.
- Adds a real propose → confirm step for every write, backed by
  server-side state (`tools/pending_actions.py`) so the model can't
  fabricate a confirmation and skip the check.
- Adds an audit log (`agent_audit_log` table) that every write, UC or
  Lakebase, gets logged to.
- Uses `UCFunctionToolkit` for the two tools that are genuinely well-suited
  to being UC SQL functions (`list_tables`, `describe_table`), and runs the
  dynamic query/write tools in-app instead of trying to force them into a
  UC SQL function's single-RETURN-expression body. See the docstring at
  the top of `tools/uc_connector.py` for why.
- Adds MLflow tracing (`mlflow.openai.autolog()` in `agent.py`), so every
  LLM call and every tool execution -- including a hidden failure like a
  permission error inside a tool -- shows up as a queryable trace, not just
  something that scrolled past in the app logs.

## Viewing traces

Once the app is deployed and you've sent it a message, open the workspace's
**Experiments** page and find the experiment at the path set by
`MLFLOW_EXPERIMENT_PATH` (default `/Shared/genie_uc_lakebase_agent`) --
each conversation turn shows up as a trace with the full LLM call (prompt,
response, which tools it decided to call) and a nested span for each tool
execution (`tool_execution` for the local UC/Lakebase tools, `uc_native_function`
for `list_tables`/`describe_table`), including their inputs/outputs and
timing. If the experiment doesn't show up, check that the app's service
principal has permission to create experiments at that path -- tracing
setup fails silently (logs a warning, doesn't crash the app) so the app
staying up doesn't mean tracing is actually working. Set
`MLFLOW_TRACING_ENABLED=false` to turn it off entirely.

This is separate from -- and doesn't require -- registering the agent
itself as an MLflow model served via Model Serving. That's a bigger step
(see "Known simplifications" below) worth doing only if the agent needs to
be callable from somewhere other than this app's own UI.

## Prerequisites

- A Unity Catalog catalog that already exists (this setup only creates the
  schema/tables inside it, not the catalog itself).
- A small SQL warehouse (serverless is fine) for the app to query through.
- Databricks CLI configured (`databricks auth login`) or
  `DATABRICKS_HOST`/`DATABRICKS_TOKEN` set, for running the setup scripts.
- If demoing the Lakebase piece: a Lakebase database instance already
  provisioned, and a Postgres role created for the identity that will run
  this app (see "Governance" below) -- Postgres roles are provisioned
  separately from Databricks identities.

## 1. Configure

Copy the defaults in `config.py` or set these env vars:

| Variable | Purpose |
|---|---|
| `UC_CATALOG` / `UC_SCHEMA` | Where the sample tables + audit log live |
| `DATABRICKS_SQL_WAREHOUSE_ID` | Warehouse the app queries through |
| `MODEL_ENDPOINT` | Foundation Model serving endpoint to use |
| `LAKEBASE_INSTANCE_NAME` | Leave blank to demo UC-only |
| `LAKEBASE_DATABASE`, `LAKEBASE_PG_USER` | Lakebase connection details |
| `ALLOWED_UC_TABLES`, `ALLOWED_LAKEBASE_TABLES` | Tables the agent may touch |

## 2. Create sample data

**If you have the Databricks CLI:**
```bash
pip install -r requirements.txt
python setup/deploy_sample_data.py         # UC: customers, orders, audit log, helper functions
python setup/deploy_lakebase_sample.py     # Lakebase: support_tickets (only if LAKEBASE_INSTANCE_NAME is set)
```

**UI-only (no CLI):**
- UC side: open **SQL Editor** in the workspace and run `sql/01_schema_and_tables.sql`
  then `sql/02_uc_helper_functions.sql`, with `{{CATALOG}}`/`{{SCHEMA}}` replaced by
  your real values (find-and-replace before pasting).
- Lakebase side: there's no SQL-Editor equivalent for Postgres. Create a new
  Databricks **notebook**, paste in `setup/lakebase_notebook_setup.py`, fill in
  the three `LAKEBASE_*` values at the top, and run it. It's self-contained
  (no dependency on this repo's file layout) so it works regardless of where
  the notebook lives, and it authenticates the same way the app itself will
  (`generate_database_credential`), so if the notebook can connect, the app will too.

## 3. Governance (do this before the demo, not after)

The allowlists in `config.py` are a second layer, not the real security
boundary. Before demoing writes, grant the app's identity only what it
needs:

```sql
-- Unity Catalog: run as a metastore admin / catalog owner
GRANT SELECT, MODIFY ON TABLE {catalog}.{schema}.customers TO `<app-service-principal>`;
GRANT SELECT, MODIFY ON TABLE {catalog}.{schema}.orders TO `<app-service-principal>`;
GRANT SELECT, MODIFY ON TABLE {catalog}.{schema}.agent_audit_log TO `<app-service-principal>`;
GRANT EXECUTE ON FUNCTION {catalog}.{schema}.list_tables TO `<app-service-principal>`;
GRANT EXECUTE ON FUNCTION {catalog}.{schema}.describe_table TO `<app-service-principal>`;
```

```sql
-- Lakebase (psql or your Postgres client)
GRANT SELECT, UPDATE ON support_tickets TO "<app-identity>";
```

That way, even a fully prompt-injected agent can't touch anything outside
what these two grants allow, regardless of what the Python-level allowlist
says.

## 4. Deploy

```bash
databricks apps create genie-uc-lakebase-demo
databricks sync . /Workspace/Users/<you>/genie_app
databricks apps deploy genie-uc-lakebase-demo --source-code-path /Workspace/Users/<you>/genie_app
```

Set the env vars from step 1 either in `app.yaml` or under the app's
"Environment variables" in the Apps UI, then open the app URL.

## Reuse for a client's real data

This is the point of building it this way -- to go from the sample demo to
a client's actual tables, you should only need to touch `config.py` (or the
app's env vars), not the code:

1. Set `UC_CATALOG` / `UC_SCHEMA` to their catalog/schema, and
   `ALLOWED_UC_TABLES` to the specific tables they want exposed.
2. Set `LAKEBASE_INSTANCE_NAME` / `LAKEBASE_DATABASE` / `ALLOWED_LAKEBASE_TABLES`
   to their Lakebase instance.
3. Re-run `setup/deploy_sample_data.py`'s function-creation half (or just
   run `sql/02_uc_helper_functions.sql` directly) against their
   catalog/schema -- it's generic, no changes needed.
4. Re-do the grants in "Governance" for their tables.
5. Skip the sample-data inserts (`sql/01_schema_and_tables.sql`'s `INSERT`
   statements) entirely -- their tables already have real data.

## Known simplifications (call these out to the client as next steps, not as "done")

- `PendingActionStore` is in-memory per app process -- fine for a single-
  instance demo, but won't survive an app restart or work across multiple
  replicas. Production would back it with a staging table.
- `filter_column`/`filter_value` support exactly one condition; there's no
  AND/OR composition. Fine for "update this one row" demos, not for
  bulk/complex updates.
- No multi-turn memory beyond the current Streamlit session (resets on
  "Reset conversation" or app restart).
- MLflow tracing is on (see "Viewing traces"), but the agent is still not
  registered as an MLflow model / served via Model Serving -- it runs as a
  Databricks App directly. Worth doing only if this needs to be callable
  from somewhere other than this app's own UI (Slack bot, another app, etc).
- Also worth granting the app's identity `CREATE EXPERIMENT` (or pre-creating
  the `/Shared/genie_uc_lakebase_agent` experiment and granting access to
  it) as part of the governance step -- otherwise tracing silently no-ops.
