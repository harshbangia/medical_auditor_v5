from fastapi import FastAPI, UploadFile, File, Form, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi import HTTPException
from typing import List, Optional
from uuid import uuid4
import os
import json
import tempfile
import traceback
from backend.ai.audit_engine import run_audit
from backend.utils.pdf_generator import generate_pdf
from backend.utils.pdf_filename import pdf_download_filename
from starlette.background import BackgroundTask
from backend.auth import authenticate_user, create_access_token, verify_token

from backend.db.database import SessionLocal
from backend.db.models import AuditReport

from backend.rag.vector_store import search
from backend.services.s3_utils import guidelines_cache
from backend.services.audit_jobs import create_job, get_job, run_job_in_background
from backend.services.audit_pipeline import run_job_audit

GLOBAL_CACHE = {}

import logging
import time

app = FastAPI()
logger = logging.getLogger("medical_auditor.audit")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)


def _audit_log(request_id: str, message: str):
    logger.info("[audit:%s] %s", request_id, message)
    print(f"[audit:{request_id}] {message}", flush=True)


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _normalize_timeline(result: dict):
    """Ensure key medico-legal events always appear in timeline when available."""
    claim = result.get("claim_details") or {}
    timeline = result.get("timeline") or []
    if not isinstance(timeline, list):
        timeline = []

    def _norm(s: str) -> str:
        return " ".join(str(s or "").strip().lower().split())

    def _is_unknown_date(s: str) -> bool:
        val = _norm(s)
        return (not val) or (val in {"-", "na", "n/a", "not available", "unknown"}) or ("*" in val)

    normalized = []
    seen_signatures = set()
    existing_event_keys = set()

    for item in timeline:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").strip()
        event = str(item.get("event") or "").strip()
        if not event and not date:
            continue
        if _is_unknown_date(date):
            date = ""
        signature = (_norm(event), _norm(date))
        if signature in seen_signatures:
            continue
        normalized.append({"date": date, "event": event})
        seen_signatures.add(signature)
        if event:
            existing_event_keys.add(_norm(event))

    required = [
        ("consultation_date", "Consultation date"),
        ("date_of_admission", "Date of admission"),
        ("date_of_discharge", "Date of discharge"),
        ("procedure_or_surgery", "Procedure / surgery done"),
        ("nature_of_admission", "Nature of admission"),
    ]

    for field, label in required:
        value = str(claim.get(field) or "").strip()
        if not value or _is_unknown_date(value):
            continue
        norm_label = _norm(label)
        norm_value = _norm(value)

        # Skip if the same event label already exists.
        if norm_label in existing_event_keys:
            continue

        # Skip if a semantically equivalent event/value pair is already present.
        duplicate_found = False
        for item in normalized:
            e = _norm(item.get("event", ""))
            d = _norm(item.get("date", ""))
            if not e and not d:
                continue
            if (
                (norm_label in e or e in norm_label)
                and (norm_value in e or e in norm_value or norm_value == d)
            ):
                duplicate_found = True
                break
        if duplicate_found:
            continue
        normalized.append({"date": value, "event": label})
        seen_signatures.add((norm_label, norm_value))

    result["timeline"] = normalized

# =========================
# CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def health():
    return {"status": "Backend is running"}


@app.get("/health/db")
def health_db():
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError
    from backend.db.database import SessionLocal, _missing

    if _missing:
        return {"db": "misconfigured", "missing_env": _missing}

    db = SessionLocal()
    try:
        count = db.execute(text("SELECT COUNT(*) FROM users")).scalar()
        return {"db": "ok", "users": count}
    except SQLAlchemyError as exc:
        logger.exception("DB health check failed")
        return {"db": "error", "detail": str(exc)}
    finally:
        db.close()


@app.on_event("startup")
def warm_guidelines_cache():
    try:
        names = guidelines_cache.get()
        logger.info("Guidelines cache warmed (%d PDFs)", len(names))
    except Exception as exc:
        logger.warning("Guidelines cache warm-up failed: %s", exc)


@app.get("/guidelines")
def list_guidelines(refresh: bool = False):
    try:
        names = guidelines_cache.get(force_refresh=refresh)
        return {"guidelines": names, "cached": not refresh}
    except Exception as e:
        logger.exception("Failed to list S3 guidelines: %s", e)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        guideline_dir = os.path.join(base_dir, "data", "guidelines")
        if os.path.isdir(guideline_dir):
            names = [f for f in os.listdir(guideline_dir) if f.lower().endswith(".pdf")]
            if names:
                return {"guidelines": sorted(names), "cached": False, "fallback": "local"}
        raise HTTPException(status_code=503, detail="Failed to load guidelines. Try again shortly.")


