import base64
import re
import streamlit as st
import os
import time
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
        background:
            radial-gradient(ellipse 80% 60% at 10% 20%, rgba(56, 189, 248, 0.18) 0%, transparent 55%),
            radial-gradient(ellipse 70% 50% at 90% 80%, rgba(37, 99, 235, 0.12) 0%, transparent 50%),
            linear-gradient(160deg, #0f172a 0%, #1e293b 35%, #f1f5f9 35%, #f8fafc 100%) !important;
    }
    [data-testid="stAppViewContainer"] .block-container {
        max-width: min(420px, calc(100vw - 2rem)) !important;
        width: 100% !important;
        padding: 2.25rem 1.75rem 2.5rem !important;
        margin-top: 5vh !important;
        margin-left: auto !important;
        margin-right: auto !important;
        background: #ffffff !important;
        border-radius: 20px !important;
        box-shadow:
            0 25px 50px -12px rgba(15, 23, 42, 0.22),
            0 0 0 1px rgba(255, 255, 255, 0.8) inset !important;
        border: 1px solid #e2e8f0 !important;
    }
    [data-testid="stAppViewContainer"] .block-container h1,
    [data-testid="stAppViewContainer"] .block-container h3 {
        color: #0f172a !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }
    [data-testid="stAppViewContainer"] .block-container label,
    [data-testid="stAppViewContainer"] .block-container [data-testid="stWidgetLabel"] p {
        color: #334155 !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
    }
    [data-testid="stAppViewContainer"] .block-container div[data-baseweb="input"] {
        background: #f8fafc !important;
        border-radius: 10px !important;
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
        max-width: 100% !important;
    }
    [data-testid="stAppViewContainer"] .block-container div[data-baseweb="input"]:focus-within {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18) !important;
    }
    [data-testid="stAppViewContainer"] .block-container div[data-baseweb="input"] input {
        background: transparent !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        caret-color: #0f172a !important;
    }
    [data-testid="stAppViewContainer"] .block-container .stButton > button,
    [data-testid="stAppViewContainer"] .block-container .stButton button,
    [data-testid="stAppViewContainer"] .block-container button[kind],
    [data-testid="stAppViewContainer"] .block-container button[data-testid="baseButton-secondary"] {
        width: 100% !important;
        height: 48px !important;
        border-radius: 12px !important;
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%) !important;
        color: #ffffff !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        border: none !important;
        box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35) !important;
        margin-top: 0.5rem !important;
    }
    [data-testid="stAppViewContainer"] .block-container .stButton > button:hover,
    [data-testid="stAppViewContainer"] .block-container .stButton button:hover {
        filter: brightness(1.05) !important;
        box-shadow: 0 6px 20px rgba(37, 99, 235, 0.4) !important;
    }
    [data-testid="stAppViewContainer"] .block-container [data-testid="stImage"] { margin-bottom: 0.5rem; }
    .gwx-login-subtitle { text-align: center; color: #475569; font-size: 0.92rem; margin: 0 0 1.25rem 0; }
</style>
"""


def _handle_api_response(response, action_label="Request"):
    """Parse API response; show clear message for nginx 504 HTML errors."""
    if response.status_code == 401:
        force_logout_and_relogin("Session expired. Please login again.")

    content_type = (response.headers.get("content-type") or "").lower()
    is_json = "application/json" in content_type

    if response.status_code == 504 or (
        not is_json and response.status_code >= 500
    ):
        st.error(
            f"{action_label} timed out (HTTP {response.status_code}). "
            "The server took too long processing your documents — this often happens on "
            "**new guidelines** (first-time index build) or **large/scanned PDFs** (OCR + image analysis). "
            "Ask your admin to increase nginx `proxy_read_timeout` (see `deploy/nginx-api.conf`) "
            "and redeploy the latest backend optimizations."
        )
        if not is_json and response.text:
            with st.expander("Raw server response"):
                st.code(response.text[:2000])
        st.stop()

    try:
        result = response.json() if response.text else {}
    except Exception:
        st.error(f"{action_label} failed — server returned a non-JSON response.")
        if response.text:
            with st.expander("Raw server response"):
                st.code(response.text[:2000])
        st.stop()

    if response.status_code != 200:
        st.error(f"{action_label} failed ({response.status_code})")
        st.write(result.get("detail") or result.get("error") or response.text)
        st.stop()

    return result


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
        "<p class='gwx-login-subtitle'>Clinical compliance auditor — sign in to continue</p>",
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
            },
            timeout=30,
        )

        if response.status_code != 200:
            try:
                err = response.json()
                detail = err.get("detail", response.text)
            except Exception:
                detail = response.text
            st.error(detail if isinstance(detail, str) else "Login failed")
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
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');
    :root {
        --gwx-bg: #f1f5f9;
        --gwx-bg-soft: #e2e8f0;
        --gwx-surface: #ffffff;
        --gwx-border: #e2e8f0;
        --gwx-text: #0f172a;
        --gwx-text-soft: #475569;
        --gwx-text-muted: #64748b;
        --gwx-primary: #2563eb;
        --gwx-primary-2: #3b82f6;
        --gwx-accent: #38bdf8;
        --gwx-sidebar-from: #0f172a;
        --gwx-sidebar-to: #1e293b;
        --gwx-sidebar-text: #f8fafc;
        --gwx-sidebar-muted: #94a3b8;
        --gwx-success-bg: #ecfdf5;
        --gwx-success-text: #047857;
        --gwx-info-bg: #eff6ff;
        --gwx-info-text: #1d4ed8;
        --gwx-warning-bg: #fffbeb;
        --gwx-warning-text: #b45309;
    }
    html, body, [class*="css"] {
        font-family: 'DM Sans', 'Segoe UI', sans-serif;
        color: var(--gwx-text) !important;
    }
    html, body { color-scheme: light; }

    /* ── Main canvas ── */
    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(ellipse 60% 40% at 100% 0%, rgba(56, 189, 248, 0.08) 0%, transparent 60%),
            linear-gradient(165deg, var(--gwx-bg) 0%, #f8fafc 50%, #ffffff 100%);
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--gwx-sidebar-from) 0%, var(--gwx-sidebar-to) 100%) !important;
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }
    [data-testid="stSidebar"] > div:first-child {
        background: transparent !important;
    }
    .gwx-sidebar-brand {
        display: flex; align-items: center; gap: 0.6rem;
        padding: 0.25rem 0 1.25rem 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 0.5rem;
    }
    .gwx-sidebar-brand-icon { font-size: 1.4rem; line-height: 1; }
    .gwx-sidebar-brand-text {
        color: var(--gwx-sidebar-text) !important;
        font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {
        color: var(--gwx-sidebar-text) !important;
    }
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown li {
        color: var(--gwx-sidebar-text) !important;
    }
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] label {
        color: var(--gwx-sidebar-text) !important;
        font-weight: 600 !important;
    }

    /* Selectbox in sidebar — light field on dark sidebar for readable selected value */
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 10px !important;
        color: var(--gwx-text) !important;
    }
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] span,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] p,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] div {
        color: var(--gwx-text) !important;
        -webkit-text-fill-color: var(--gwx-text) !important;
    }
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] svg {
        color: var(--gwx-text-soft) !important;
        fill: var(--gwx-text-soft) !important;
    }
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] input {
        color: var(--gwx-text) !important;
        -webkit-text-fill-color: var(--gwx-text) !important;
        caret-color: var(--gwx-text) !important;
        background: transparent !important;
    }

    /* File uploader in sidebar */
    [data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] label {
        color: var(--gwx-sidebar-text) !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
        background: rgba(255, 255, 255, 0.06) !important;
        border: 2px dashed rgba(255, 255, 255, 0.25) !important;
        border-radius: 12px !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] span,
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] p,
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] small,
    [data-testid="stSidebar"] [data-testid="stFileDropzoneInstructions"] div,
    [data-testid="stSidebar"] [data-testid="stFileDropzoneInstructions"] span,
    [data-testid="stSidebar"] [data-testid="stFileDropzoneInstructions"] small {
        color: var(--gwx-sidebar-muted) !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] button[data-testid="stBaseButton-secondary"],
    [data-testid="stSidebar"] [data-testid="stFileUploader"] button {
        background: linear-gradient(135deg, var(--gwx-primary-2) 0%, var(--gwx-primary) 100%) !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
        opacity: 1 !important;
        border-radius: 8px !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {
        filter: brightness(1.08) !important;
    }
    /* Uploaded file chips */
    [data-testid="stSidebar"] [data-testid="stFileChip"] {
        background: rgba(255, 255, 255, 0.12) !important;
        border: 1px solid rgba(255, 255, 255, 0.18) !important;
        border-radius: 8px !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileChipName"],
    [data-testid="stSidebar"] [data-testid="stFileChip"] span,
    [data-testid="stSidebar"] [data-testid="stFileChip"] p {
        color: var(--gwx-sidebar-text) !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileChipDeleteBtn"] {
        color: var(--gwx-accent) !important;
    }
    /* Legacy file chip selectors (older Streamlit versions) */
    [data-testid="stSidebar"] [data-testid="stFileUploaderFile"],
    [data-testid="stSidebar"] [data-testid="stFileUploaderFileName"] {
        color: var(--gwx-sidebar-text) !important;
    }

    /* Sidebar buttons */
    [data-testid="stSidebar"] .stButton > button {
        width: 100%; border-radius: 10px; font-weight: 600;
        background: linear-gradient(135deg, var(--gwx-primary-2) 0%, var(--gwx-primary) 100%);
        color: #ffffff !important; border: none;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
        transition: filter 0.15s ease, box-shadow 0.15s ease;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        filter: brightness(1.06);
        box-shadow: 0 6px 16px rgba(37, 99, 235, 0.38);
    }
    [data-testid="stSidebar"] .stButton:last-child > button {
        background: transparent !important;
        border: 1px solid rgba(255, 255, 255, 0.28) !important;
        color: var(--gwx-sidebar-text) !important;
        box-shadow: none !important;
    }
    [data-testid="stSidebar"] .stButton:last-child > button:hover {
        background: rgba(255, 255, 255, 0.08) !important;
        filter: none !important;
    }

    /* ── Main content text ── */
    [data-testid="stAppViewContainer"] .block-container p,
    [data-testid="stAppViewContainer"] .block-container li,
    [data-testid="stAppViewContainer"] .block-container label {
        color: var(--gwx-text);
    }
    [data-testid="stAppViewContainer"] [data-testid="stText"],
    [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] {
        color: var(--gwx-text-soft) !important;
    }
    [data-testid="stAppViewContainer"] hr {
        border-color: var(--gwx-border) !important;
    }
    [data-testid="stAppViewContainer"] h1,
    [data-testid="stAppViewContainer"] h2,
    [data-testid="stAppViewContainer"] h3,
    [data-testid="stAppViewContainer"] h4 {
        color: var(--gwx-text) !important;
        letter-spacing: -0.02em;
    }

    /* ── Custom components ── */
    .gwx-header-bar {
        display: flex; align-items: center; gap: 1.25rem;
        padding: 1.1rem 1.35rem; margin: 0 -1rem 1.25rem -1rem;
        background: linear-gradient(90deg, #ffffff 0%, #f8fafc 100%);
        border-radius: 14px; border: 1px solid var(--gwx-border);
        border-left: 4px solid var(--gwx-accent);
        box-shadow: 0 4px 24px rgba(15, 23, 42, 0.07);
    }
    .gwx-header-bar h2 {
        margin: 0; color: var(--gwx-text); font-size: 1.45rem;
        font-weight: 700; letter-spacing: -0.02em;
    }
    .gwx-header-bar .tagline { color: var(--gwx-text-soft); font-size: 0.9rem; margin-top: 0.2rem; }

    .gwx-card {
        background: var(--gwx-surface); border-radius: 14px; padding: 1.35rem 1.5rem;
        border: 1px solid var(--gwx-border);
        box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
        margin-bottom: 1rem;
        transition: box-shadow 0.2s ease, transform 0.2s ease;
    }
    .gwx-card:hover {
        box-shadow: 0 6px 24px rgba(15, 23, 42, 0.09);
        transform: translateY(-1px);
    }
    .gwx-card h3 { margin: 0 0 0.75rem 0; color: var(--gwx-text); font-size: 1.05rem; font-weight: 700; }
    .gwx-card-compact { padding: 12px 14px; }
    .gwx-steps ol { margin: 0; padding-left: 1.25rem; color: var(--gwx-text-soft); line-height: 1.75; }
    .gwx-steps li { margin-bottom: 0.5rem; }

    .gwx-ref-pill {
        display: inline-block; background: #1e293b; color: #ffffff !important;
        padding: 0.35rem 0.85rem; border-radius: 999px; font-size: 0.85rem;
        font-weight: 600; margin-right: 0.5rem;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .gwx-ref-pill *, .gwx-ref-pill span { color: #ffffff !important; }
    .gwx-ref-pill-accent { background: linear-gradient(135deg, var(--gwx-primary-2), var(--gwx-primary)); }

    .gwx-section-title {
        color: var(--gwx-text); font-weight: 700; font-size: 1.1rem;
        margin: 1.25rem 0 0.75rem 0; padding-bottom: 0.35rem;
        border-bottom: 2px solid var(--gwx-primary-2); display: inline-block;
    }

    .gwx-text-muted { color: var(--gwx-text-muted) !important; }
    .gwx-text-soft { color: var(--gwx-text-soft) !important; }
    .gwx-field-title { color: var(--gwx-text); font-weight: 700; }
    .gwx-row { margin: 0.25rem 0; }
    .gwx-row-tight { margin: 0.2rem 0; }
    .gwx-comment { color: var(--gwx-text-muted); font-style: italic; }

    /* Metrics */
    div[data-testid="column"] .stMetric {
        background: linear-gradient(135deg, #f8fafc 0%, #ffffff 100%);
        padding: 0.85rem 1rem;
        border-radius: 12px;
        border: 1px solid var(--gwx-border);
        box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
    }
    div[data-testid="column"] .stMetric label {
        color: var(--gwx-text-soft) !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
    }
    div[data-testid="column"] .stMetric [data-testid="stMetricValue"] {
        color: var(--gwx-text) !important;
        font-weight: 700 !important;
    }

    /* Inputs and text areas */
    [data-testid="stAppViewContainer"] div[data-baseweb="input"],
    [data-testid="stAppViewContainer"] div[data-baseweb="textarea"],
    [data-testid="stAppViewContainer"] div[data-baseweb="select"] > div {
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 10px !important;
    }
    [data-testid="stAppViewContainer"] div[data-baseweb="input"] input,
    [data-testid="stAppViewContainer"] div[data-baseweb="textarea"] textarea,
    [data-testid="stAppViewContainer"] div[data-baseweb="select"] span {
        color: var(--gwx-text) !important;
        -webkit-text-fill-color: var(--gwx-text) !important;
    }
    [data-testid="stAppViewContainer"] div[data-baseweb="input"] input {
        caret-color: var(--gwx-text) !important;
        background: transparent !important;
    }
    [data-testid="stAppViewContainer"] div[data-baseweb="input"]:focus-within,
    [data-testid="stAppViewContainer"] div[data-baseweb="textarea"]:focus-within,
    [data-testid="stAppViewContainer"] div[data-baseweb="select"]:focus-within > div {
        border-color: var(--gwx-primary) !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14) !important;
    }

    /* Main buttons */
    [data-testid="stAppViewContainer"] .stButton > button {
        border-radius: 10px !important;
        border: none !important;
        color: #ffffff !important;
        background: linear-gradient(135deg, var(--gwx-primary-2) 0%, var(--gwx-primary) 100%) !important;
        box-shadow: 0 3px 10px rgba(37, 99, 235, 0.28) !important;
        font-weight: 600 !important;
        transition: filter 0.15s ease !important;
    }
    [data-testid="stAppViewContainer"] .stButton > button:hover {
        filter: brightness(1.05) !important;
    }

    /* Status boxes */
    [data-testid="stSuccess"] {
        background: var(--gwx-success-bg) !important;
        border: 1px solid #6ee7b7 !important;
        border-radius: 10px !important;
    }
    [data-testid="stInfo"] {
        background: var(--gwx-info-bg) !important;
        border: 1px solid #93c5fd !important;
        border-radius: 10px !important;
    }
    [data-testid="stWarning"] {
        background: var(--gwx-warning-bg) !important;
        border: 1px solid #fcd34d !important;
        border-radius: 10px !important;
    }
    [data-testid="stSuccess"] * { color: var(--gwx-success-text) !important; }
    [data-testid="stInfo"] * { color: var(--gwx-info-text) !important; }
    [data-testid="stWarning"] * { color: var(--gwx-warning-text) !important; }
    [data-testid="stException"], [data-testid="stError"] {
        background: #fef2f2 !important;
        border: 1px solid #fca5a5 !important;
        border-radius: 10px !important;
    }
    [data-testid="stException"] *, [data-testid="stError"] * {
        color: #b91c1c !important;
    }
</style>
"""
st.markdown(PAGE_CSS, unsafe_allow_html=True)

# =========================
# SIDEBAR
# =========================
st.sidebar.markdown(
    '<div class="gwx-sidebar-brand">'
    '<span class="gwx-sidebar-brand-icon">🧠</span>'
    '<span class="gwx-sidebar-brand-text">Medical Auditor</span>'
    '</div>',
    unsafe_allow_html=True,
)

# ✅ GUIDELINE DROPDOWN
def load_guidelines(force_refresh=False):
    try:
        params = {"refresh": "true"} if force_refresh else {}
        resp = requests.get(f"{API_BASE}/guidelines", params=params, timeout=30)
        if resp.status_code == 200:
            payload = resp.json() or {}
            items = payload.get("guidelines") or []
            return [g for g in items if isinstance(g, str) and g.strip()]
    except Exception:
        pass
    if os.path.isdir(GUIDELINE_PATH):
        return [f for f in os.listdir(GUIDELINE_PATH) if f.lower().endswith(".pdf")]
    return []


if "guidelines_list" not in st.session_state:
    st.session_state["guidelines_list"] = load_guidelines()

gcol1, gcol2 = st.sidebar.columns([3, 1])
with gcol2:
    if st.button("↻", help="Refresh guidelines from S3"):
        st.session_state["guidelines_list"] = load_guidelines(force_refresh=True)
        st.rerun()

guidelines = st.session_state["guidelines_list"]
if not guidelines:
    st.sidebar.warning("No guidelines loaded. Click ↻ to retry.")

selected_guideline = gcol1.selectbox(
    "📘 Select Guideline",
    ["-- Select --"] + sorted(guidelines),
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
            <p style="margin:0;line-height:1.7;" class="gwx-text-soft">
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

    progress_bar = st.progress(0, text="Starting audit…")
    status_line = st.empty()

    files = [
        ("files", (file.name, file.getvalue(), "application/pdf"))
        for file in uploaded_files
    ]

    try:
        response = requests.post(
            API_URL,
            files=files,
            data={"guideline": selected_guideline},
            headers=headers,
            timeout=600,
        )
    except requests.exceptions.Timeout:
        st.error("Upload timed out. Try fewer or smaller PDF files.")
        st.stop()

    if response.status_code == 202:
        try:
            payload = response.json()
        except Exception:
            st.error("Server returned an invalid job response.")
            st.stop()
        job_id = payload.get("job_id")
        if not job_id:
            st.error("Audit job was not created.")
            st.stop()

        result = None
        transient_errors = 0
        max_transient = 30  # ~60s of retries while backend restarts under OCR load
        while True:
            time.sleep(2)
            try:
                poll = requests.get(
                    f"{API_BASE}/audit/status/{job_id}",
                    headers=headers,
                    timeout=60,
                )
            except requests.exceptions.RequestException as exc:
                status_line.warning(f"Connection blip — retrying… ({exc})")
                continue

            if poll.status_code == 401:
                force_logout_and_relogin("Session expired. Please login again.")

            if poll.status_code in (502, 503, 504):
                transient_errors += 1
                if transient_errors > max_transient:
                    st.error(
                        f"Server unavailable ({poll.status_code}) while processing PDFs. "
                        "The backend may have run out of memory — try fewer/smaller files."
                    )
                    st.stop()
                status_line.warning(
                    f"Server busy processing PDFs ({poll.status_code}) — retrying… "
                    f"({transient_errors}/{max_transient})"
                )
                continue

            transient_errors = 0

            if poll.status_code != 200:
                st.error(f"Status check failed ({poll.status_code})")
                st.stop()

            data = poll.json()
            pct = max(0, min(100, int(data.get("progress") or 0)))
            phase = data.get("phase") or data.get("status") or "working"
            msg = data.get("message") or phase
            progress_bar.progress(pct / 100, text=f"{msg} ({pct}%)")

            if data.get("status") == "completed":
                result = data.get("result")
                if isinstance(result, dict) and result.get("error"):
                    st.error(f"Audit failed: {result.get('error')}")
                    st.stop()
                break
            if data.get("status") == "failed":
                st.error(data.get("error") or data.get("message") or "Audit failed")
                st.stop()

        progress_bar.progress(1.0, text="Audit complete")
    else:
        result = _handle_api_response(response, "Audit")

    if isinstance(result, dict) and (result.get("error") or result.get("detail")):
        st.error("Audit did not return a valid report")
        st.write(result.get("detail") or result.get("error"))
        st.stop()

    st.session_state["report"] = result
    st.session_state.pop("pdf_blob", None)
    st.session_state.pop("pdf_name", None)
    st.session_state["session_id"] = result.get("session_id")
    st.session_state["audit_meta"] = {
        "audit_id": f"GMS-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
        "audit_date": datetime.now().strftime("%d/%m/%Y"),
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
    for _k in (
        "hospital",
        "consultation_date",
        "date_of_admission",
        "date_of_discharge",
        "nature_of_admission",
        "procedure_or_surgery",
        "diagnosis",
    ):
        data["claim_details"].setdefault(_k, "")
    data.setdefault("clinical_checklist", [])
    data.setdefault("auditor_observation_summary", "")
    data.setdefault("treatment_billing_audit", {})
    for _k in (
        "room_category_admitted",
        "room_category_eligible",
        "procedures_performed",
        "cross_checked_with_preauth",
        "excluded_items_billed",
        "charges_appropriate",
    ):
        data["treatment_billing_audit"].setdefault(_k, "")
    data.setdefault("financial_review", {})
    for _k in (
        "total_hospital_bill",
        "non_payable_amount",
        "net_claimable_amount",
        "recommended_approval_amount",
        "patient_liability",
    ):
        data["financial_review"].setdefault(_k, "")

    st.markdown('<p class="gwx-section-title" style="margin-top:0">Medical audit report</p>', unsafe_allow_html=True)

    c_act, c_edit, c_pdf = st.columns([2, 1, 1])
    with c_act:
        st.markdown(
            f"<span class='gwx-ref-pill'>Ref: {meta.get('audit_id', '-')}</span>"
            f"<span class='gwx-ref-pill gwx-ref-pill-accent'>Date: {meta.get('audit_date', '-')}</span>",
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
            cc3, cc4 = st.columns(2)
            consult_date = cc3.text_input("Consult date", value=str(data["claim_details"].get("consultation_date") or ""))
            admission_date = cc4.text_input("Date of admission", value=str(data["claim_details"].get("date_of_admission") or ""))
            cc5, cc6 = st.columns(2)
            discharge_date = cc5.text_input("Date of discharge", value=str(data["claim_details"].get("date_of_discharge") or ""))
            admission_nature = cc6.text_input("Nature of admission", value=str(data["claim_details"].get("nature_of_admission") or ""))
            procedure_done = st.text_input("Procedure / surgery done", value=str(data["claim_details"].get("procedure_or_surgery") or ""))

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
                data["claim_details"]["consultation_date"] = consult_date
                data["claim_details"]["date_of_admission"] = admission_date
                data["claim_details"]["date_of_discharge"] = discharge_date
                data["claim_details"]["nature_of_admission"] = admission_nature
                data["claim_details"]["procedure_or_surgery"] = procedure_done
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
        <p class="gwx-row"><strong>Insurance company:</strong> {ins.get('insurance_company') or '—'}</p>
        <p class="gwx-row"><strong>Policy number:</strong> {ins.get('policy_number') or '—'}</p>
        <p class="gwx-row"><strong>Policy period:</strong> {ins.get('policy_period') or '—'}</p>
        <p class="gwx-row"><strong>Claim / incident number:</strong> {ins.get('claim_incident_number') or '—'}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c = data["claim_details"]
    st.markdown('<p class="gwx-section-title">Claim details</p>', unsafe_allow_html=True)
    st.markdown(
        f"<div class='gwx-card'><p class='gwx-row'><strong>Hospital:</strong> {c.get('hospital') or '—'}</p>"
        f"<p class='gwx-row'><strong>Consult date:</strong> {c.get('consultation_date') or '—'}</p>"
        f"<p class='gwx-row'><strong>Date of admission:</strong> {c.get('date_of_admission') or '—'}</p>"
        f"<p class='gwx-row'><strong>Date of discharge:</strong> {c.get('date_of_discharge') or '—'}</p>"
        f"<p class='gwx-row'><strong>Nature of admission:</strong> {c.get('nature_of_admission') or '—'}</p>"
        f"<p class='gwx-row'><strong>Procedure / surgery done:</strong> {c.get('procedure_or_surgery') or '—'}</p>"
        f"<p class='gwx-row'><strong>Diagnosis:</strong> {c.get('diagnosis') or '—'}</p></div>",
        unsafe_allow_html=True,
    )

    verdict = (data.get("compliance_verdict") or "").strip()
    if verdict:
        v_lower = verdict.lower()
        if "non" in v_lower or "not" in v_lower:
            st.error(f"**Compliance verdict:** {verdict}")
        elif "partial" in v_lower:
            st.warning(f"**Compliance verdict:** {verdict}")
        elif "compliant" in v_lower and "partial" not in v_lower:
            st.success(f"**Compliance verdict:** {verdict}")
        else:
            st.info(f"**Compliance verdict:** {verdict}")

    deviations = data.get("guideline_deviations") or []
    if deviations:
        st.markdown('<p class="gwx-section-title">Guideline deviations</p>', unsafe_allow_html=True)
        for dev in deviations:
            if isinstance(dev, dict):
                sev = dev.get("severity") or "—"
                st.markdown(
                    f"""
                    <div class="gwx-card" style="border-left:4px solid #dc2626;">
                    <p class="gwx-row-tight"><strong>{dev.get('issue') or 'Deviation'}</strong>
                    <span class="gwx-ref-pill">{sev}</span></p>
                    <p class="gwx-row-tight"><strong>Guideline expects:</strong> {dev.get('guideline_expectation') or '—'}</p>
                    <p class="gwx-row-tight"><strong>Case evidence:</strong> {dev.get('case_evidence') or '—'}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            elif dev:
                st.warning(str(dev))

    challenges = data.get("challenge_points") or []
    if challenges:
        st.markdown('<p class="gwx-section-title">Points the hospital must justify</p>', unsafe_allow_html=True)
        for pt in challenges:
            st.markdown(f"- **Challenge:** {pt}")

    if data.get("imaging_findings"):
        st.markdown('<p class="gwx-section-title">Imaging findings</p>', unsafe_allow_html=True)
        for img in data["imaging_findings"]:
            st.markdown(
                f"""
                <div class="gwx-card">
                <p class="gwx-row-tight"><strong>Type:</strong> {img.get('type')}</p>
                <p class="gwx-row-tight"><strong>Finding:</strong> {img.get('finding')}</p>
                <p class="gwx-row-tight"><strong>Clinical correlation:</strong> {img.get('clinical_correlation')}</p>
                <p class="gwx-row-tight"><strong>Consistency:</strong> {img.get('consistency_with_diagnosis')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown('<p class="gwx-section-title">Clinical findings</p>', unsafe_allow_html=True)
    for item in data.get("clinical_findings", []):
        st.markdown(
            f"""
            <div class="gwx-card gwx-card-compact">
            <b class="gwx-field-title">{item.get('parameter')}</b><br>
            <span class="gwx-text-soft">Value:</span> {item.get('value')}<br>
            <i class="gwx-comment">{item.get('comment')}</i>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<p class="gwx-section-title">Documentation checklist</p>', unsafe_allow_html=True)
    checklist = data.get("clinical_checklist") or []
    if checklist:
        for row in checklist:
            st.markdown(
                f"""
                <div class="gwx-card gwx-card-compact">
                <b class="gwx-field-title">{row.get('area') or 'Area'}</b><br>
                <span class="gwx-text-soft">Available:</span> {row.get('available') or '—'}<br>
                <i class="gwx-comment">{row.get('remarks') or ''}</i>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("No checklist details available.")

    st.markdown('<p class="gwx-section-title">Documentation gaps</p>', unsafe_allow_html=True)
    for gap in data.get("documentation_gaps", []):
        st.warning(gap)

    tba = data.get("treatment_billing_audit") or {}
    st.markdown('<p class="gwx-section-title">Treatment & billing audit</p>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class='gwx-card'>
        <p class='gwx-row'><strong>Room category admitted:</strong> {tba.get('room_category_admitted') or '—'}</p>
        <p class='gwx-row'><strong>Room category eligible (policy):</strong> {tba.get('room_category_eligible') or '—'}</p>
        <p class='gwx-row'><strong>Procedures performed:</strong> {tba.get('procedures_performed') or '—'}</p>
        <p class='gwx-row'><strong>Cross-checked with pre-auth:</strong> {tba.get('cross_checked_with_preauth') or '—'}</p>
        <p class='gwx-row'><strong>Excluded items billed:</strong> {tba.get('excluded_items_billed') or '—'}</p>
        <p class='gwx-row'><strong>Charges appropriate:</strong> {tba.get('charges_appropriate') or '—'}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fin = data.get("financial_review") or {}
    st.markdown('<p class="gwx-section-title">Financial review</p>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class='gwx-card'>
        <p class='gwx-row'><strong>Total hospital bill:</strong> {fin.get('total_hospital_bill') or '—'}</p>
        <p class='gwx-row'><strong>Non-payable amount:</strong> {fin.get('non_payable_amount') or '—'}</p>
        <p class='gwx-row'><strong>Net claimable amount:</strong> {fin.get('net_claimable_amount') or '—'}</p>
        <p class='gwx-row'><strong>Recommended approval amount:</strong> {fin.get('recommended_approval_amount') or '—'}</p>
        <p class='gwx-row'><strong>Patient liability:</strong> {fin.get('patient_liability') or '—'}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<p class="gwx-section-title">Timeline</p>', unsafe_allow_html=True)
    for t in data.get("timeline", []):
        st.markdown(f"• **{t.get('date')}** → {t.get('event')}")

    st.markdown('<p class="gwx-section-title">Auditor\'s observations (detailed)</p>', unsafe_allow_html=True)
    if (data.get("auditor_observation_summary") or "").strip():
        st.markdown(
            f"""
            <div class="gwx-card">
            <p class="gwx-row-tight"><strong>Overall narrative:</strong></p>
            <p class="gwx-row-tight" style="white-space:pre-line">{data.get('auditor_observation_summary')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    for idx, obs in enumerate(data.get("observations", []), start=1):
        answer = str(obs.get("answer") or "").strip()
        ans_lower = answer.lower()
        if "not supported" in ans_lower:
            ans_style = "color:#b91c1c;font-weight:600;"
        elif "partial" in ans_lower or "insufficient" in ans_lower:
            ans_style = "color:#b45309;font-weight:600;"
        elif "supported" in ans_lower:
            ans_style = "color:#047857;font-weight:600;"
        else:
            ans_style = ""
        st.markdown(
            f"""
            <div class="gwx-card">
            <p class="gwx-row-tight"><strong>Q{idx}:</strong> {obs.get('question')}</p>
            <p class="gwx-row-tight"><strong>Analysis:</strong> {obs.get('analysis')}</p>
            <p class="gwx-row-tight"><strong>Answer:</strong> <span style="{ans_style}">{answer or '—'}</span></p>
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