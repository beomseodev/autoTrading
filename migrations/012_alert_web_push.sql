ALTER TABLE alert_users ADD COLUMN push_consent boolean NOT NULL DEFAULT false;
ALTER TABLE alert_users ADD COLUMN push_frequency text NOT NULL DEFAULT 'daily' CHECK(push_frequency IN ('daily','weekly'));
ALTER TABLE alert_users ADD COLUMN push_version integer NOT NULL DEFAULT 1;
CREATE TABLE alert_push_subscriptions (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES alert_users ON DELETE CASCADE,
 endpoint text NOT NULL UNIQUE, keys jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alert_push_subscriptions_user ON alert_push_subscriptions(user_id);
CREATE TABLE alert_push_deliveries (
 digest_id uuid NOT NULL REFERENCES alert_digests ON DELETE CASCADE,
 subscription_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'pending',
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(digest_id,subscription_id)
);
ALTER TABLE alert_runs ADD COLUMN push_calls integer NOT NULL DEFAULT 0;
ALTER TABLE alert_runs ADD COLUMN push_429 integer NOT NULL DEFAULT 0;
