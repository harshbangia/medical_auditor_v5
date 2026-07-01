#!/usr/bin/env python3
"""Quick RDS connectivity test — run from project root with venv active."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

print("=== DB probe ===", flush=True)

from backend.config import env
from backend.db.database import CONNECT_ARGS, DATABASE_URL, DB_HOST, DB_NAME, _missing

if _missing:
    print(f"MISSING .env keys: {_missing}", flush=True)
    sys.exit(1)

print(f"host={DB_HOST} db={DB_NAME}", flush=True)
print(f"connect_args={CONNECT_ARGS}", flush=True)
pw = env("DB_PASSWORD") or ""
print(f"password in .env: len={len(pw)}", flush=True)

from sqlalchemy import create_engine, text

print("connecting...", flush=True)
engine = create_engine(DATABASE_URL, connect_args=CONNECT_ARGS, pool_pre_ping=True)
try:
    with engine.connect() as conn:
        val = conn.execute(text("SELECT 1")).scalar()
    print(f"OK — SELECT 1 => {val}", flush=True)
except Exception as exc:
    print(f"FAILED — {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1)
