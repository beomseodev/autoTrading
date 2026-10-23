CREATE TABLE alert_users (
 id uuid PRIMARY KEY, google_sub text NOT NULL UNIQUE, email text NOT NULL,
 consent boolean NOT NULL DEFAULT false, revision integer NOT NULL DEFAULT 1,
 unsubscribe_version integer NOT NULL DEFAULT 1, suppressed text,
 legacy_consolidated boolean NOT NULL DEFAULT false, consent_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alert_instruments (
 id uuid PRIMARY KEY, symbol text NOT NULL UNIQUE, metadata jsonb NOT NULL
);
CREATE TABLE alert_watchlist (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES alert_users ON DELETE CASCADE,
 instrument_id uuid NOT NULL REFERENCES alert_instruments, archived boolean NOT NULL DEFAULT false,
 UNIQUE(user_id, instrument_id)
);
CREATE TABLE alert_rules (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES alert_users ON DELETE CASCADE,
 instrument_id uuid NOT NULL REFERENCES alert_instruments, kind text NOT NULL, params jsonb NOT NULL,
 name text NOT NULL DEFAULT '', fingerprint text NOT NULL, enabled boolean NOT NULL DEFAULT true,
 archived boolean NOT NULL DEFAULT false, revision integer NOT NULL DEFAULT 1,
 meaning_version integer NOT NULL DEFAULT 1, activated_at timestamptz NOT NULL DEFAULT now(),
 last_session date, last_result jsonb, UNIQUE(user_id, instrument_id, fingerprint)
);
CREATE TABLE alert_daily_bars (
 instrument_id uuid PRIMARY KEY REFERENCES alert_instruments, snapshot jsonb NOT NULL,
 fingerprint text NOT NULL, first_seen timestamptz NOT NULL DEFAULT now(),
 checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alert_runs (
 id uuid PRIMARY KEY, digest_date date NOT NULL UNIQUE, cutoff timestamptz NOT NULL,
 deadline timestamptz NOT NULL, lease_token uuid, lease_until timestamptz,
 status text NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alert_tasks (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES alert_runs ON DELETE CASCADE,
 kind text NOT NULL, target_id uuid NOT NULL, payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(), result jsonb, error text,
 UNIQUE(run_id, kind, target_id)
);
CREATE TABLE alert_events (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES alert_users ON DELETE CASCADE,
 rule_id uuid REFERENCES alert_rules ON DELETE CASCADE, instrument_id uuid NOT NULL REFERENCES alert_instruments,
 meaning_version integer NOT NULL, session_date date NOT NULL, trigger_kind text NOT NULL,
 snapshot_hash text NOT NULL, payload jsonb NOT NULL, canceled boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id, rule_id, meaning_version, session_date, trigger_kind)
);
CREATE TABLE alert_digests (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES alert_users ON DELETE CASCADE,
 digest_date date NOT NULL, kind text NOT NULL DEFAULT 'daily', payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
 first_attempt_at timestamptz, provider_id text UNIQUE, error text,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id, digest_date, kind)
);
CREATE TABLE alert_webhook_receipts (
 id text PRIMARY KEY, provider_id text NOT NULL, kind text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alert_provider_credits (
 minute timestamptz PRIMARY KEY, used integer NOT NULL DEFAULT 0
);
CREATE INDEX alert_rules_user ON alert_rules(user_id);
CREATE INDEX alert_events_history ON alert_events(user_id, created_at DESC, id);
CREATE INDEX alert_tasks_due ON alert_tasks(run_id, status, next_attempt_at);