@app.get("/audit/status/{job_id}")
def audit_status(job_id: str, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = _extract_bearer_token(authorization)
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = {
        "job_id": job.job_id,
        "status": job.status,
        "phase": job.phase,
        "progress": job.progress,
        "message": job.message,
    }
    if job.status == "completed" and job.result:
        payload["result"] = job.result
    if job.status == "failed":
        payload["error"] = job.error or job.message
    return payload


# =========================
# LOGIN
# =========================
from  pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/login")
def login(data: LoginRequest):
    from sqlalchemy.exc import SQLAlchemyError

    try:
        user = authenticate_user(data.email, data.password)
    except SQLAlchemyError:
        logger.exception("Login database error")
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Check .env and RDS connection on the server.",
        )

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": user["email"]})

    return {
        "access_token": token,
        "token_type": "bearer"
    }


# =========================
# REGISTER
# =========================
# @app.post("/register")
# async def register(email: str = Form(...), password: str = Form(...)):
#
#     db = SessionLocal()
#
#     if db.query(User).filter(User.email == email).first():
#         db.close()
#         return {"error": "User already exists"}
#
#     new_user = User(email=email, password=password)
#
#     db.add(new_user)
#     db.commit()
#     db.close()
#
#     return {"message": "User created successfully"}


# =========================
# MAIN AUDIT API
# =========================
@app.post("/audit")
async def audit(
    request: Request,
    files: Optional[List[UploadFile]] = File(None),
    guideline: Optional[str] = Form(None),
    question: Optional[str] = Form(None),
    session_id: str = Form(None),
    authorization: str = Header(None)
):
    request_id = str(uuid4())[:8]
    started_at = time.time()
    _audit_log(request_id, "hit /audit")
    _audit_log(request_id, f"files received: {files}")
    _audit_log(request_id, f"guideline received: {guideline}")
    _audit_log(request_id, f"has authorization header: {bool(authorization)}")
    # =========================
    # ⚡ FAST QA MODE (NO OCR)
    # =========================
    if question and session_id:

        cached = GLOBAL_CACHE.get(session_id)

        if not cached:
            _audit_log(request_id, f"qa mode cache miss for session_id={session_id}")
            raise HTTPException(status_code=404, detail="Session expired")

        _audit_log(request_id, "fast QA mode (no OCR)")

        case_text = cached["case_text"]
        images = cached["images"]
        guideline = cached["guideline"]
        index = cached["index"]
        chunks = cached["chunks"]

        relevant_guideline = search(index, chunks, question, top_k=10)

        result = run_audit(
            case_text,
            relevant_guideline,
            user_question=question,
            images=images
        )
        _audit_log(
            request_id,
            f"fast QA completed; response keys={list(result.keys()) if isinstance(result, dict) else type(result)}"
        )
        return result
    # =========================
    # AUTH (KEEP SAME)
    # =========================
    if not authorization:
        _audit_log(request_id, "missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing token")

    token = _extract_bearer_token(authorization)
    if not token:
        _audit_log(request_id, "malformed Authorization header")
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    payload = verify_token(token)
    _audit_log(request_id, "token parsed; verifying")
    if not payload:
        _audit_log(request_id, "invalid or expired token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        if not files:
            raise HTTPException(status_code=400, detail="Upload case documents")

        file_items = []
        seen_names = set()
        for file in files:
            await file.seek(0)
            file_bytes = await file.read()
            if not file_bytes:
                continue
            name = file.filename or "upload.pdf"
            if name in seen_names:
                continue
            seen_names.add(name)
            file_items.append((name, file_bytes))

        if not file_items:
            raise HTTPException(status_code=400, detail="No valid PDF files uploaded")

        _audit_log(request_id, f"queued {len(file_items)} unique PDF(s) for async audit")
        job = create_job()
        run_job_in_background(
            job,
            lambda j: run_job_audit(
                j, file_items, guideline, question, GLOBAL_CACHE
            ),
        )
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.job_id,
                "status": "queued",
                "message": "Audit started. Poll /audit/status/{job_id} for progress.",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        _audit_log(request_id, f"audit failed after {time.time() - started_at:.2f}s: {str(e)}")
        traceback.print_exc()
        raise

# =========================
# PDF GENERATION
# =========================
def _unlink_temp(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


@app.post("/generate-pdf")
async def generate_pdf_api(data: dict):
    download_name = pdf_download_filename(data)
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        generate_pdf(data, tmp_path)
    except Exception:
        _unlink_temp(tmp_path)
        raise
    return FileResponse(
        path=tmp_path,
        filename=download_name,
        media_type="application/pdf",
        background=BackgroundTask(_unlink_temp, tmp_path),
    )


# =========================
# HISTORY
# =========================
@app.get("/history")
async def get_history(authorization: str = Header(None)):

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    payload = verify_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db = SessionLocal()

    reports = db.query(AuditReport)\
        .filter(AuditReport.user_email == payload["sub"])\
        .order_by(AuditReport.created_at.desc())\
        .all()

    db.close()

    return [
        {
            "id": r.id,
            "created_at": r.created_at.strftime("%d-%m-%Y %H:%M"),
            "report": json.loads(r.report_json)
        }
        for r in reports
    ]

def chunk_text(text, size=3000, overlap=300):
    chunks = []
    start = 0

    while start < len(text):
        chunks.append(text[start:start+size])
        start += size - overlap

    return chunks

