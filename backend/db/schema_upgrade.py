"""Lightweight schema upgrades for existing PostgreSQL databases."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("medical_auditor.db")


def _has_column(engine: Engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def _has_table(engine: Engine, table: str) -> bool:
    return table in inspect(engine).get_table_names()


def upgrade_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        if _has_table(engine, "users"):
            if not _has_column(engine, "users", "role"):
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'user'"))
            if not _has_column(engine, "users", "is_active"):
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            if not _has_column(engine, "users", "created_at"):
                conn.execute(text("ALTER TABLE users ADD COLUMN created_at TIMESTAMP"))
            if not _has_column(engine, "users", "last_login_at"):
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP"))

        if _has_table(engine, "audit_reports"):
            for col, ddl in (
                ("user_id", "ALTER TABLE audit_reports ADD COLUMN user_id INTEGER"),
                ("job_id", "ALTER TABLE audit_reports ADD COLUMN job_id VARCHAR"),
                ("audit_ref", "ALTER TABLE audit_reports ADD COLUMN audit_ref VARCHAR"),
                ("patient_name", "ALTER TABLE audit_reports ADD COLUMN patient_name VARCHAR"),
                ("guidelines_json", "ALTER TABLE audit_reports ADD COLUMN guidelines_json TEXT"),
                ("file_count", "ALTER TABLE audit_reports ADD COLUMN file_count INTEGER DEFAULT 0"),
                ("status", "ALTER TABLE audit_reports ADD COLUMN status VARCHAR DEFAULT 'completed'"),
                ("completed_at", "ALTER TABLE audit_reports ADD COLUMN completed_at TIMESTAMP"),
            ):
                if not _has_column(engine, "audit_reports", col):
                    conn.execute(text(ddl))

    logger.info("Database schema upgrade check complete")
