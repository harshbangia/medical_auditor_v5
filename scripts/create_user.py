#!/usr/bin/env python3
"""Create or reset a login user in PostgreSQL.

Usage (from project root):
  python scripts/create_user.py admin@example.com yourpassword admin
  python scripts/create_user.py user@example.com yourpassword user
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.auth import hash_password
from backend.db.database import SessionLocal, engine
from backend.db.models import Base, User
from backend.db.schema_upgrade import upgrade_schema
from datetime import datetime


def main():
    if len(sys.argv) not in (3, 4):
        print("Usage: python scripts/create_user.py <email> <password> [admin|user]")
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    role = (sys.argv[3] if len(sys.argv) == 4 else "user").strip().lower()
    if role not in ("admin", "user"):
        role = "user"

    Base.metadata.create_all(bind=engine)
    upgrade_schema(engine)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        hashed = hash_password(password)
        if user:
            user.password = hashed
            user.role = role
            user.is_active = True
            print(f"Updated user {email} (role={role})")
        else:
            db.add(
                User(
                    email=email,
                    password=hashed,
                    role=role,
                    is_active=True,
                    created_at=datetime.utcnow(),
                )
            )
            print(f"Created user {email} (role={role})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
