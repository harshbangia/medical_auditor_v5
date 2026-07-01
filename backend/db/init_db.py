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
    pw = env("DB_PASSWORD") or ""
    print(f"Password loaded from .env: {'yes' if pw else 'NO'} (len={len(pw)})", flush=True)
    print("Step 1/3: testing connection (10s timeout)...", flush=True)
    probe = check_db_connection()
    if not probe.get("ok"):
        print(f"ERROR: cannot connect — {probe.get('error')}", flush=True)
        print("Tip: ensure DB_PASSWORD in .env matches psql (no extra quotes/spaces).", flush=True)
        print("Tip: add DB_SSLMODE=require to .env for RDS.", flush=True)
        return 1
    print("  connected", flush=True)

    print("Step 2/3: creating tables...", flush=True)
    Base.metadata.create_all(bind=engine)
    print("Step 3/3: applying schema upgrades...", flush=True)
    upgrade_schema(engine)
    print("  schema upgrades done", flush=True)
    print("Tables created successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
