from backend.db.database import SessionLocal
from backend.db.models import AuditHistory

def save_audit(case_id, result):
    db = SessionLocal()
    try:
        record = AuditHistory(case_id=case_id, result=result)
        db.add(record)
        db.commit()
    finally:
        db.close()