import base64
import streamlit as st
import os
import json
from datetime import datetime
import uuid
import requests
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# =========================
# PATHS
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")

# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title="Glowix Medical Compliance Auditor",
    page_icon="🧠",
    layout="wide"
)

API_BASE = "http://localhost:8000"
API_URL = f"{API_BASE}/audit"

# =========================
# LOGIN PAGE
# =========================
def login_page():

    st.markdown("""
    <style>
    .block-container {
        max-width: 420px;
        padding-top: 60px;
    }

    .custom-label {
        margin-bottom: 4px;
        font-size: 15px;
        font-weight: 500;
    }

    div[data-baseweb="input"] > div {
        height: 42px;
        border-radius: 8px;
    }

    .stButton > button {
        width: 100%;
        height: 45px;
        border-radius: 10px;
        background-color: #2f63d6;
        color: white;
        font-weight: 600;
        border: none;
    }

    .stButton > button:hover {
        background-color: #1e40af;
    }
    </style>
    """, unsafe_allow_html=True)

    # Logo
    if os.path.exists(LOGO_PATH):
        st.markdown(
            f"""
            <div style="text-align: center;">
                <img src="data:image/png;base64,{base64.b64encode(open(LOGO_PATH, "rb").read()).decode()}" width="120">
            </div>
            """,
            unsafe_allow_html=True
        )

    st.markdown(
        "<h3 style='text-align:center;'>Glowix Medical Services Pvt. Ltd.</h3>",
        unsafe_allow_html=True
    )

    st.markdown("<div class='custom-label'>Email</div>", unsafe_allow_html=True)
    email = st.text_input("", placeholder="Enter your email", label_visibility="collapsed")

    st.markdown("<div class='custom-label'>Password</div>", unsafe_allow_html=True)
    password = st.text_input("", type="password", placeholder="Enter your password", label_visibility="collapsed")

    st.markdown("<div style='margin-top:15px'></div>", unsafe_allow_html=True)

    if st.button("Login"):
        try:
            response = requests.post(
                f"{API_BASE}/login",
                data={"email": email, "password": password}
            )

            data = response.json()

            if "access_token" in data:
                st.session_state["token"] = data["access_token"]
                st.success("Login successful")
                st.rerun()
            else:
                st.error("Invalid credentials")

        except:
            st.error("Backend not running")

# =========================
# LOGIN CHECK
# =========================
if "token" not in st.session_state:
    login_page()
    st.stop()

# =========================
# LOAD HISTORY (AFTER LOGIN)
# =========================
headers = {
    "Authorization": f"Bearer {st.session_state['token']}"
}

history_data = []

try:
    response = requests.get(f"{API_BASE}/history", headers=headers)

    if response.status_code == 200:
        history_data = response.json()

except:
    st.sidebar.error("Unable to load history")

# =========================
# SIDEBAR
# =========================
st.sidebar.title("🧠 Medical Auditor")

options = ["-- Select --"] + [
    f"{item['created_at']} (ID: {item['id']})"
    for item in history_data
]

selected = st.sidebar.selectbox("📁 Past Audits", options)

if selected != "-- Select --":
    index = options.index(selected) - 1
    st.session_state["report"] = history_data[index]["report"]

uploaded_files = st.sidebar.file_uploader(
    "📂 Upload Case Documents",
    accept_multiple_files=True
)

run = st.sidebar.button("🚀 Run Audit")

if st.sidebar.button("Logout"):
    st.session_state.clear()
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

    if not uploaded_files:
        st.error("Upload case documents")
        st.stop()

    with st.spinner("Running Audit..."):

        try:
            files = [
                ("files", (file.name, file.getvalue(), "application/pdf"))
                for file in uploaded_files
            ]

            response = requests.post(API_URL, files=files, headers=headers)

            if response.status_code != 200:
                st.error("Backend error")
                st.stop()

            result = response.json()

        except Exception as e:
            st.error(f"Error: {e}")
            st.stop()

        st.session_state["report"] = result

        st.session_state["audit_meta"] = {
            "audit_id": f"GMS-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
            "audit_date": datetime.now().strftime("%d/%m/%Y")
        }

    st.success("Audit Completed")

# =========================
# DISPLAY REPORT
# =========================
if "report" in st.session_state:

    data = st.session_state["report"]
    meta = st.session_state.get("audit_meta", {})

    st.markdown("## 📄 Medical Audit Report")

    st.markdown(f"""
    ### 📘 Guideline Referenced  
    **{data.get('guideline_used', 'Not specified')}**
    """)

    st.markdown(f"""
    **Ref No:** {meta.get('audit_id', '-')}  
    **Date:** {meta.get('audit_date', '-')}
    """)

    st.subheader("Patient Details")
    p = data.get("patient_details", {})

    col1, col2, col3 = st.columns(3)
    col1.write(p.get('name','-'))
    col2.write(p.get('age','-'))
    col3.write(p.get('sex','-'))

    st.subheader("Clinical Findings")

    for item in data.get("clinical_findings", []):
        st.markdown(f"""
        <div style='padding:10px;background:#f5f5f5;border-radius:10px;margin-bottom:10px'>
        <b>{item.get('parameter')}</b>: {item.get('value')}<br>
        {item.get('comment')}
        </div>
        """, unsafe_allow_html=True)

    st.subheader("Conclusion")
    st.success(data.get("auditor_conclusion"))

    # =========================
    # PDF DOWNLOAD
    # =========================
    if st.button("Generate PDF"):
        response = requests.post(f"{API_BASE}/generate-pdf", json=data)

        if response.status_code == 200:
            st.download_button(
                "Download PDF",
                response.content,
                "audit_report.pdf"
            )
        else:
            st.error("PDF generation failed")