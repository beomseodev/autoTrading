ALTER TABLE alert_rules ADD COLUMN benchmark_instrument_id uuid REFERENCES alert_instruments;
