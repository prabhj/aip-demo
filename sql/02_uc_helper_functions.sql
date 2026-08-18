-- These two ARE real Unity Catalog SQL functions (registered via
-- UCFunctionToolkit in agent.py and executed via client.execute_function),
-- unlike query/write which run in-app -- see tools/uc_connector.py for why.
-- They're genuinely single-RETURN-expression queries against
-- system.information_schema, so they fit UC SQL functions cleanly and give
-- the agent real schema grounding instead of guessing column names blind.
-- Generic/catalog-agnostic: no {{CATALOG}}/{{SCHEMA}} substitution needed,
-- the caller (the agent, seeded from config.py) passes those as arguments.

CREATE OR REPLACE FUNCTION {{CATALOG}}.{{SCHEMA}}.list_tables(
  catalog_name STRING COMMENT 'Unity Catalog catalog name to list tables in',
  schema_name STRING COMMENT 'Schema name within that catalog to list tables in'
)
RETURNS TABLE (table_name STRING, table_type STRING, comment STRING)
LANGUAGE SQL
COMMENT 'Agent tool: lists the tables available in a given catalog.schema, with type and description. Call this before query_uc_table if you are not sure what tables exist.'
RETURN
  SELECT table_name, table_type, comment
  FROM system.information_schema.tables
  WHERE table_catalog = catalog_name AND table_schema = schema_name;

CREATE OR REPLACE FUNCTION {{CATALOG}}.{{SCHEMA}}.describe_table(
  catalog_name STRING COMMENT 'Unity Catalog catalog name',
  schema_name STRING COMMENT 'Schema name',
  tbl_name STRING COMMENT 'Table name (unqualified) to describe columns for'
)
RETURNS TABLE (column_name STRING, data_type STRING, comment STRING)
LANGUAGE SQL
COMMENT 'Agent tool: lists the columns and types of a given table. Call this before filtering or updating a column you are not sure exists.'
RETURN
  -- Parameter is named tbl_name (not table_name) specifically to avoid
  -- colliding with information_schema.columns.table_name below.
  SELECT column_name, full_data_type AS data_type, comment
  FROM system.information_schema.columns
  WHERE table_catalog = catalog_name AND table_schema = schema_name
    AND table_name = tbl_name;
