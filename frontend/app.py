import base64
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
API_BASE = "http://51.20.128.148:8501/"
API_URL = f"{API_BASE}/audit"

# =========================
# LOGIN PAGE
# =========================
def login_page():

    st.markdown("""
    <style>
    .block-container { max-width: 420px; padding-top: 60px; }
    .custom-label { margin-bottom: 4px; font-size: 15px; font-weight: 500; }
    div[data-baseweb="input"] > div { height: 42px; border-radius: 8px; }
    .stButton > button {
        width: 100%; height: 45px; border-radius: 10px;
        background-color: #2f63d6; color: white; font-weight: 600; border: none;
    }
    </style>
    """, unsafe_allow_html=True)

    if os.path.exists(LOGO_PATH):
        st.markdown(
            f"<div style='text-align:center;'><img src='data:image/png;base64,{base64.b64encode(open(LOGO_PATH, 'rb').read()).decode()}' width='120'></div>",
            unsafe_allow_html=True
        )

    st.markdown("<h3 style='text-align:center;'>Glowix Medical Services Pvt. Ltd.</h3>", unsafe_allow_html=True)

    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        response = requests.post(
            url=f"{API_BASE}/login",
            data={
                "email": email,
                "password": password
            }
        )

        if response.status_code != 200:
            st.error(response.text)
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

# =========================
# SIDEBAR
# =========================
st.sidebar.title("🧠 Medical Auditor")

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
col1, col2 = st.columns([1, 5])

with col1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=80)

with col2:
    st.markdown("## Glowix Medical Services Pvt. Ltd.")
    st.caption("AI-Powered Clinical Audit System")

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

        result = response.json()

        # 🔥 THIS LINE IS MISSING (CRITICAL FIX)
        st.session_state["report"] = result

        st.session_state["session_id"] = result.get("session_id")

        st.session_state["audit_meta"] = {
            "audit_id": f"GMS-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
            "audit_date": datetime.now().strftime("%d/%m/%Y")
        }

    st.success("Audit Completed")

# =========================
# DISPLAY REPORT (FULL PDF MATCH)
# =========================
# =========================
# DISPLAY REPORT (PREMIUM UI)
# =========================
if "report" in st.session_state:


    data = st.session_state["report"]
    meta = st.session_state.get("audit_meta", {})

    st.markdown("## 📄 Medical Audit Report")

    # =========================
    # HEADER
    # =========================
    st.markdown(f"""
    **Guideline:** {data.get('guideline_used','-')}  
    **Ref No:** {meta.get('audit_id','-')}  
    **Date:** {meta.get('audit_date','-')}
    """)

    st.markdown("---")

    # =========================
    # PATIENT DETAILS
    # =========================
    st.subheader("👤 Patient Details")
    p = data.get("patient_details", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("Name", p.get("name", "-"))
    col2.metric("Age", p.get("age", "-"))
    col3.metric("Sex", p.get("sex", "-"))

    st.markdown("---")

    # =========================
    # CLAIM DETAILS
    # =========================
    st.subheader("🏥 Claim Details")
    c = data.get("claim_details", {})

    st.write(f"**Hospital:** {c.get('hospital','-')}")
    st.write(f"**Diagnosis:** {c.get('diagnosis','-')}")

    st.markdown("---")

    # =========================
    # IMAGING
    # =========================
    if data.get("imaging_findings"):
        st.subheader("🩻 Imaging Findings")

        for img in data["imaging_findings"]:
            st.markdown(f"""
            **Type:** {img.get('type')}  
            **Finding:** {img.get('finding')}  
            **Clinical Correlation:** {img.get('clinical_correlation')}  
            **Consistency:** {img.get('consistency_with_diagnosis')}
            """)
            st.markdown("---")

    # =========================
    # CLINICAL FINDINGS
    # =========================
    st.subheader("🧪 Clinical Findings")

    for item in data.get("clinical_findings", []):
        st.markdown(f"""
        <div style='padding:12px;border-radius:10px;background:#f8f9fa;margin-bottom:10px'>
        <b>{item.get('parameter')}</b><br>
        Value: {item.get('value')}<br>
        <i>{item.get('comment')}</i>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # =========================
    # DOCUMENTATION GAPS
    # =========================
    st.subheader("⚠️ Documentation Gaps")

    for gap in data.get("documentation_gaps", []):
        st.warning(gap)

    st.markdown("---")

    # =========================
    # TIMELINE
    # =========================
    st.subheader("📅 Timeline")

    for t in data.get("timeline", []):
        st.markdown(f"• **{t.get('date')}** → {t.get('event')}")

    st.markdown("---")

    # =========================
    # OBSERVATIONS
    # =========================
    st.subheader("🔍 Observations")

    for obs in data.get("observations", []):
        st.markdown(f"""
        **Q:** {obs.get('question')}  
        **Analysis:** {obs.get('analysis')}  
        **Answer:** {obs.get('answer')}
        """)
        st.markdown("---")

    # =========================
    # CONCLUSION
    # =========================
    st.subheader("✅ Conclusion")
    st.success(data.get("auditor_conclusion"))

    # =========================
    # REMARKS
    # =========================
    st.subheader("📝 Remarks")
    st.info(data.get("remarks"))

    # =========================
    # Q&A SECTION
    # =========================
    if data.get("qa_section"):
        st.subheader("💬 Questions & Answers")

        for qa in data["qa_section"]:
            st.markdown(f"**Q:** {qa.get('question')}")
            st.write(f"**A:** {qa.get('answer')}")
            st.info(qa.get("justification"))
            st.markdown("---")

    # =========================
    # ASK QUESTION
    # =========================
    question = st.text_input("Ask a question")

    if st.button("Ask"):

        if not question.strip():
            st.warning("Enter a question")
            st.stop()

        # 🔥 REUSE PREVIOUS FILES + GUIDELINE
        files = [
            ("files", (file.name, file.getvalue(), "application/pdf"))
            for file in uploaded_files
        ]

        res = requests.post(
            f"{API_BASE}/audit",
            data={
                "question": question,
                "guideline": selected_guideline,
                "session_id": st.session_state.get("session_id")  # 🔥 ADD THIS
            },
            headers=headers
        )

        qa = res.json()
        st.write("DEBUG QA RESPONSE:", qa)

        if qa.get("mode") == "qa":

            if "qa_section" not in st.session_state["report"]:
                st.session_state["report"]["qa_section"] = []

            # 🔥 HANDLE BOTH FORMATS

            if qa.get("qa_section"):
                # normal case
                for item in qa["qa_section"]:
                    st.session_state["report"]["qa_section"].append(item)

            else:
                # 🔥 single QA response (your current case)
                st.session_state["report"]["qa_section"].append({
                    "question": qa.get("question"),
                    "answer": qa.get("answer"),
                    "justification": qa.get("justification")
                })

            st.rerun()

    # =========================
    # PDF
    # =========================
    if st.button("Download PDF"):
        res = requests.post(f"{API_BASE}/generate-pdf", json=st.session_state["report"])
        st.download_button("Download", res.content, "audit_report.pdf")