CREATE INDEX alert_events_portfolio_sources ON alert_events USING gin ((payload->'sourceIds'));
