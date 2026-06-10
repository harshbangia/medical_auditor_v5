import logging

from jose import JWTError, ExpiredSignatureError, jwt
from datetime import datetime, timedelta
from sqlalchemy.exc import SQLAlchemyError

from backend.config import env
from backend.db.database import SessionLocal
from backend.db.models import User

logger = logging.getLogger("medical_auditor.auth")

SECRET_KEY = env("SECRET_KEY", "fallback_dev_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(env("ACCESS_TOKEN_EXPIRE_MINUTES", "480") or "480")


def authenticate_user(email: str, password: str):
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        return None

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            logger.info("Login failed: no user for email=%s", email)
            return None
        if password != user.password:
            logger.info("Login failed: wrong password for email=%s", email)
            return None
        return {"email": user.email}
    except SQLAlchemyError as exc:
        logger.exception("Database error during login: %s", exc)
        raise
    finally:
        db.close()


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        return None
    except JWTError:
        return None
