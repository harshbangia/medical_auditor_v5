import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config import env
from backend.db.database import DB_HOST, DB_NAME, _missing, check_db_connection, engine
from backend.db.models import Base
from backend.db import models  # noqa: F401
from backend.db.schema_upgrade import upgrade_schema


def main() -> int:
    parser = argparse.ArgumentParser(description="Create database tables for medical_auditor_v5")
    parser.add_argument(
        "--legacy-upgrade",
        action="store_true",
        help="Run ALTER TABLE patches for old databases (usually not needed).",
    )
    args = parser.parse_args()

    print("=== Database init ===", flush=True)
    print("Tip: stop glowix first if step 3 hangs — sudo systemctl stop glowix", flush=True)
    if _missing:
        print(f"ERROR: missing .env keys: {_missing}", flush=True)
        return 1

    print(f"Target: host={DB_HOST} db={DB_NAME}", flush=True)
    pw = env("DB_PASSWORD") or ""
    print(f"Password loaded from .env: {'yes' if pw else 'NO'} (len={len(pw)})", flush=True)
    print("Step 1/2: testing connection (10s timeout)...", flush=True)
    probe = check_db_connection()
    if not probe.get("ok"):
        print(f"ERROR: cannot connect — {probe.get('error')}", flush=True)
        return 1
    print("  connected", flush=True)

    print("Step 2/2: creating tables from models...", flush=True)
    Base.metadata.create_all(bind=engine)
    print("  tables created", flush=True)

    if args.legacy_upgrade:
        print("Optional: legacy column patches...", flush=True)
        upgrade_schema(engine)
        print("  legacy patches done", flush=True)
    else:
        print("Skipped legacy column patches (use --legacy-upgrade only for old DBs).", flush=True)

    print("Tables created successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
