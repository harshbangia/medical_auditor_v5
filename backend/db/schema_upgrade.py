"""Lightweight schema upgrades for existing PostgreSQL databases."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("medical_auditor.db")

_USER_COLUMNS = (
    ("role", "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'user'"),
    ("is_active", "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"),
    ("created_at", "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP"),
    ("last_login_at", "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP"),
)

_AUDIT_COLUMNS = (
    ("user_id", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS user_id INTEGER"),
    ("job_id", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS job_id VARCHAR"),
    ("audit_ref", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS audit_ref VARCHAR"),
    ("patient_name", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS patient_name VARCHAR"),
    ("guidelines_json", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS guidelines_json TEXT"),
    ("file_count", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS file_count INTEGER DEFAULT 0"),
    ("status", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'completed'"),
    ("completed_at", "ALTER TABLE audit_reports ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP"),
)


def upgrade_schema(engine: Engine) -> None:
    """Apply additive column changes without inspect() inside an open transaction.

    Calling inspect(engine) while a transaction holds ALTER locks on the same table
    can deadlock PostgreSQL (seen on RDS during init_db step 3).
    """
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    statements: list[str] = []
    if "users" in tables:
        statements.extend(ddl for _col, ddl in _USER_COLUMNS)
    if "audit_reports" in tables:
        statements.extend(ddl for _col, ddl in _AUDIT_COLUMNS)

    if not statements:
        logger.info("No schema upgrades required")
        return

    with engine.begin() as conn:
        for ddl in statements:
            conn.execute(text(ddl))

    logger.info("Database schema upgrade check complete")
