import base64
import re
import streamlit as st
import os
from datetime import datetime
import uuid
import requests
import sys


from streamlit_cookies_manager import EncryptedCookieManager



cookies = EncryptedCookieManager(
    prefix="glowix",
    password="glowix-super-secret-key-2026-!@#"
)

if not cookies.ready():
    st.stop()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# =========================
# PATHS
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    from backend.utils.pdf_filename import pdf_download_filename
except ImportError:

    def pdf_download_filename(report_data: dict) -> str:
        raw = (report_data.get("patient_details") or {}).get("name") or ""
        raw = str(raw).strip()
        if not raw or raw == "-":
            return "audit_report.pdf"
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
        safe = re.sub(r"\s+", "_", safe).strip("_")
        safe = safe[:80] if safe else "audit_report"
        if not safe:
            safe = "audit_report"
        return f"{safe}_audit.pdf"

LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")
GUIDELINE_PATH = os.path.join(BASE_DIR, "data", "guidelines")

# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title="Glowix Medical Compliance Auditor",
    page_icon="🧠",
    layout="wide"
)

#API_BASE = "http://localhost:8000"
API_BASE = "http://13.61.84.162/api"
API_URL = f"{API_BASE}/audit"

BASE_FONT_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', 'Segoe UI', sans-serif; }
</style>
"""

LOGIN_CSS = """
<style>
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(155deg, #1a2744 0%, #2a3f66 28%, #64748b 28%, #e2e8f0 55%, #f1f5f9 100%) !important;
    }
    [data-testid="stAppViewContainer"] .main .block-container {
        max-width: 440px !important;
        padding: 2.25rem 2rem 2.5rem !important;
        margin-top: 5vh !important;
        margin-left: auto !important;
        margin-right: auto !important;
        background: #ffffff !important;
        border-radius: 20px !important;
        box-shadow:
            0 25px 50px -12px rgba(26, 39, 68, 0.25),
            0 0 0 1px rgba(255, 255, 255, 0.6) inset !important;
        border: 1px solid #e2e8f0 !important;
    }
    [data-testid="stAppViewContainer"] .main .block-container h1,
    [data-testid="stAppViewContainer"] .main .block-container h3 {
        color: #1a2744 !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }
    [data-testid="stAppViewContainer"] .main label,
    [data-testid="stAppViewContainer"] .main [data-testid="stWidgetLabel"] p {
        color: #334155 !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
    }
    [data-testid="stAppViewContainer"] .main div[data-baseweb="input"] {
        background: #f8fafc !important;
        border-radius: 10px !important;
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
    }
    [data-testid="stAppViewContainer"] .main div[data-baseweb="input"]:focus-within {
        border-color: #3d6fd8 !important;
        box-shadow: 0 0 0 3px rgba(61, 111, 216, 0.2) !important;
    }
    [data-testid="stAppViewContainer"] .main div[data-baseweb="input"] input {
        background: transparent !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        caret-color: #0f172a !important;
    }
    [data-testid="stAppViewContainer"] .main .stButton > button {
        width: 100% !important;
        height: 48px !important;
        border-radius: 12px !important;
        background: linear-gradient(135deg, #3d6fd8 0%, #2f5bb5 100%) !important;
        color: #ffffff !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        border: none !important;
        box-shadow: 0 4px 14px rgba(47, 91, 181, 0.45) !important;
        margin-top: 0.5rem !important;
    }
    [data-testid="stAppViewContainer"] .main .stButton > button:hover {
        filter: brightness(1.06) !important;
        box-shadow: 0 6px 20px rgba(47, 91, 181, 0.5) !important;
    }
    [data-testid="stAppViewContainer"] .main [data-testid="stImage"] { margin-bottom: 0.5rem; }
</style>
"""


def force_logout_and_relogin(message: str):
    st.error(message)
    st.session_state["is_logged_out"] = True
    st.session_state.pop("token", None)
    if "token" in cookies:
        del cookies["token"]
        cookies.save()
    st.rerun()

# =========================
# LOGIN PAGE
# =========================
def login_page():
    st.markdown(BASE_FONT_CSS, unsafe_allow_html=True)
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)

    if os.path.exists(LOGO_PATH):
        st.markdown(
            f"<div style='text-align:center;padding-bottom:0.5rem;'><img src='data:image/png;base64,{base64.b64encode(open(LOGO_PATH, 'rb').read()).decode()}' width='100' style='border-radius:16px;box-shadow:0 8px 24px rgba(26,39,68,0.12);'></div>",
            unsafe_allow_html=True
        )

    st.markdown(
        "<h3 style='text-align:center;margin:0 0 0.15rem 0;'>Glowix Medical Services Pvt. Ltd.</h3>"
        "<p style='text-align:center;color:#64748b;font-size:0.92rem;margin:0 0 1.25rem 0;'>Clinical compliance auditor — sign in to continue</p>",
        unsafe_allow_html=True,
    )

    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        response = requests.post(
            url=f"{API_BASE}/login",
            json={
                "email": email,
                "password": password
            }
        )

        if response.status_code != 200:
            st.error(response.text)
            return
        else:
            data = response.json()

            if "access_token" in data:
                st.success("Login successful")

                # 🔥 set session
                st.session_state["token"] = data["access_token"]
                st.session_state["force_login"] = False
                st.session_state["is_logged_out"] = False

                # 🔥 set cookie
                cookies["token"] = data["access_token"]
                cookies.save()

                st.rerun()
            else:
                st.error("Login failed")

# 🔥 DO NOT RESTORE IF USER LOGGED OUT
if "token" not in st.session_state:

    if not st.session_state.get("is_logged_out"):

        cookie_token = cookies.get("token")

        if cookie_token:
            st.session_state["token"] = cookie_token

# 🔥 THEN CHECK LOGIN
if "token" not in st.session_state:

    login_page()
    st.stop()

headers = {"Authorization": f"Bearer {st.session_state['token']}"}

PAGE_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', 'Segoe UI', sans-serif; }
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(165deg, #f0f4fb 0%, #e8eef8 45%, #f7f9fc 100%);
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a2744 0%, #243352 100%) !important;
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    /* Do NOT blanket-style all spans — it washes out the file uploader on white */
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown li,
    [data-testid="stSidebar"] .stMarkdown span {
        color: #e8edf5 !important;
    }
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] label {
        color: #e8edf5 !important;
    }
    [data-testid="stSidebar"] .stSelectbox label { font-weight: 600; }
    /* File uploader: force dark text + visible primary button on white dropzone */
    [data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] label {
        color: #e8edf5 !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] section,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] span,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] p,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] small,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] div[data-testid="stCaptionContainer"] {
        color: #1e293b !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
        background: #ffffff !important;
        border: 2px dashed #64748b !important;
        border-radius: 12px !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] * {
        color: #1e293b !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] button {
        background: linear-gradient(135deg, #3d6fd8 0%, #2f5bb5 100%) !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
        opacity: 1 !important;
        border-radius: 8px !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {
        filter: brightness(1.08) !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stIconMaterialIcon"] {
        color: #3d6fd8 !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        width: 100%; border-radius: 10px; font-weight: 600;
        background: linear-gradient(135deg, #3d6fd8 0%, #2f5bb5 100%);
        color: white; border: none;
    }
    [data-testid="stSidebar"] .stButton > button:hover { filter: brightness(1.08); }
    .gwx-header-bar {
        display: flex; align-items: center; gap: 1.25rem;
        padding: 1rem 1.25rem; margin: 0 -1rem 1.25rem -1rem;
        background: linear-gradient(90deg, #ffffff 0%, #f8fafc 100%);
        border-radius: 14px; border: 1px solid #e2e8f0;
        box-shadow: 0 4px 24px rgba(26, 39, 68, 0.06);
    }
    .gwx-header-bar h2 { margin: 0; color: #1a2744; font-size: 1.45rem; font-weight: 700; letter-spacing: -0.02em; }
    .gwx-header-bar .tagline { color: #64748b; font-size: 0.9rem; margin-top: 0.2rem; }
    .gwx-card {
        background: #fff; border-radius: 14px; padding: 1.35rem 1.5rem;
        border: 1px solid #e2e8f0; box-shadow: 0 2px 16px rgba(26, 39, 68, 0.05);
        margin-bottom: 1rem;
    }
    .gwx-card h3 { margin: 0 0 0.75rem 0; color: #1a2744; font-size: 1.05rem; font-weight: 700; }
    .gwx-steps ol { margin: 0; padding-left: 1.25rem; color: #334155; line-height: 1.75; }
    .gwx-steps li { margin-bottom: 0.5rem; }
    .gwx-ref-pill {
        display: inline-block; background: #1a2744; color: #fff;
        padding: 0.35rem 0.85rem; border-radius: 8px; font-size: 0.85rem; font-weight: 600; margin-right: 0.5rem;
    }
    .gwx-section-title { color: #1a2744; font-weight: 700; font-size: 1.1rem; margin: 1.25rem 0 0.75rem 0;
        padding-bottom: 0.35rem; border-bottom: 2px solid #3d6fd8; display: inline-block; }
    div[data-testid="column"] .stMetric { background: #f8fafc; padding: 0.75rem; border-radius: 10px; border: 1px solid #e2e8f0; }
</style>
"""
st.markdown(PAGE_CSS, unsafe_allow_html=True)

# =========================
# SIDEBAR
# =========================
st.sidebar.markdown("### Medical Auditor")

# ✅ GUIDELINE DROPDOWN
guidelines = os.listdir(GUIDELINE_PATH)
selected_guideline = st.sidebar.selectbox(
    "📘 Select Guideline",
    ["-- Select --"] + guidelines
)

# Upload
uploaded_files = st.sidebar.file_uploader("📂 Upload Case Documents", accept_multiple_files=True)

run = st.sidebar.button("🚀 Run Audit")

if st.sidebar.button("Logout"):

    # 🔥 set logout flag
    st.session_state["is_logged_out"] = True

    # 🔥 clear token from session
    st.session_state.pop("token", None)

    st.rerun()

# =========================
# HEADER
# =========================
logo_b64 = ""
if os.path.exists(LOGO_PATH):
    logo_b64 = base64.b64encode(open(LOGO_PATH, "rb").read()).decode()

st.markdown(
    f"""
    <div class="gwx-header-bar">
        {"<img src='data:image/png;base64," + logo_b64 + "' width='64' style='border-radius:12px'/>" if logo_b64 else ""}
        <div>
            <h2>Glowix Medical Services Pvt. Ltd.</h2>
            <div class="tagline">AI-Powered Clinical Compliance Auditor</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =========================
# LANDING (before first audit)
# =========================
if "report" not in st.session_state:
    st.markdown(
        """
        <div class="gwx-card gwx-steps">
            <h3>How to run an audit — step by step</h3>
            <ol>
                <li><strong>Select a guideline</strong> in the left sidebar (choose the PDF that matches the clinical context).</li>
                <li><strong>Upload case documents</strong> as PDFs — discharge summary, clinical notes, imaging reports, and photos (e.g. clinical pictures) if embedded in PDF.</li>
                <li>Click <strong>Run Audit</strong> and wait while text and images are processed; a structured report will appear here.</li>
                <li>Review <strong>Inference</strong>, documentation gaps, and observations; use <strong>Ask a question</strong> for follow-ups when needed.</li>
                <li>Use <strong>Edit report</strong> to correct any field, then <strong>Download PDF</strong> for a shareable file named with the patient when available.</li>
                <li>Log out from the sidebar when finished on a shared workstation.</li>
            </ol>
        </div>
        <div class="gwx-card">
            <h3>Tips for best results</h3>
            <p style="margin:0;color:#475569;line-height:1.7;">
                Prefer searchable PDFs where possible. If clinical photos or scans are only in image form, ensure they are inside the PDF pages you upload
                so the system can analyze them. You can always adjust extracted details using <strong>Edit report</strong> before exporting.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# =========================
# RUN AUDIT
# =========================
if run:

    if selected_guideline == "-- Select --":
        st.error("Please select a guideline")
        st.stop()

    if not uploaded_files:
        st.error("Upload case documents")
        st.stop()

    with st.spinner("Running audit..."):

        files = [
            ("files", (file.name, file.getvalue(), "application/pdf"))
            for file in uploaded_files
        ]

        response = requests.post(
            API_URL,
            files=files,
            data={"guideline": selected_guideline},
            headers=headers
        )

        try:
            result = response.json()
        except Exception:
            st.error("Something went wrong")
            st.text(response.text)
            st.stop()
        if response.status_code != 200:
            if response.status_code == 401:
                force_logout_and_relogin("Session expired. Please login again.")
            st.error(f"Audit failed ({response.status_code})")
            st.write(result.get("detail") or result.get("error") or response.text)
            st.stop()

        if isinstance(result, dict) and (result.get("error") or result.get("detail")):
            st.error("Audit did not return a valid report")
            st.write(result.get("detail") or result.get("error"))
            st.stop()

        # result = response.json()

        # 🔥 THIS LINE IS MISSING (CRITICAL FIX)
        st.session_state["report"] = result
        st.session_state.pop("pdf_blob", None)
        st.session_state.pop("pdf_name", None)

        st.session_state["session_id"] = result.get("session_id")

        st.session_state["audit_meta"] = {
            "audit_id": f"GMS-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
            "audit_date": datetime.now().strftime("%d/%m/%Y")
        }

    st.success("Audit Completed")

# =========================
# DISPLAY REPORT
# =========================
if "report" in st.session_state:
    if "report_edit_mode" not in st.session_state:
        st.session_state["report_edit_mode"] = False

    data = st.session_state["report"]
    meta = st.session_state.get("audit_meta", {})
    data.setdefault("patient_details", {})
    data.setdefault("insurance_details", {})
    for _k in ("insurance_company", "policy_number", "policy_period", "claim_incident_number"):
        data["insurance_details"].setdefault(_k, "")
    data.setdefault("claim_details", {})

    st.markdown('<p class="gwx-section-title" style="margin-top:0">Medical audit report</p>', unsafe_allow_html=True)

    c_act, c_edit, c_pdf = st.columns([2, 1, 1])
    with c_act:
        st.markdown(
            f"<span class='gwx-ref-pill'>Ref: {meta.get('audit_id', '-')}</span>"
            f"<span class='gwx-ref-pill' style='background:#3d6fd8'>Date: {meta.get('audit_date', '-')}</span>",
            unsafe_allow_html=True,
        )
    with c_edit:
        if st.session_state["report_edit_mode"]:
            if st.button("Done editing", use_container_width=True):
                st.session_state["report_edit_mode"] = False
                st.rerun()
        else:
            if st.button("Edit report", use_container_width=True):
                st.session_state["report_edit_mode"] = True
                st.rerun()
    with c_pdf:
        if st.button("Download PDF", use_container_width=True, type="primary"):
            pdf_payload = dict(data)
            pdf_payload["report_ref"] = meta.get("audit_id", "")
            pdf_payload["report_date"] = meta.get("audit_date", "")
            res = requests.post(f"{API_BASE}/generate-pdf", json=pdf_payload)
            if res.status_code == 200:
                st.session_state["pdf_blob"] = res.content
                st.session_state["pdf_name"] = pdf_download_filename(data)
            else:
                st.error("PDF generation failed")
                st.session_state.pop("pdf_blob", None)
                st.session_state.pop("pdf_name", None)
        if st.session_state.get("pdf_blob"):
            st.download_button(
                "Save PDF file",
                st.session_state["pdf_blob"],
                file_name=st.session_state.get("pdf_name", "audit_report.pdf"),
                mime="application/pdf",
                use_container_width=True,
                key="gwx_save_pdf",
            )

    st.caption(f"Guideline referenced: **{data.get('guideline_used', '-') }**")
    st.markdown("---")

    if st.session_state["report_edit_mode"]:
        with st.form("report_edit_form"):
            st.markdown("**Patient details**")
            ec1, ec2, ec3 = st.columns(3)
            pn = ec1.text_input("Name", value=str(data["patient_details"].get("name") or ""))
            pa = ec2.text_input("Age", value=str(data["patient_details"].get("age") or ""))
            ps = ec3.text_input("Sex", value=str(data["patient_details"].get("sex") or ""))

            st.markdown("**Insurance details**")
            ins = data["insurance_details"]
            i1, i2 = st.columns(2)
            icomp = i1.text_input("Insurance company", value=str(ins.get("insurance_company") or ""))
            ipol = i2.text_input("Policy number", value=str(ins.get("policy_number") or ""))
            i3, i4 = st.columns(2)
            iper = i3.text_input("Policy period", value=str(ins.get("policy_period") or ""))
            icl = i4.text_input("Claim / incident number", value=str(ins.get("claim_incident_number") or ""))

            st.markdown("**Claim details**")
            cc1, cc2 = st.columns(2)
            ch = cc1.text_input("Hospital", value=str(data["claim_details"].get("hospital") or ""))
            cdg = cc2.text_input("Diagnosis", value=str(data["claim_details"].get("diagnosis") or ""))

            gl = st.text_input("Guideline (display label)", value=str(data.get("guideline_used") or ""))

            inf_text = st.text_area(
                "Inference",
                value=str(data.get("inference") or data.get("auditor_conclusion") or ""),
                height=120,
            )
            rem_text = st.text_area("Remarks", value=str(data.get("remarks") or ""), height=80)

            gaps_lines = "\n".join(str(g) for g in (data.get("documentation_gaps") or []) if g)
            gaps_text = st.text_area(
                "Documentation gaps / checklist (one item per line)",
                value=gaps_lines,
                height=140,
            )

            ref_e = st.text_input("Report ref (PDF header)", value=str(meta.get("audit_id") or ""))
            date_e = st.text_input("Report date (PDF header)", value=str(meta.get("audit_date") or ""))

            if st.form_submit_button("Save changes"):
                data["patient_details"]["name"] = pn
                data["patient_details"]["age"] = pa
                data["patient_details"]["sex"] = ps
                data["insurance_details"]["insurance_company"] = icomp
                data["insurance_details"]["policy_number"] = ipol
                data["insurance_details"]["policy_period"] = iper
                data["insurance_details"]["claim_incident_number"] = icl
                data["claim_details"]["hospital"] = ch
                data["claim_details"]["diagnosis"] = cdg
                data["guideline_used"] = gl
                data["inference"] = inf_text
                data["auditor_conclusion"] = inf_text
                data["remarks"] = rem_text
                data["documentation_gaps"] = [ln.strip() for ln in gaps_text.splitlines() if ln.strip()]
                st.session_state["audit_meta"] = {
                    **meta,
                    "audit_id": ref_e or meta.get("audit_id", ""),
                    "audit_date": date_e or meta.get("audit_date", ""),
                }
                st.success("Report updated.")
                st.session_state["report_edit_mode"] = False
                st.rerun()
        st.stop()

    p = data["patient_details"]
    st.markdown('<p class="gwx-section-title">Patient details</p>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    col1.metric("Name", p.get("name") or "—")
    col2.metric("Age", p.get("age") or "—")
    col3.metric("Sex", p.get("sex") or "—")

    st.markdown('<p class="gwx-section-title">Insurance details</p>', unsafe_allow_html=True)
    ins = data["insurance_details"]
    st.markdown(
        f"""
        <div class="gwx-card" style="margin-bottom:1rem">
        <p style="margin:0.25rem 0"><strong>Insurance company:</strong> {ins.get('insurance_company') or '—'}</p>
        <p style="margin:0.25rem 0"><strong>Policy number:</strong> {ins.get('policy_number') or '—'}</p>
        <p style="margin:0.25rem 0"><strong>Policy period:</strong> {ins.get('policy_period') or '—'}</p>
        <p style="margin:0.25rem 0"><strong>Claim / incident number:</strong> {ins.get('claim_incident_number') or '—'}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c = data["claim_details"]
    st.markdown('<p class="gwx-section-title">Claim details</p>', unsafe_allow_html=True)
    st.markdown(
        f"<div class='gwx-card'><p style='margin:0.25rem 0'><strong>Hospital:</strong> {c.get('hospital') or '—'}</p>"
        f"<p style='margin:0.25rem 0'><strong>Diagnosis:</strong> {c.get('diagnosis') or '—'}</p></div>",
        unsafe_allow_html=True,
    )

    if data.get("imaging_findings"):
        st.markdown('<p class="gwx-section-title">Imaging findings</p>', unsafe_allow_html=True)
        for img in data["imaging_findings"]:
            st.markdown(
                f"""
                <div class="gwx-card">
                <p style="margin:0.2rem 0"><strong>Type:</strong> {img.get('type')}</p>
                <p style="margin:0.2rem 0"><strong>Finding:</strong> {img.get('finding')}</p>
                <p style="margin:0.2rem 0"><strong>Clinical correlation:</strong> {img.get('clinical_correlation')}</p>
                <p style="margin:0.2rem 0"><strong>Consistency:</strong> {img.get('consistency_with_diagnosis')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown('<p class="gwx-section-title">Clinical findings</p>', unsafe_allow_html=True)
    for item in data.get("clinical_findings", []):
        st.markdown(
            f"""
            <div class="gwx-card" style="padding:12px 14px">
            <b style="color:#1a2744">{item.get('parameter')}</b><br>
            <span style="color:#475569">Value:</span> {item.get('value')}<br>
            <i style="color:#64748b">{item.get('comment')}</i>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<p class="gwx-section-title">Documentation gaps / checklist</p>', unsafe_allow_html=True)
    for gap in data.get("documentation_gaps", []):
        st.warning(gap)

    st.markdown('<p class="gwx-section-title">Timeline</p>', unsafe_allow_html=True)
    for t in data.get("timeline", []):
        st.markdown(f"• **{t.get('date')}** → {t.get('event')}")

    st.markdown('<p class="gwx-section-title">Observations</p>', unsafe_allow_html=True)
    for obs in data.get("observations", []):
        st.markdown(
            f"""
            <div class="gwx-card">
            <p style="margin:0.2rem 0"><strong>Q:</strong> {obs.get('question')}</p>
            <p style="margin:0.2rem 0"><strong>Analysis:</strong> {obs.get('analysis')}</p>
            <p style="margin:0.2rem 0"><strong>Answer:</strong> {obs.get('answer')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<p class="gwx-section-title">Inference</p>', unsafe_allow_html=True)
    conclusion = (data.get("inference") or data.get("auditor_conclusion") or "").strip() or "—"
    st.success(conclusion)

    st.markdown('<p class="gwx-section-title">Remarks</p>', unsafe_allow_html=True)
    st.info(data.get("remarks") or "—")

    if data.get("qa_section"):
        st.markdown('<p class="gwx-section-title">Questions & answers</p>', unsafe_allow_html=True)
        for qa in data["qa_section"]:
            st.markdown(f"**Q:** {qa.get('question')}")
            st.write(f"**A:** {qa.get('answer')}")
            st.info(qa.get("justification") or "")
            st.markdown("---")

    question = st.text_input("Ask a follow-up question")

    if st.button("Ask"):
        if not question.strip():
            st.warning("Enter a question")
            st.stop()

        files = [
            ("files", (file.name, file.getvalue(), "application/pdf"))
            for file in uploaded_files
        ]

        res = requests.post(
            f"{API_BASE}/audit",
            data={
                "question": question,
                "guideline": selected_guideline,
                "session_id": st.session_state.get("session_id"),
            },
            headers=headers,
        )

        qa = res.json()

        if res.status_code == 401:
            force_logout_and_relogin("Session expired. Please login again.")

        if res.status_code != 200:
            st.error(qa.get("detail") or qa.get("error") or res.text)
            st.stop()

        if qa.get("mode") == "qa":
            if "qa_section" not in st.session_state["report"]:
                st.session_state["report"]["qa_section"] = []

            if qa.get("qa_section"):
                for item in qa["qa_section"]:
                    st.session_state["report"]["qa_section"].append(item)
            else:
                st.session_state["report"]["qa_section"].append({
                    "question": qa.get("question"),
                    "answer": qa.get("answer"),
                    "justification": qa.get("justification"),
                })

            st.rerun()