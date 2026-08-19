"""
One-time setup: creates the sample support_tickets table in the configured
Lakebase project. Requires LAKEBASE_PROJECT_ID (and optionally
LAKEBASE_PG_USER) to be set -- see config.py. Targets Lakebase Postgres
"Autoscaling" capacity mode; see tools/lakebase_connector.py's docstring if
your instance is legacy "Provisioned" instead.

Run:
    python setup/deploy_lakebase_sample.py

Note: this uses the same generate_database_credential auth path as the
app itself, so if this script can connect, the app will be able to too.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from tools.lakebase_connector import get_connection


def _has_real_sql(chunk: str) -> bool:
    for line in chunk.splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return True
    return False


if __name__ == "__main__":
    if not config.LAKEBASE_ENABLED:
        raise SystemExit("Set LAKEBASE_PROJECT_ID before running this script.")

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "sql", "lakebase", "01_sample_lakebase_schema.sql")) as f:
        sql_text = f.read()

    statements = [s.strip() for s in sql_text.split(";") if _has_real_sql(s)]

    print(f"Connecting to Lakebase project '{config.LAKEBASE_PROJECT_ID}' / db '{config.LAKEBASE_DATABASE}'...")
    conn = get_connection()
    try:
        cur = conn.cursor()
        for i, stmt in enumerate(statements, 1):
            print(f"statement {i}/{len(statements)}...")
            cur.execute(stmt)
        conn.commit()
        print(f"Done. Sample table: support_tickets ({config.LAKEBASE_DATABASE}).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("Remember to GRANT UPDATE/SELECT on support_tickets to the Postgres "
          "role the app connects as -- see README 'Governance'.")
