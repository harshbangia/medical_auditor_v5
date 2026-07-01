import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import bcrypt
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from backend.config import env
from backend.db.database import SessionLocal
from backend.db.models import LoginEvent, User

logger = logging.getLogger("medical_auditor.auth")

SECRET_KEY = env("SECRET_KEY", "fallback_dev_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(env("ACCESS_TOKEN_EXPIRE_MINUTES", "480") or "480")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, stored_password: str) -> bool:
    if not stored_password:
        return False
    if stored_password.startswith("$2"):
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                stored_password.encode("utf-8"),
            )
        except ValueError:
            return False
    return plain_password == stored_password


def user_to_dict(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role or "user",
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def record_login_event(
    email: str,
    success: bool,
    user_id: Optional[int] = None,
    ip_address: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            LoginEvent(
                user_id=user_id,
                email_attempt=email,
                success=success,
                ip_address=ip_address,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record login event")
    finally:
        db.close()


def authenticate_user(email: str, password: str, ip_address: Optional[str] = None):
    email = (email or "").strip().lower()
    password = password or ""
    if not email or not password:
        return None

    db = SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.email) == email).first()
        if not user or not user.is_active:
            record_login_event(email, False, user_id=user.id if user else None, ip_address=ip_address)
            logger.info("Login failed: invalid user or inactive email=%s", email)
            return None
        if not verify_password(password, user.password):
            record_login_event(email, False, user_id=user.id, ip_address=ip_address)
            logger.info("Login failed: wrong password for email=%s", email)
            return None

        if not user.password.startswith("$2"):
            user.password = hash_password(password)

        user.last_login_at = datetime.utcnow()
        db.commit()
        record_login_event(email, True, user_id=user.id, ip_address=ip_address)
        return user_to_dict(user)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Database error during login: %s", exc)
        raise
    finally:
        db.close()


def create_access_token(user: Dict[str, Any]) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user["email"],
        "uid": user["id"],
        "role": user.get("role", "user"),
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        return None
    except JWTError:
        return None


def get_user_from_token(token: str) -> Optional[Dict[str, Any]]:
    payload = verify_token(token)
    if not payload:
        return None

    db = SessionLocal()
    try:
        user = None
        if payload.get("uid"):
            user = db.query(User).filter(User.id == payload["uid"]).first()
        if not user and payload.get("sub"):
            user = db.query(User).filter(func.lower(User.email) == str(payload["sub"]).lower()).first()
        if not user or not user.is_active:
            return None
        return user_to_dict(user)
    finally:
        db.close()


def require_user(authorization: Optional[str]) -> Dict[str, Any]:
    from fastapi import HTTPException

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    user = get_user_from_token(parts[1].strip())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def require_admin(authorization: Optional[str]) -> Dict[str, Any]:
    from fastapi import HTTPException

    user = require_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
