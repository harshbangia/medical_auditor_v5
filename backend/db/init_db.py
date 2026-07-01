import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config import env
from backend.db.database import DB_HOST, DB_NAME, _missing, check_db_connection, engine
from backend.db.models import Base
from backend.db import models  # noqa: F401
from backend.db.schema_upgrade import upgrade_schema


def main() -> int:
    print("=== Database init ===", flush=True)
    if _missing:
        print(f"ERROR: missing .env keys: {_missing}", flush=True)
        return 1

    print(f"Target: host={DB_HOST} db={DB_NAME}", flush=True)
    print("Step 1/3: testing connection (10s timeout)...", flush=True)
    probe = check_db_connection()
    if not probe.get("ok"):
        print(f"ERROR: cannot connect — {probe.get('error')}", flush=True)
        print("Tip: RDS usually needs sslmode=require (now default for remote hosts).", flush=True)
        return 1
    print(f"  connected (users table count: {probe.get('users', 0)})", flush=True)

    print("Step 2/3: creating tables...", flush=True)
    Base.metadata.create_all(bind=engine)
    print("Step 3/3: applying schema upgrades...", flush=True)
    upgrade_schema(engine)
    print("Tables created successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
