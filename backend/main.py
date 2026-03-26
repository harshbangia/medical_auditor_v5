from aiohttp import payload
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import os
import tempfile
from typing import List
from fastapi.responses import FileResponse
from backend.utils.pdf_reader import extract_text_from_pdf
from backend.ai.audit_engine import run_audit
from backend.ai.guideline_selector import select_guideline
from backend.utils.pdf_generator import generate_pdf
from fastapi import Form, Header
from backend.auth import authenticate_user, create_access_token, verify_token

from backend.db.database import SessionLocal
from backend.db.models import AuditReport
import json

app = FastAPI()

# =========================
# CORS (Frontend access)
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later restrict for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# HEALTH CHECK (IMPORTANT)
# =========================
@app.get("/")
def health():
    return {"status": "Backend is running"}

# =========================
# Login
# =========================
@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):

    user = authenticate_user(email, password)

    if not user:
        return {"error": "Invalid credentials"}

    token = create_access_token({"sub": user["email"]})

    return {"access_token": token}

# =========================
# MAIN AUDIT API
# =========================
@app.post("/audit")
async def audit(
    files: List[UploadFile] = File(...),
    authorization: str = Header(None)
):
    if not authorization:
        return {"error": "Missing token"}

    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)

    if not payload:
        return {"error": "Invalid or expired token"}

    try:
        case_text = ""

        # -------------------------
        # Read PDFs
        # -------------------------
        for file in files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name

            case_text += extract_text_from_pdf(tmp_path)
            os.remove(tmp_path)

        # -------------------------
        # Auto guideline selection
        # -------------------------
        selected_guideline = select_guideline(case_text)

        guideline_path = os.path.join("data/guidelines", selected_guideline)

        if not os.path.exists(guideline_path):
            return {
                "error": "Guideline not found",
                "selected_guideline": selected_guideline
            }

        guideline_text = extract_text_from_pdf(guideline_path)

        # -------------------------
        # Run AI Audit
        # -------------------------
        result = run_audit(case_text, guideline_text)

        # Add metadata for frontend display
        # Clean guideline name (remove .pdf, make readable)
        guideline_name = selected_guideline.replace(".pdf", "").replace("_", " ").title()

        result["guideline_used"] = guideline_name
        result["guideline_file"] = selected_guideline


        db = SessionLocal()

        db_report = AuditReport(
            user_email=payload["sub"],
            report_json=json.dumps(result)
        )

        db.add(db_report)
        db.commit()
        db.close()

        return result


    except Exception as e:
        return {"error": str(e)}


# =========================
# PDF GENERATION API
# =========================
@app.post("/generate-pdf")
async def generate_pdf_api(data: dict):

    try:
        file_path = "audit_report.pdf"

        generate_pdf(data, file_path)

        return FileResponse(
            path=file_path,
            filename="audit_report.pdf",
            media_type="application/pdf"
        )

    except Exception as e:
        return {"error": str(e)}

# =========================
# Register
# =========================
@app.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):

    from backend.db.database import SessionLocal
    from backend.db.models import User
    from backend.auth import pwd_context

    db = SessionLocal()

    existing_user = db.query(User).filter(User.email == email).first()

    if existing_user:
        db.close()
        return {"error": "User already exists"}

    hashed_password = pwd_context.hash(password)

    new_user = User(email=email, password=hashed_password)

    db.add(new_user)
    db.commit()
    db.close()

    return {"message": "User created successfully"}

@app.get("/history")
async def get_history(authorization: str = Header(None)):

    if not authorization:
        return {"error": "Missing token"}

    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)

    if not payload:
        return {"error": "Invalid token"}

    from backend.db.database import SessionLocal
    from backend.db.models import AuditReport
    import json

    db = SessionLocal()

    reports = db.query(AuditReport)\
        .filter(AuditReport.user_email == payload["sub"])\
        .order_by(AuditReport.created_at.desc())\
        .all()

    db.close()

    result = []

    for r in reports:
        result.append({
            "id": r.id,
            "created_at": r.created_at.strftime("%d-%m-%Y %H:%M"),
            "report": json.loads(r.report_json)
        })

    return result