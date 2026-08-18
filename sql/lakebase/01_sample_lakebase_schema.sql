-- Sample Lakebase (Postgres) table for the demo -- a small operational
-- "support tickets" table, representative of the kind of OLTP data Lakebase
-- is meant for (vs. the analytical customers/orders tables in UC).
-- Run via setup/deploy_lakebase_sample.py, which connects using the same
-- generate_database_credential path the app itself uses.

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
