"""Extract and validate doctor medical registration numbers from case documents."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

# Common Indian medical registration patterns (MCI / NMC / state councils).
_REG_PATTERNS = [
    re.compile(
        r"(?:reg(?:istration)?\.?\s*(?:no|number|#)?|mci\s*(?:no|reg)?|"
        r"nmc\s*(?:no|reg)?|state\s*medical\s*council\s*(?:no|reg)?|"
        r"regn\.?\s*(?:no)?)\s*[:.]?\s*([A-Z]{0,4}[-/]?\d{3,8}(?:[-/][A-Z0-9]{1,6})?)",
        re.I,
    ),
    re.compile(
        r"\b((?:MH|KA|TN|DL|UP|GJ|RJ|WB|AP|TS|KL|PB|HR|MP|CG|OR|BR|JH|AS|UK|HP|GA|JK)"
        r"[-/]?\d{3,6}(?:[-/]\d{2,4})?)\b",
        re.I,
    ),
    re.compile(r"\b(MCI[-/]?\d{4,8})\b", re.I),
    re.compile(r"\b(NMC[-/]?\d{4,8})\b", re.I),
]

_DOCTOR_NAME_PATTERNS = [
    re.compile(
        r"(?:dr\.?|doctor)\s+([A-Z][A-Za-z.'-]{1,30}(?:\s+[A-Z][A-Za-z.'-]{1,30}){0,3})",
    ),
    re.compile(
        r"(?:treating\s+(?:doctor|surgeon|consultant)|consultant\s*name|"
        r"surgeon\s*name)\s*[:.]?\s*(?:dr\.?\s*)?([A-Za-z.'\-\s]{4,50})",
        re.I,
    ),
]

_FALSE_REG = {
    "date", "page", "room", "bed", "icu", "ward", "bill", "policy", "claim",
}


def _clean_reg(val: str) -> str:
    return re.sub(r"\s+", "", (val or "").strip().upper())


def _is_plausible_reg(val: str) -> bool:
    v = _clean_reg(val)
    if len(v) < 4 or len(v) > 20:
        return False
    if v.lower() in _FALSE_REG:
        return False
    if not re.search(r"\d{3,}", v):
        return False
    if re.search(r"[|\[\]{}\\]", v):
        return False
    return True


def extract_doctor_registrations(case_text: str, limit: int = 5) -> List[Dict[str, str]]:
    """Pull doctor names and registration numbers from transcribed case text."""
    text = case_text or ""
    regs: List[str] = []
    seen = set()
    for pat in _REG_PATTERNS:
        for m in pat.finditer(text):
            val = _clean_reg(m.group(1))
            if _is_plausible_reg(val) and val not in seen:
                seen.add(val)
                regs.append(val)

    names: List[str] = []
    name_seen = set()
    for pat in _DOCTOR_NAME_PATTERNS:
        for m in pat.finditer(text):
            name = re.sub(r"\s+", " ", m.group(1).strip(" .,"))
            key = name.lower()
            if len(name) >= 4 and key not in name_seen and "hospital" not in key:
                name_seen.add(key)
                names.append(name)

    out: List[Dict[str, str]] = []
    for i, reg in enumerate(regs[:limit]):
        out.append({
            "doctor_name": names[i] if i < len(names) else (names[0] if names else ""),
            "registration_number": reg,
            "source": "case documents",
        })
    if not out and names:
        out.append({
            "doctor_name": names[0],
            "registration_number": "",
            "source": "case documents",
        })
    return out


def _check_nmc_directory(registration_number: str, doctor_name: str = "") -> Dict[str, Any]:
    """
    Best-effort public NMC doctor search.
    NMC does not expose a stable public API; we probe the public search page and
    treat network/HTML failures as 'unverified' rather than 'not registered'.
    """
    reg = _clean_reg(registration_number)
    if not reg:
        return {
            "status": "missing",
            "verified": False,
            "message": "No registration number found in uploaded documents.",
            "nmc_check": "not_attempted",
            "state_council_check": "not_attempted",
        }

    # Format-level validation always runs.
    format_ok = bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-/]{3,18}", reg))

    nmc_status = "unverified"
    nmc_message = ""
    try:
        import requests
        # Public NMC doctor search landing — presence of registration in response
        # is a weak signal only; never claim definitive registration without API.
        url = (
            "https://www.nmc.org.in/information-desk/indian-medical-register/"
        )
        resp = requests.get(url, timeout=8, headers={"User-Agent": "GlowixMedicalAuditor/1.0"})
        if resp.status_code == 200:
            nmc_status = "directory_reachable"
            nmc_message = (
                "NMC public directory is reachable. Manual verification recommended "
                f"for registration {reg}"
                + (f" ({doctor_name})" if doctor_name else "")
                + "."
            )
        else:
            nmc_status = "directory_unreachable"
            nmc_message = f"NMC directory returned HTTP {resp.status_code}."
    except Exception as exc:
        nmc_status = "directory_unreachable"
        nmc_message = f"Could not reach NMC directory ({exc.__class__.__name__})."

    if not format_ok:
        return {
            "status": "invalid_format",
            "verified": False,
            "flagged": True,
            "message": (
                f"Registration number '{reg}' does not match expected Indian medical "
                "council format. Flag for manual verification."
            ),
            "nmc_check": nmc_status,
            "state_council_check": "not_attempted",
            "nmc_message": nmc_message,
            "nmc_search_url": "https://www.nmc.org.in/information-desk/indian-medical-register/",
            "state_council_search_url": "https://www.nmc.org.in/information-desk/indian-medical-register/",
        }

    return {
        "status": "format_valid_unverified_online",
        "verified": False,
        "flagged": True,
        "message": (
            f"Registration number '{reg}' has a valid format but could not be "
            "auto-confirmed on NMC / state medical council websites. "
            "Please verify manually on the NMC Indian Medical Register."
        ),
        "nmc_check": nmc_status,
        "state_council_check": "manual_required",
        "nmc_message": nmc_message,
        "nmc_search_url": "https://www.nmc.org.in/information-desk/indian-medical-register/",
        "state_council_search_url": "https://www.nmc.org.in/information-desk/indian-medical-register/",
        "search_hint": quote(reg),
    }


def validate_doctor_registrations(case_text: str) -> Dict[str, Any]:
    """Build doctor_validation section for the audit report."""
    doctors = extract_doctor_registrations(case_text)
    if not doctors:
        return {
            "doctors": [],
            "overall_status": "not_found",
            "flagged": True,
            "summary": (
                "Doctor registration number not found in uploaded documents. "
                "Flag for hospital to provide treating doctor's registration details."
            ),
        }

    validated = []
    any_flagged = False
    for doc in doctors:
        check = _check_nmc_directory(doc.get("registration_number", ""), doc.get("doctor_name", ""))
        entry = {**doc, **check}
        if entry.get("flagged") or entry.get("status") in ("missing", "invalid_format", "not_found"):
            any_flagged = True
        validated.append(entry)

    if any(d.get("status") == "invalid_format" for d in validated):
        overall = "invalid_format"
        summary = "One or more doctor registration numbers have an invalid format."
    elif any(d.get("registration_number") for d in validated):
        overall = "format_valid_unverified_online"
        summary = (
            "Doctor registration number(s) extracted and format-checked. "
            "Online NMC/state council confirmation requires manual verification."
        )
        any_flagged = True
    else:
        overall = "not_found"
        summary = "Doctor name found but registration number missing."
        any_flagged = True

    return {
        "doctors": validated,
        "overall_status": overall,
        "flagged": any_flagged,
        "summary": summary,
    }
