CREATE TABLE IF NOT EXISTS sources (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT,
  last_scraped TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  source_id INT REFERENCES sources(id),
  source_sku TEXT,
  brand TEXT,
  model TEXT,
  category TEXT,
  price NUMERIC,
  currency TEXT,
  url TEXT,
  raw_html TEXT,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_specs (
  id SERIAL PRIMARY KEY,
  product_id INT REFERENCES products(id) ON DELETE CASCADE,
  spec_name TEXT,
  spec_value_text TEXT,
  spec_value_num NUMERIC,
  spec_unit TEXT
);