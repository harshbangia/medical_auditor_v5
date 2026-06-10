#!/usr/bin/env python3
"""Create or reset a login user in PostgreSQL.

Usage (from project root on EC2):
  python scripts/create_user.py admin@example.com yourpassword
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.database import SessionLocal
from backend.db.models import User, Base, engine


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_user.py <email> <password>")
        sys.exit(1)

    email = sys.argv[1].strip()
    password = sys.argv[2]

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.password = password
            print(f"Updated password for {email}")
        else:
            db.add(User(email=email, password=password))
            print(f"Created user {email}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
