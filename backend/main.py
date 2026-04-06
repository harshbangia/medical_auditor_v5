from fastapi import FastAPI, UploadFile, File, Form, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import List, Optional
from backend.ai.audit_engine import extract_case_summary
from fastapi import HTTPException
from backend.utils.pdf_reader import extract_text_and_images
from fastapi import Form
from uuid import uuid4
import os
import tempfile
import json
import traceback
from backend.utils.pdf_reader import extract_images_from_pdf
from backend.utils.pdf_reader import extract_text_from_pdf
from backend.ai.audit_engine import run_audit
from backend.utils.pdf_generator import generate_pdf
from backend.auth import authenticate_user, create_access_token, verify_token

from backend.db.database import SessionLocal
from backend.db.models import AuditReport, User

from backend.rag.rag_manager import get_or_create_index
from backend.rag.vector_store import search
GLOBAL_CACHE = {}

import boto3


USE_S3 = False

s3 = boto3.client("s3")
BUCKET_NAME = "glowix-medical-auditor"

app = FastAPI()

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


# =========================
# LOGIN
# =========================
from  pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/login")
def login(data: LoginRequest):
    user = authenticate_user(data.email, data.password)

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
    # =========================
    # ⚡ FAST QA MODE (NO OCR)
    # =========================
    if question and session_id:

        cached = GLOBAL_CACHE.get(session_id)

        if not cached:
            return {"error": "Session expired"}

        print("⚡ FAST QA MODE (no OCR)")

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
        print("final result",result)
        return result
    # =========================
    # AUTH (KEEP SAME)
    # =========================
    if not authorization:
        return {"error": "Missing token"}

    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)

    if not payload:
        return {"error": "Invalid or expired token"}

    try:
        case_text = ""
        images = []

        # =========================
        # FILE PROCESSING (UPDATED)
        # =========================
        case_texts = []

        if files:
            for file in files:

                await file.seek(0)
                file_bytes = await file.read()

                if not file_bytes:
                    print("❌ EMPTY FILE:", file.filename)
                    continue

                tmp_path = None

                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(file_bytes)
                        tmp.flush()
                        tmp_path = tmp.name
                        s3 = boto3.client("s3")
                        BUCKET_NAME = "glowix-medical-auditor"
                        if USE_S3:
                            s3_key = f"uploads/{uuid4()}.pdf"
                            s3.upload_file(tmp_path, BUCKET_NAME, s3_key)

                            s3_url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
                            print("☁️ Uploaded to S3:", s3_url)

                    # ✅ TEXT EXTRACTION
                    # 🔥 SINGLE OCR PASS (TEXT + IMAGES)
                    text, imgs = extract_text_and_images(tmp_path)

                    if text.strip():
                        case_texts.append(text)
                    else:
                        print("⚠️ No text extracted from:", file.filename)

                    images.extend(imgs)

                    # # ✅ IMAGE EXTRACTION
                    # imgs = extract_images_from_pdf(tmp_path)
                    # images.extend(imgs)

                except Exception as e:
                    print("❌ File processing failed:", str(e))

                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)

        # ✅ FINAL MERGE (NO LIMIT)
        case_text = "\n\n".join(case_texts)

        print("🔥 TOTAL CASE LENGTH:", len(case_text))
        print("🖼️ TOTAL IMAGES:", len(images))

        if len(case_text.strip()) < 50:
            return {"error": "No meaningful text extracted"}

        # =========================
        # GUIDELINE (RAG)
        # =========================

        from backend.ai.guideline_selector import select_guideline

        if not guideline:
            print("🤖 Auto-selecting guideline...")
            guideline = select_guideline(case_text)

        # ✅ CLEAN AGAIN (extra safety)
        guideline = guideline.strip().replace('"', '').replace("'", "")

        print("📘 FINAL GUIDELINE:", guideline)

        USE_S3_GUIDELINE = True  # toggle

        if USE_S3_GUIDELINE:


            s3 = boto3.client("s3")
            BUCKET_NAME = "glowix-medical-auditor"

            key = f"guidelines/{guideline}"

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                s3.download_file(BUCKET_NAME, key, tmp.name)
                guideline_path = tmp.name

        else:
            BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            guideline_path = os.path.join(BASE_DIR, "data", "guidelines", guideline)

        print("📂 FULL PATH:", guideline_path)

        if not os.path.exists(guideline_path):
            available = os.listdir(os.path.join(BASE_DIR, "data", "guidelines"))
            print("📁 AVAILABLE FILES:", available)
            return {"error": f"Guideline not found: {guideline}"}

        # =========================
        # GET QUESTION FROM BODY (IMPORTANT)
        # =========================
        # body = await request.json() if request else {}
        user_question = question
        print("❓ USER QUESTION:", user_question)

        index, chunks = get_or_create_index(guideline)

        query = case_text[:5000]



        if user_question:
            print("🧠 QA MODE → using focused retrieval")

            # 🔥 Use question itself for retrieval
            query_text = (user_question or "") + "\n" + case_text[:3000]

            relevant_guideline = search(
                index,
                chunks,
                query_text,
                top_k=10
            )

        else:
            print("🧠 AUDIT MODE → using smart RAG")

            relevant_guideline = search(
                index,
                chunks,
                query,
                top_k=6
            )

        print("📚 GUIDELINE LENGTH:", len(relevant_guideline))

        if not relevant_guideline.strip():
            return {"error": "Guideline retrieval failed"}

        # =========================
        # LIMIT SIZE (CRITICAL)
        # =========================
        def limit_text(text, max_chars):
            return text[:max_chars] if len(text) > max_chars else text

        if user_question:
            case_text = case_text[:20000]  # more context for QA
        else:
            case_text = case_text[:20000]

        relevant_guideline = limit_text(relevant_guideline, 10000)


        # =========================
        # RUN AUDIT
        # =========================
        print('case length:', len(case_text))
        print('text length:', case_text[:200])
        result = run_audit(
            case_text,
            relevant_guideline,
            user_question=user_question,
            images=images
        )


        session_id = str(uuid4())

        GLOBAL_CACHE[session_id] = {
            "case_text": case_text,
            "images": images,
            "guideline": guideline,
            "index": index,
            "chunks": chunks
        }

        result["session_id"] = session_id
        if not result:
            return {"error": "Empty AI response"}

        if "error" in result:
            return result

        # =========================
        # SAFETY (PREVENT UI BREAK)
        # =========================
        result.setdefault("patient_details", {})
        result.setdefault("claim_details", {})
        result.setdefault("clinical_findings", [])
        result.setdefault("documentation_gaps", [])
        result.setdefault("timeline", [])
        result.setdefault("observations", [])
        result.setdefault("auditor_conclusion", "")
        result.setdefault("remarks", "")
        result.setdefault("qa_section", [])

        return result


    except Exception as e:

        print("❌ AUDIT FAILED:")

        traceback.print_exc()  # 🔥 THIS WILL SHOW EXACT LINE

        return {"error": str(e)}

# =========================
# PDF GENERATION
# =========================
@app.post("/generate-pdf")
async def generate_pdf_api(data: dict):
    file_path = "audit_report.pdf"
    generate_pdf(data, file_path)

    return FileResponse(
        path=file_path,
        filename="audit_report.pdf",
        media_type="application/pdf"
    )


# =========================
# HISTORY
# =========================
@app.get("/history")
async def get_history(authorization: str = Header(None)):

    if not authorization:
        return {"error": "Missing token"}

    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)

    if not payload:
        return {"error": "Invalid token"}

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

