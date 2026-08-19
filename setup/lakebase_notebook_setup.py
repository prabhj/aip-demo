"""
Self-contained Lakebase sample-data setup, meant to be pasted into a single
Databricks notebook cell (Workspace -> Create -> Notebook) and run there --
no CLI needed, and no dependency on the rest of this repo's file layout, so
it works even if this notebook isn't inside the cloned Git folder.

If running as an actual Databricks notebook cell, put this on its own line
first (remove the leading '#'):
# %pip install psycopg2-binary --quiet
# dbutils.library.restartPython()
Then run this script in the next cell.

Fill in the three CONFIG values below before running.
"""

import uuid

import psycopg2
from databricks.sdk import WorkspaceClient

# ---- CONFIG: fill these in ----
LAKEBASE_INSTANCE_NAME = "<your-lakebase-instance-name>"   # not a hostname -- the instance's name
LAKEBASE_DATABASE = "databricks_postgres"                   # change if you used a different db name
LAKEBASE_PG_USER = ""  # leave blank to use your current Databricks identity; only works if a
                        # matching Postgres role already exists for it (see README "Governance")
# --------------------------------

SAMPLE_SQL = """
CREATE TABLE IF NOT EXISTS support_tickets (
  ticket_id     SERIAL PRIMARY KEY,
  customer_id   INT NOT NULL,
  subject       TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open',
  priority      TEXT NOT NULL DEFAULT 'medium',
  created_at    TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO support_tickets (customer_id, subject, status, priority, created_at) VALUES
  (1, 'Order 103 arrived damaged',        'open',        'high',   now() - interval '5 days'),
  (2, 'Question about return policy',     'open',        'low',    now() - interval '4 days'),
  (3, 'Duplicate charge on order 104',    'in_progress', 'high',   now() - interval '3 days'),
  (5, 'Order 106 cancellation confirm',   'closed',      'medium', now() - interval '10 days'),
  (7, 'Delivery address change request',  'open',        'medium', now() - interval '2 days'),
  (8, 'Missing item in order 110',        'in_progress', 'high',   now() - interval '1 days'),
  (9, 'Loyalty points not applied',       'open',        'low',    now() - interval '6 hours');
"""

w = WorkspaceClient()

print(f"Looking up Lakebase instance '{LAKEBASE_INSTANCE_NAME}'...")
instance = w.database.get_database_instance(name=LAKEBASE_INSTANCE_NAME)

print("Generating a short-lived OAuth credential (this is the same path the app itself uses)...")
cred = w.database.generate_database_credential(
    request_id=str(uuid.uuid4()), instance_names=[LAKEBASE_INSTANCE_NAME]
)

pg_user = LAKEBASE_PG_USER
if not pg_user:
    me = w.current_user.me()
    pg_user = me.user_name or me.emails[0].value
    print(f"LAKEBASE_PG_USER not set -- trying current identity: {pg_user}")
    print("If this fails with an auth error, a Postgres role matching this identity "
          "probably doesn't exist yet in the instance -- create one and/or set LAKEBASE_PG_USER.")

conn = psycopg2.connect(
    host=instance.read_write_dns,
    port=5432,
    dbname=LAKEBASE_DATABASE,
    user=pg_user,
    password=cred.token,
    sslmode="require",
)

try:
    cur = conn.cursor()
    cur.execute(SAMPLE_SQL)
    conn.commit()
    cur.execute("SELECT count(*) FROM support_tickets;")
    count = cur.fetchone()[0]
    print(f"Done. support_tickets now has {count} rows in database '{LAKEBASE_DATABASE}'.")
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()

print(
    "\nNext: grant the app's service principal access to this table, e.g. via psql/DBeaver "
    "or a Postgres client connected the same way this script did:\n"
    '  GRANT SELECT, UPDATE ON support_tickets TO "<app-service-principal>";\n'
    "Then set these as your app's env vars (in app.yaml) and redeploy:\n"
    f"  LAKEBASE_INSTANCE_NAME={LAKEBASE_INSTANCE_NAME}\n"
    f"  LAKEBASE_DATABASE={LAKEBASE_DATABASE}\n"
    f"  LAKEBASE_PG_USER={LAKEBASE_PG_USER or '(leave blank to match the app service principal identity, once a role exists for it)'}"
)
