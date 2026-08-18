ALTER TABLE orders ADD COLUMN external_ref text;
CREATE UNIQUE INDEX ux_orders_external_ref ON orders(external_ref);
UPDATE schema_version SET version = 2;
