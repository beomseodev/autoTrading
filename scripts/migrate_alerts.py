"""Run explicitly: python -m scripts.migrate_alerts. Never runs at app startup."""
import os
from dotenv import load_dotenv
from app.services.alert_store import migrate

if __name__ == "__main__":
    load_dotenv()
    url = os.getenv("ALERT_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("ALERT_MIGRATION_DATABASE_URL is required")
    migrate(url)
    print("Stock alert migrations applied.")
