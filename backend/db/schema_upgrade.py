"""Lightweight schema upgrades for existing PostgreSQL databases."""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("medical_auditor.db")

_LEGACY_ALTER_STATEMENTS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'user'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS job_id VARCHAR",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS audit_ref VARCHAR",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS patient_name VARCHAR",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS guidelines_json TEXT",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS file_count INTEGER DEFAULT 0",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'completed'",
    "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
    """
    CREATE TABLE IF NOT EXISTS qa_sessions (
        session_id VARCHAR PRIMARY KEY,
        case_text TEXT NOT NULL,
        guidelines_json TEXT,
        guideline VARCHAR,
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_qa_sessions_created_at ON qa_sessions (created_at)",
)


def upgrade_schema(engine: Engine) -> None:
    """Patch legacy databases missing columns added after first deploy.

    Uses autocommit per statement (no inspect(), no long transaction) so it
    does not deadlock with a running glowix instance or init_db step 2.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for ddl in _LEGACY_ALTER_STATEMENTS:
            try:
                conn.execute(text(ddl))
            except Exception as exc:
                msg = str(exc).lower()
                if "does not exist" in msg or "undefined_table" in msg:
                    continue
                raise
    logger.info("Database schema upgrade check complete")
