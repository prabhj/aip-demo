-- Sample UC data for the demo. {{CATALOG}} / {{SCHEMA}} get substituted by
-- setup/deploy_sample_data.py from config.py -- edit config.py, not this
-- file, to point a real client engagement at their own catalog/schema.
-- Assumes {{CATALOG}} already exists (creating a catalog needs metastore
-- admin rights this app's identity may not have).

CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.{{SCHEMA}};

CREATE OR REPLACE TABLE {{CATALOG}}.{{SCHEMA}}.customers (
  customer_id   INT,
  name          STRING,
  email         STRING,
  region        STRING,
  signup_date   DATE
);

INSERT INTO {{CATALOG}}.{{SCHEMA}}.customers VALUES
  (1, 'Ava Chen',        'ava.chen@example.com',        'West',    DATE '2024-01-12'),
  (2, 'Marcus Johnson',  'marcus.johnson@example.com',  'South',   DATE '2024-02-03'),
  (3, 'Priya Nair',      'priya.nair@example.com',      'East',    DATE '2024-02-20'),
  (4, 'Liam O''Brien',   'liam.obrien@example.com',     'Midwest', DATE '2024-03-05'),
  (5, 'Sofia Ramirez',   'sofia.ramirez@example.com',   'West',    DATE '2024-03-18'),
  (6, 'Noah Kim',        'noah.kim@example.com',        'South',   DATE '2024-04-02'),
  (7, 'Emma Dubois',     'emma.dubois@example.com',     'East',    DATE '2024-04-22'),
  (8, 'Wei Zhang',       'wei.zhang@example.com',       'Midwest', DATE '2024-05-09'),
  (9, 'Fatima Al-Sayed', 'fatima.alsayed@example.com',  'West',    DATE '2024-05-30'),
  (10,'Diego Fernandez', 'diego.fernandez@example.com', 'South',   DATE '2024-06-14');

CREATE OR REPLACE TABLE {{CATALOG}}.{{SCHEMA}}.orders (
  order_id      INT,
  customer_id   INT,
  order_date    DATE,
  amount        DECIMAL(10,2),
  status        STRING
);

INSERT INTO {{CATALOG}}.{{SCHEMA}}.orders VALUES
  (101, 1, DATE '2024-06-01', 129.99, 'shipped'),
  (102, 2, DATE '2024-06-02',  59.50, 'processing'),
  (103, 1, DATE '2024-06-05', 210.00, 'shipped'),
  (104, 3, DATE '2024-06-06',  75.25, 'processing'),
  (105, 4, DATE '2024-06-09', 340.10, 'delivered'),
  (106, 5, DATE '2024-06-10',  18.75, 'cancelled'),
  (107, 6, DATE '2024-06-11', 402.00, 'shipped'),
  (108, 2, DATE '2024-06-12',  95.00, 'processing'),
  (109, 7, DATE '2024-06-14', 150.00, 'delivered'),
  (110, 8, DATE '2024-06-15',  62.30, 'processing'),
  (111, 9, DATE '2024-06-16', 275.60, 'shipped'),
  (112, 10,DATE '2024-06-18',  44.00, 'processing'),
  (113, 3, DATE '2024-06-19', 189.99, 'delivered'),
  (114, 1, DATE '2024-06-20',  33.20, 'processing'),
  (115, 5, DATE '2024-06-21', 500.00, 'shipped');

-- Every write the agent executes (UC or Lakebase) logs one row here.
CREATE OR REPLACE TABLE {{CATALOG}}.{{SCHEMA}}.agent_audit_log (
  event_time         TIMESTAMP,
  app_user           STRING,
  target             STRING,   -- 'uc' or 'lakebase'
  table_name         STRING,
  statement_preview  STRING,
  result             STRING,
  success            BOOLEAN
);
