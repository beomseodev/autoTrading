ALTER TABLE alert_rules ADD COLUMN evaluation_state jsonb NOT NULL DEFAULT '{}';
