CREATE TABLE alert_information_cache (
 cache_key text PRIMARY KEY, snapshot jsonb NOT NULL, checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alert_external_credits (
 provider text NOT NULL, minute timestamptz NOT NULL, used integer NOT NULL DEFAULT 0,
 PRIMARY KEY(provider,minute)
);
