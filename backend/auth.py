from jose import JWTError, jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
from dotenv import load_dotenv
import os
from backend.db.database import SessionLocal
from backend.db.models import User

# =========================
# LOAD ENV VARIABLES
# =========================
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "fallback_dev_key")  # use .env in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# =========================
# PASSWORD HASHING
# =========================
#pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# =========================
# TEMP USER (REPLACE WITH DB LATER)
# =========================
# fake_user = {
#     "email": "admin@glowix.com",
#     "hashed_password": pwd_context.hash("1234")
# }

# =========================
# VERIFY PASSWORD
# =========================
# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)

# =========================
# AUTHENTICATE USER
# =========================

def authenticate_user(email: str, password: str):
    db = SessionLocal()

    user = db.query(User).filter(User.email == email).first()

    if not user:
        db.close()
        return None

    # if not verify_password(password, user.password):
    if password != user.password:
        db.close()
        return None

    db.close()
    return {"email": user.email}

# =========================
# CREATE JWT TOKEN
# =========================
def create_access_token(data: dict):
    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    return encoded_jwt

# =========================
# VERIFY TOKEN (IMPORTANT)
# =========================
def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None



