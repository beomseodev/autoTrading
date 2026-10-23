ALTER TABLE alert_runs ADD COLUMN audience jsonb NOT NULL DEFAULT '[]';
ALTER TABLE alert_digests ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE alert_digests ADD COLUMN payload_cleared boolean NOT NULL DEFAULT false;
