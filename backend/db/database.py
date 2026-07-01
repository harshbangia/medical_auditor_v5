import logging
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.config import env

logger = logging.getLogger("medical_auditor.db")

DB_USER = env("DB_USER")
DB_PASSWORD = env("DB_PASSWORD")
DB_HOST = env("DB_HOST")
DB_PORT = env("DB_PORT", "5432")
DB_NAME = env("DB_NAME")
DB_SSLMODE = env("DB_SSLMODE")
DB_CONNECT_TIMEOUT = int(env("DB_CONNECT_TIMEOUT", "10") or "10")

_missing = [k for k, v in {
    "DB_USER": DB_USER, "DB_PASSWORD": DB_PASSWORD,
    "DB_HOST": DB_HOST, "DB_NAME": DB_NAME,
}.items() if not v]

if _missing:
    logger.warning(
        "Missing DB env vars: %s — login will fail until .env is configured.", _missing
    )
    DATABASE_URL = "postgresql://localhost/placeholder"
    CONNECT_ARGS = {"connect_timeout": DB_CONNECT_TIMEOUT}
else:
    DATABASE_URL = (
        f"postgresql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    sslmode = DB_SSLMODE
    if not sslmode:
        sslmode = "require" if DB_HOST not in ("localhost", "127.0.0.1") else "prefer"
    CONNECT_ARGS = {
        "connect_timeout": DB_CONNECT_TIMEOUT,
        "sslmode": sslmode,
    }

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=CONNECT_ARGS,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def check_db_connection() -> dict:
    """Quick connectivity probe with a short timeout."""
    if _missing:
        return {"ok": False, "error": f"missing env: {_missing}"}
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        return {"ok": True, "users": int(count or 0)}
    except Exception as exc:
        logger.exception("Database connection failed")
        return {"ok": False, "error": str(exc)}
