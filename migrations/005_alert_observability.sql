ALTER TABLE alert_tasks ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
UPDATE alert_tasks t SET created_at=r.created_at FROM alert_runs r WHERE t.run_id=r.id;
ALTER TABLE alert_runs ADD COLUMN data_calls integer NOT NULL DEFAULT 0;
ALTER TABLE alert_runs ADD COLUMN data_429 integer NOT NULL DEFAULT 0;
ALTER TABLE alert_runs ADD COLUMN email_calls integer NOT NULL DEFAULT 0;
ALTER TABLE alert_runs ADD COLUMN email_429 integer NOT NULL DEFAULT 0;
CREATE INDEX alert_digests_day ON alert_digests(digest_date);
