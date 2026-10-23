"""Opt-in PostgreSQL acceptance load; all market and mail responses are fixed fakes."""
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.services import alert_jobs as jobs
from app.services.alert_data import fingerprint
from app.services.alert_store import database, json_value
from app.stock_alerts import rule_hash
from tests.test_stock_alert_api import db, META
from tests.test_stock_alert_jobs import NOW


@pytest.mark.skipif(os.getenv("ALERT_RUN_LOAD_TEST") != "1", reason="opt-in 50-user PostgreSQL load")
def test_fifty_users_hundred_instruments_five_thousand_rules(db, monkeypatch):
    monkeypatch.setenv("STOCK_ALERTS_SEND_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "test-only")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("ALERT_UNSUBSCRIBE_SECRET", "test-unsubscribe-secret-with-more-than-32-chars")
    monkeypatch.setattr(jobs, "legacy_section", lambda _: None)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    snapshot = {"raw": [{"date": day, "open": value, "high": value, "low": value, "close": value, "volume": 100.}
                        for day,value in [("2026-10-20",10.),("2026-10-21",10.),("2026-10-22",100.)]],
                "splits": [], "policy": "raw-split-only-v1", "target": "2026-10-22"}
    users, instruments, watches, rules, caches = [], [], [], [], []
    for n in range(50):
        uid=uuid4(); users.append((uid,"load-user-"+str(n),f"load-{n}@example.com"))
        for k in range(2):
            iid=uuid4(); symbol=f"FAKE{n}_{k}"; meta={**META,"symbol":symbol,"providerSymbol":symbol}
            instruments.append((iid,symbol,json_value(meta)));watches.append((uuid4(),uid,iid))
            caches.append((iid,json_value(snapshot),fingerprint(snapshot),NOW-timedelta(minutes=10)))
            for j in range(50):
                params={"price":20.+j,"direction":"both"}
                rules.append((uuid4(),uid,iid,json_value(params),rule_hash("price_cross",params),NOW-timedelta(days=1)))
    with database() as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO alert_users(id,google_sub,email,consent) VALUES(%s,%s,%s,true)",users)
            cur.executemany("INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)",instruments)
            cur.executemany("INSERT INTO alert_watchlist(id,user_id,instrument_id) VALUES(%s,%s,%s)",watches)
            cur.executemany("INSERT INTO alert_rules(id,user_id,instrument_id,kind,params,fingerprint,activated_at) VALUES(%s,%s,%s,'price_cross',%s,%s,%s)",rules)
            cur.executemany("INSERT INTO alert_daily_bars(instrument_id,snapshot,fingerprint,first_seen) VALUES(%s,%s,%s,%s)",caches)
    calls, sent, durations = [], [], []
    monkeypatch.setattr(jobs,"load_daily_bars",lambda meta,*_: (calls.append(meta["symbol"]) or snapshot))
    monkeypatch.setattr(jobs,"send_payload",lambda identity,payload: (sent.append(identity) or ("accepted",str(identity))))
    for tick in range(12):
        current=NOW+timedelta(minutes=5*tick)
        monkeypatch.setattr(jobs,"utcnow",lambda: current)
        start=time.monotonic(); jobs.process_alert_run(now=current);durations.append(time.monotonic()-start)
        if len(sent)==50:break
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM alert_events").fetchone()["n"]==5000
        assert conn.execute("SELECT count(*) AS n FROM alert_digests WHERE status='accepted'").fetchone()["n"]==50
    assert len(calls)==len(set(calls))==100
    assert len(sent)==len(set(sent))==50
    assert max(durations)<210
    Path("output").mkdir(exist_ok=True)
    Path("output/stock-alert-load.json").write_text(json.dumps({"users":50,"instruments":100,"rules":5000,"fetches":len(calls),"digests":len(sent),"callSeconds":durations}),encoding="utf-8")
