"""Opt-in read-only supplier check. Never sends email or changes evaluation cursors."""
import argparse
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from app.services.alert_data import load_daily_bars, resolve_instrument, fingerprint, DataPending


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow billed provider reads")
    parser.add_argument("--symbol", default="AAPL")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this consumes supplier credits")
    load_dotenv()
    cutoff = datetime.now(timezone(timedelta(hours=9))).replace(hour=8,minute=0,second=0,microsecond=0)
    try:
        meta = resolve_instrument(args.symbol)
        snapshot = load_daily_bars(meta, cutoff)
        print({"symbol":meta["symbol"],"mic":meta["mic"],"currency":meta["currency"],
               "target":snapshot["target"],"bars":len(snapshot["raw"]),"hash":fingerprint(snapshot)})
    except DataPending as exc:
        raise SystemExit("Data check deferred: " + exc.code) from None
