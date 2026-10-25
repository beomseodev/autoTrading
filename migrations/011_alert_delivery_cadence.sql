ALTER TABLE alert_users ADD COLUMN email_frequency text NOT NULL DEFAULT 'daily'
 CHECK (email_frequency IN ('daily','weekly'));
ALTER TABLE alert_digests ADD COLUMN channel text NOT NULL DEFAULT 'email';
ALTER TABLE alert_digests DROP CONSTRAINT alert_digests_user_id_digest_date_kind_key;
ALTER TABLE alert_digests ADD UNIQUE(user_id,digest_date,kind,channel);
