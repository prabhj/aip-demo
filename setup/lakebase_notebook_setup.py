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

Targets Lakebase Postgres "Autoscaling" capacity mode (the current default
for new Lakebase instances), which uses a Project -> Branch -> Endpoint
resource hierarchy under WorkspaceClient.postgres.* -- NOT the older
"Provisioned" mode's flat WorkspaceClient.database.* API. If your instance
is legacy Provisioned (check the Lakebase Postgres UI: does your instance
show under a "Provisioned" tab or an "Autoscaling" tab?), see the note at
the bottom of this file instead.

Fill in the CONFIG values below before running.
"""

import psycopg2
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import EndpointType

# ---- CONFIG: fill these in ----
# The project's short ID -- the same string shown as the instance/project
# name in the Lakebase Postgres UI's "Autoscaling" tab. (If your instance
# shows a "(upgraded)" suffix there, that suffix is a UI label only -- use
# the ID without it.) This is NOT a hostname; the script below resolves the
# connection host for you from this ID.
LAKEBASE_PROJECT_ID = "<your-lakebase-project-id>"
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

project_name = f"projects/{LAKEBASE_PROJECT_ID}"
print(f"Looking up Lakebase project '{project_name}'...")
w.postgres.get_project(name=project_name)  # raises if the project ID is wrong

print("Finding the project's default branch...")
branches = list(w.postgres.list_branches(parent=project_name))
if not branches:
    raise RuntimeError(f"Project '{project_name}' has no branches.")
default_branch = next((b for b in branches if b.status and b.status.default), None)
if default_branch is None:
    default_branch = branches[0]
    print(f"  (no branch marked default -- using the first one: {default_branch.name})")
else:
    print(f"  default branch: {default_branch.name}")

print("Finding that branch's read-write endpoint...")
endpoints = list(w.postgres.list_endpoints(parent=default_branch.name))
rw_endpoint = next(
    (e for e in endpoints if e.spec and e.spec.endpoint_type == EndpointType.ENDPOINT_TYPE_READ_WRITE),
    None,
)
if rw_endpoint is None:
    raise RuntimeError(f"Branch '{default_branch.name}' has no read-write endpoint.")

endpoint = w.postgres.get_endpoint(name=rw_endpoint.name)
host = endpoint.status.hosts.host if endpoint.status and endpoint.status.hosts else None
if not host:
    raise RuntimeError(f"Endpoint '{rw_endpoint.name}' has no host yet -- it may still be starting up.")
print(f"  endpoint: {endpoint.name}")
print(f"  host: {host}")

print("Generating a short-lived OAuth credential (this is the same path the app itself uses)...")
cred = w.postgres.generate_database_credential(endpoint=endpoint.name)

pg_user = LAKEBASE_PG_USER
if not pg_user:
    me = w.current_user.me()
    pg_user = me.user_name or me.emails[0].value
    print(f"LAKEBASE_PG_USER not set -- trying current identity: {pg_user}")
    print("If this fails with an auth error, a Postgres role matching this identity "
          "probably doesn't exist yet in the project -- create one and/or set LAKEBASE_PG_USER.")

conn = psycopg2.connect(
    host=host,
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
    f"  LAKEBASE_PROJECT_ID={LAKEBASE_PROJECT_ID}\n"
    f"  LAKEBASE_DATABASE={LAKEBASE_DATABASE}\n"
    f"  LAKEBASE_PG_USER={LAKEBASE_PG_USER or '(leave blank to match the app service principal identity, once a role exists for it)'}"
)

# ---- If your instance is legacy "Provisioned" instead of "Autoscaling" ----
# Provisioned uses a different, flat API -- swap the lookup/credential/connect
# steps above for:
#   import uuid
#   instance = w.database.get_database_instance(name=LAKEBASE_INSTANCE_NAME)
#   cred = w.database.generate_database_credential(
#       request_id=str(uuid.uuid4()), instance_names=[LAKEBASE_INSTANCE_NAME]
#   )
#   host = instance.read_write_dns
# everything else (the psycopg2.connect call, SAMPLE_SQL, etc.) stays the same.
