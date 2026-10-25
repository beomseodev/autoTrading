ALTER TABLE alert_runs ADD COLUMN duration_seconds double precision NOT NULL DEFAULT 0;
ALTER TABLE alert_runs ADD COLUMN rate_limit_count integer NOT NULL DEFAULT 0;
