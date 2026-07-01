"""Persist audit jobs and expose admin metrics."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from backend.db.database import SessionLocal
from backend.db.models import AuditReport, LoginEvent, User

logger = logging.getLogger("medical_auditor.audit_store")


def _patient_name_from_report(report: dict) -> str:
    if not isinstance(report, dict):
        return ""
    return str((report.get("patient_details") or {}).get("name") or "").strip()


def record_audit_started(
    *,
    user_id: int,
    user_email: str,
    job_id: str,
    file_count: int,
    guidelines: Optional[List[str]] = None,
) -> None:
    db = SessionLocal()
    try:
        row = AuditReport(
            user_id=user_id,
            user_email=user_email,
            job_id=job_id,
            file_count=file_count,
            guidelines_json=json.dumps(guidelines or []),
            status="running",
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record audit start for job %s", job_id)
    finally:
        db.close()


def record_audit_completed(
    *,
    job_id: str,
    report: Optional[dict],
    status: str = "completed",
    audit_ref: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        row = db.query(AuditReport).filter(AuditReport.job_id == job_id).first()
        if not row:
            logger.warning("No audit row found for job %s", job_id)
            return
        row.status = status
        row.completed_at = datetime.utcnow()
        if audit_ref:
            row.audit_ref = audit_ref
        if isinstance(report, dict):
            row.report_json = json.dumps(report)
            row.patient_name = _patient_name_from_report(report) or row.patient_name
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record audit completion for job %s", job_id)
    finally:
        db.close()


def list_completed_audits_for_user(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    from backend.utils.pdf_filename import pdf_download_filename

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditReport)
            .filter(AuditReport.user_id == user_id, AuditReport.status == "completed")
            .order_by(AuditReport.completed_at.desc(), AuditReport.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for row in rows:
            report = {}
            if row.report_json:
                try:
                    report = json.loads(row.report_json)
                except json.JSONDecodeError:
                    report = {}
            completed = row.completed_at or row.created_at
            out.append({
                "id": row.id,
                "audit_ref": row.audit_ref,
                "patient_name": row.patient_name or _patient_name_from_report(report),
                "completed_at": completed.strftime("%d-%m-%Y %H:%M") if completed else "",
                "completed_at_iso": completed.isoformat() if completed else "",
                "file_count": row.file_count,
                "download_filename": pdf_download_filename(report, completed_at=completed),
            })
        return out
    finally:
        db.close()


def get_completed_audit_report(audit_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(AuditReport).filter(
            AuditReport.id == audit_id,
            AuditReport.status == "completed",
        ).first()
        if not row or not row.report_json:
            return None
        try:
            report = json.loads(row.report_json)
        except json.JSONDecodeError:
            return None
        completed = row.completed_at or row.created_at
        from backend.utils.pdf_filename import pdf_download_filename
        return {
            "id": row.id,
            "user_id": row.user_id,
            "report": report,
            "completed_at": completed,
            "download_filename": pdf_download_filename(report, completed_at=completed),
        }
    finally:
        db.close()


def list_user_history(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(AuditReport)
            .filter(AuditReport.user_id == user_id, AuditReport.status == "completed")
            .order_by(AuditReport.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for row in rows:
            report = {}
            if row.report_json:
                try:
                    report = json.loads(row.report_json)
                except json.JSONDecodeError:
                    report = {}
            out.append({
                "id": row.id,
                "audit_ref": row.audit_ref,
                "patient_name": row.patient_name,
                "created_at": row.created_at.strftime("%d-%m-%Y %H:%M") if row.created_at else "",
                "file_count": row.file_count,
                "report": report,
            })
        return out
    finally:
        db.close()


def get_admin_metrics() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.email.asc()).all()
        total_logins = db.query(func.count(LoginEvent.id)).filter(LoginEvent.success.is_(True)).scalar() or 0
        total_cases = db.query(func.count(AuditReport.id)).scalar() or 0
        completed_cases = (
            db.query(func.count(AuditReport.id)).filter(AuditReport.status == "completed").scalar() or 0
        )

        per_user = []
        for user in users:
            login_count = (
                db.query(func.count(LoginEvent.id))
                .filter(LoginEvent.user_id == user.id, LoginEvent.success.is_(True))
                .scalar()
                or 0
            )
            cases_started = (
                db.query(func.count(AuditReport.id))
                .filter(AuditReport.user_id == user.id)
                .scalar()
                or 0
            )
            cases_completed = (
                db.query(func.count(AuditReport.id))
                .filter(AuditReport.user_id == user.id, AuditReport.status == "completed")
                .scalar()
                or 0
            )
            per_user.append({
                "id": user.id,
                "email": user.email,
                "role": user.role or "user",
                "is_active": bool(user.is_active),
                "created_at": user.created_at.strftime("%d-%m-%Y %H:%M") if user.created_at else "",
                "last_login_at": user.last_login_at.strftime("%d-%m-%Y %H:%M") if user.last_login_at else "—",
                "login_count": int(login_count),
                "cases_started": int(cases_started),
                "cases_completed": int(cases_completed),
            })

        return {
            "total_logins": int(total_logins),
            "total_cases": int(total_cases),
            "completed_cases": int(completed_cases),
            "active_users": sum(1 for u in users if u.is_active),
            "per_user": per_user,
        }
    finally:
        db.close()


def create_user_account(email: str, password: str, role: str = "user") -> Dict[str, Any]:
    from backend.auth import hash_password

    email = (email or "").strip().lower()
    role = (role or "user").strip().lower()
    if role not in ("admin", "user"):
        role = "user"

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            raise ValueError("User already exists")
        user = User(
            email=email,
            password=hash_password(password),
            role=role,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
        }
    except SQLAlchemyError as exc:
        db.rollback()
        raise exc
    finally:
        db.close()


def set_user_active(user_id: int, is_active: bool) -> bool:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        user.is_active = is_active
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        db.close()
