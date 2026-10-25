CREATE INDEX alert_events_instrument_correction ON alert_events(instrument_id, created_at);
CREATE INDEX alert_events_reference_correction ON alert_events((payload->>'benchmarkId'), created_at);
